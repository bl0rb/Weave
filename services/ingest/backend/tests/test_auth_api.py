"""Tests for the Step 2 auth API: setup, local login/logout, /me, the
/auth/admin/* management surface, origin_guard (CSRF), and the OIDC
authorize/callback dance.

Uses the same TestClient/DB wiring as test_api.py (see conftest.py), but
constructs its own fresh `TestClient(app)` per test (see `client` fixture
below) rather than the shared module-level client, so each test gets an
isolated cookie jar -- session/OIDC-state cookies from one test must never
leak into another.
"""

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import KeySet, RSAKey
from sqlalchemy import select

import app.api.auth as auth_module
from app.core.config import settings
from app.main import app
from app.models.models import (
    AuthProvider,
    Job,
    JobStatus,
    LoginHandoffCode,
    Session as SessionModel,
    Team,
    User,
    UserRole,
    WorkerLogEntry,
)
from app.services.security import encrypt_client_secret, hash_password, rate_limiter
from conftest import BROWSER_HEADERS, TestingSessionLocal


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    # /setup, /login, and the OIDC endpoints all call enforce_rate_limit,
    # which is backed by a single shared Redis counter keyed by client host
    # (TestClient always presents as "testclient"). Reset it before every
    # test in this module so test order/volume here can never trip a false
    # 429 for a later test, and so it doesn't inherit noise from test_api.py
    # if these modules run in the same process.
    rate_limiter.reset()
    yield


@pytest.fixture
def client() -> TestClient:
    """A fresh cookie jar per test. `app.dependency_overrides[get_db]` is
    already wired up once, process-wide, in conftest.py."""
    return TestClient(app, headers=BROWSER_HEADERS)


def _db():
    return TestingSessionLocal()


def _wipe_users_and_sessions() -> None:
    db = _db()
    try:
        db.query(SessionModel).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()


def _wipe_worker_logs() -> None:
    # Local/OIDC logins now write worker_log_entries rows (see
    # _log_auth_event in app/api/auth.py); tests that assert exact counts
    # against this table need a clean slate, since it isn't reset between
    # tests the way users/sessions are above.
    db = _db()
    try:
        db.query(WorkerLogEntry).delete()
        db.commit()
    finally:
        db.close()


def _create_user(
    *,
    username: str,
    email: str,
    password: str | None = 'CorrectHorse1',
    role: UserRole = UserRole.USER,
    is_active: bool = True,
    oidc_provider_id: str | None = None,
    oidc_subject: str | None = None,
) -> User:
    db = _db()
    try:
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password) if password else None,
            role=role,
            is_active=is_active,
            oidc_provider_id=oidc_provider_id,
            oidc_subject=oidc_subject,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()


def _login(client: TestClient, identifier: str, password: str):
    return client.post('/api/v1/auth/login', json={'identifier': identifier, 'password': password})


# --- setup --------------------------------------------------------------------

def test_setup_status_and_setup_flow_locks_after_first_admin(client: TestClient) -> None:
    _wipe_users_and_sessions()

    status_resp = client.get('/api/v1/auth/setup-status')
    assert status_resp.status_code == 200
    assert status_resp.json() == {'needs_setup': True}

    setup_resp = client.post(
        '/api/v1/auth/setup',
        json={'username': 'RootAdmin', 'email': 'root@example.com', 'password': 'SetupPassw0rd'},
    )
    assert setup_resp.status_code == 200
    body = setup_resp.json()
    assert body['username'] == 'rootadmin'  # stored lowercased
    assert body['role'] == 'admin'
    assert 'weave_ingest_session' in client.cookies

    me_resp = client.get('/api/v1/auth/me')
    assert me_resp.status_code == 200
    assert me_resp.json()['role'] == 'admin'

    second_setup = client.post(
        '/api/v1/auth/setup',
        json={'username': 'someoneelse', 'email': 'someone@example.com', 'password': 'AnotherPassw0rd'},
    )
    assert second_setup.status_code == 409

    final_status = client.get('/api/v1/auth/setup-status')
    assert final_status.json() == {'needs_setup': False}


# --- local login ----------------------------------------------------------------

def test_login_success_sets_session_and_me_works(client: TestClient) -> None:
    _create_user(username='loginuser', email='loginuser@example.com', password='CorrectHorse1')

    resp = _login(client, 'loginuser', 'CorrectHorse1')
    assert resp.status_code == 200
    assert resp.json()['username'] == 'loginuser'
    assert 'weave_ingest_session' in client.cookies

    me_resp = client.get('/api/v1/auth/me')
    assert me_resp.status_code == 200
    assert me_resp.json()['email'] == 'loginuser@example.com'


def test_login_by_email_case_insensitive(client: TestClient) -> None:
    _create_user(username='mixedcaseuser', email='MixedCase@Example.com', password='CorrectHorse1')

    resp = _login(client, 'MIXEDCASE@EXAMPLE.COM', 'CorrectHorse1')
    assert resp.status_code == 200


def test_login_wrong_password_is_generic_401(client: TestClient) -> None:
    _create_user(username='wrongpassuser', email='wrongpass@example.com', password='CorrectHorse1')

    resp = _login(client, 'wrongpassuser', 'not-the-password')
    assert resp.status_code == 401
    assert 'weave_ingest_session' not in client.cookies


def test_login_unknown_identifier_is_generic_401(client: TestClient) -> None:
    resp = _login(client, 'nobody-with-this-username', 'whatever12')
    assert resp.status_code == 401


def test_login_inactive_user_rejected(client: TestClient) -> None:
    _create_user(username='inactiveuser', email='inactive@example.com', password='CorrectHorse1', is_active=False)

    resp = _login(client, 'inactiveuser', 'CorrectHorse1')
    assert resp.status_code == 401


def test_login_oidc_only_account_rejected(client: TestClient) -> None:
    # password=None => OIDC-only account, no local password login possible.
    _create_user(username='oidconlyuser', email='oidconly@example.com', password=None)

    resp = _login(client, 'oidconlyuser', 'anything12')
    assert resp.status_code == 401


def test_setup_writes_worker_log_entry(client: TestClient) -> None:
    _wipe_users_and_sessions()
    _wipe_worker_logs()

    resp = client.post(
        '/api/v1/auth/setup',
        json={'username': 'FirstAdmin', 'email': 'first@example.com', 'password': 'SetupPassw0rd'},
    )
    assert resp.status_code == 200

    db = _db()
    try:
        rows = db.scalars(select(WorkerLogEntry).where(WorkerLogEntry.logger_name == 'app.auth')).all()
        assert len(rows) == 1
        assert rows[0].level == 'INFO'
        # the stored (lowercased) username, matching what the account got
        assert rows[0].message == 'setup completed: first admin firstadmin created'
    finally:
        db.close()


def test_login_failure_writes_worker_log_entry(client: TestClient) -> None:
    _wipe_worker_logs()
    _create_user(username='logfailuser', email='logfail@example.com', password='CorrectHorse1')

    resp = _login(client, 'logfailuser', 'wrong-password')
    assert resp.status_code == 401

    db = _db()
    try:
        rows = db.scalars(select(WorkerLogEntry).where(WorkerLogEntry.logger_name == 'app.auth')).all()
        assert len(rows) == 1
        assert rows[0].level == 'WARNING'
        assert rows[0].message == 'failed sign-in for identifier logfailuser'
    finally:
        db.close()


# --- logout / me ------------------------------------------------------------------

def test_logout_revokes_session(client: TestClient) -> None:
    _create_user(username='logoutuser', email='logoutuser@example.com', password='CorrectHorse1')
    _login(client, 'logoutuser', 'CorrectHorse1')
    assert client.get('/api/v1/auth/me').status_code == 200

    logout_resp = client.post('/api/v1/auth/logout')
    assert logout_resp.status_code == 200

    # The cookie the client still has on file is now invalid server-side --
    # the session row backing it was deleted, not merely expired locally.
    me_resp = client.get('/api/v1/auth/me')
    assert me_resp.status_code == 401


def test_me_requires_authentication(client: TestClient) -> None:
    resp = client.get('/api/v1/auth/me')
    assert resp.status_code == 401


def test_protected_business_route_requires_authentication(client: TestClient) -> None:
    # The global secure-by-default gate applies to the pre-existing
    # job/folder router too, not just /auth/*.
    resp = client.get('/api/v1/jobs')
    assert resp.status_code == 401


# --- admin CRUD authz -------------------------------------------------------------

def test_admin_users_endpoint_requires_admin_role(client: TestClient) -> None:
    _create_user(username='plainuser', email='plainuser@example.com', password='CorrectHorse1', role=UserRole.USER)
    _login(client, 'plainuser', 'CorrectHorse1')

    resp = client.get('/api/v1/auth/admin/users')
    assert resp.status_code == 403


def test_admin_users_endpoint_works_for_admin(client: TestClient) -> None:
    _create_user(username='workingadmin', email='workingadmin@example.com', password='CorrectHorse1', role=UserRole.ADMIN)
    _login(client, 'workingadmin', 'CorrectHorse1')

    resp = client.get('/api/v1/auth/admin/users')
    assert resp.status_code == 200
    usernames = [item['username'] for item in resp.json()['items']]
    assert 'workingadmin' in usernames


def test_admin_can_create_team_and_assign_user(client: TestClient) -> None:
    _create_user(username='teamadmin', email='teamadmin@example.com', password='CorrectHorse1', role=UserRole.ADMIN)
    target = _create_user(username='teammember', email='teammember@example.com', password='CorrectHorse1')
    _login(client, 'teamadmin', 'CorrectHorse1')

    team_resp = client.post('/api/v1/auth/admin/teams', json={'name': 'Finance Team'})
    assert team_resp.status_code == 201
    team_id = team_resp.json()['id']

    update_resp = client.patch(f'/api/v1/auth/admin/users/{target.id}', json={'team_id': team_id})
    assert update_resp.status_code == 200
    assert update_resp.json()['team_id'] == team_id


def test_admin_cannot_demote_or_delete_last_active_admin(client: TestClient) -> None:
    _wipe_users_and_sessions()
    sole_admin = _create_user(username='soleadmin', email='soleadmin@example.com', password='CorrectHorse1', role=UserRole.ADMIN)
    _login(client, 'soleadmin', 'CorrectHorse1')

    demote_resp = client.patch(f'/api/v1/auth/admin/users/{sole_admin.id}', json={'role': 'user'})
    assert demote_resp.status_code == 409

    deactivate_resp = client.patch(f'/api/v1/auth/admin/users/{sole_admin.id}', json={'is_active': False})
    assert deactivate_resp.status_code == 409

    delete_resp = client.delete(f'/api/v1/auth/admin/users/{sole_admin.id}')
    assert delete_resp.status_code == 409

    # A second active admin makes the demotion/deletion legal again.
    _create_user(username='secondadmin', email='secondadmin@example.com', password='CorrectHorse1', role=UserRole.ADMIN)
    demote_resp_ok = client.patch(f'/api/v1/auth/admin/users/{sole_admin.id}', json={'role': 'user'})
    assert demote_resp_ok.status_code == 200


def test_admin_providers_list_never_exposes_secret(client: TestClient) -> None:
    admin = _create_user(username='provideradmin', email='provideradmin@example.com', password='CorrectHorse1', role=UserRole.ADMIN)
    db = _db()
    try:
        provider = AuthProvider(
            slug='keycloak-secret-test',
            display_name='Keycloak',
            issuer_url='https://idp.example.com',
            client_id='client123',
            client_secret_encrypted=encrypt_client_secret('super-secret-value'),
            enabled=True,
        )
        db.add(provider)
        db.commit()
    finally:
        db.close()
    _login(client, 'provideradmin', 'CorrectHorse1')

    resp = client.get('/api/v1/auth/admin/providers')
    assert resp.status_code == 200
    raw_body = resp.text
    assert 'super-secret-value' not in raw_body
    matching = [item for item in resp.json()['items'] if item['slug'] == 'keycloak-secret-test']
    assert matching and matching[0]['client_secret_set'] is True
    assert 'client_secret' not in matching[0]
    assert 'client_secret_encrypted' not in matching[0]
    del admin  # only needed to create the session above


def test_public_providers_endpoint_hides_disabled_and_secrets(client: TestClient) -> None:
    db = _db()
    try:
        db.add(
            AuthProvider(
                slug='public-enabled-provider',
                display_name='Enabled IdP',
                issuer_url='https://idp.example.com',
                client_id='cid',
                client_secret_encrypted=encrypt_client_secret('shh'),
                enabled=True,
            )
        )
        db.add(
            AuthProvider(
                slug='public-disabled-provider',
                display_name='Disabled IdP',
                issuer_url='https://idp2.example.com',
                client_id='cid2',
                client_secret_encrypted=encrypt_client_secret('shh2'),
                enabled=False,
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.get('/api/v1/auth/providers')
    assert resp.status_code == 200
    slugs = {item['slug'] for item in resp.json()['items']}
    assert 'public-enabled-provider' in slugs
    assert 'public-disabled-provider' not in slugs
    assert 'shh' not in resp.text


def test_admin_claim_ownerless_jobs(client: TestClient) -> None:
    admin = _create_user(username='claimadmin', email='claimadmin@example.com', password='CorrectHorse1', role=UserRole.ADMIN)
    db = _db()
    try:
        db.add(
            Job(
                id='ownerless-job-1',
                original_filename='legacy.pdf',
                upload_path='/tmp/legacy.pdf',
                status=JobStatus.FINISHED,
                owner_id=None,
            )
        )
        db.commit()
    finally:
        db.close()
    _login(client, 'claimadmin', 'CorrectHorse1')

    resp = client.post('/api/v1/auth/admin/jobs/claim-ownerless', json={'owner_id': admin.id})
    assert resp.status_code == 200
    assert resp.json()['claimed'] >= 1

    db = _db()
    try:
        job = db.get(Job, 'ownerless-job-1')
        assert job.owner_id == admin.id
    finally:
        db.close()


# --- origin_guard (CSRF) -----------------------------------------------------------

def test_origin_guard_rejects_foreign_origin_on_public_login(client: TestClient) -> None:
    resp = client.post(
        '/api/v1/auth/login',
        json={'identifier': 'whoever', 'password': 'whatever12'},
        headers={'Origin': 'https://evil.example'},
    )
    assert resp.status_code == 403


def test_origin_guard_allows_configured_origin(client: TestClient) -> None:
    # Passes origin_guard and reaches the real handler -- proven by getting
    # a 401 (bad credentials) rather than a 403 (blocked origin).
    resp = client.post(
        '/api/v1/auth/login',
        json={'identifier': 'whoever', 'password': 'whatever12'},
        headers={'Origin': 'http://localhost:3000'},
    )
    assert resp.status_code == 401


def test_origin_guard_ignores_get_requests(client: TestClient) -> None:
    resp = client.get('/api/v1/auth/setup-status', headers={'Origin': 'https://evil.example'})
    assert resp.status_code == 200


# --- OIDC ---------------------------------------------------------------------------

def _rsa_keypair_and_jwks():
    key = RSAKey.generate_key(2048, parameters={'kid': 'test-kid'})
    jwks = {'keys': [key.as_dict(private=False)]}
    return key, KeySet.import_key_set(jwks)


def _sign_id_token(key: RSAKey, **claim_overrides) -> str:
    now = int(time.time())
    claims = {
        'iss': 'https://idp.example.com',
        'aud': 'test-client-id',
        'sub': 'idp-subject-1',
        'exp': now + 300,
        'iat': now,
        'email': 'newoidcuser@example.com',
        'preferred_username': 'newoidcuser',
    }
    claims.update(claim_overrides)
    return joserfc_jwt.encode({'alg': 'RS256', 'kid': 'test-kid'}, claims, key)


def _make_oidc_provider(slug: str = 'test-oidc', *, use_email_as_username: bool = False) -> AuthProvider:
    db = _db()
    try:
        provider = AuthProvider(
            slug=slug,
            display_name='Test OIDC',
            issuer_url='https://idp.example.com',
            client_id='test-client-id',
            client_secret_encrypted=encrypt_client_secret('idp-client-secret'),
            enabled=True,
            use_email_as_username=use_email_as_username,
        )
        db.add(provider)
        db.commit()
        db.refresh(provider)
        db.expunge(provider)
        return provider
    finally:
        db.close()


def _oidc_login(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    slug: str,
    *,
    discovery_extra: dict | None = None,
    userinfo_response: dict | None = None,
    userinfo_raises: bool = False,
    **claim_overrides,
):
    """Drives the full authorize -> callback dance for `slug` and returns the
    callback response. `claim_overrides` go straight into the signed ID
    token (see `_sign_id_token`) -- e.g. sub=..., email=..., preferred_username=...

    `discovery_extra` merges into `_DISCOVERY_DOCUMENT` (e.g. to add a
    `userinfo_endpoint`). `userinfo_response`/`userinfo_raises` stub out
    `fetch_userinfo` for the userinfo-fallback tests; by default neither is
    set, so a callback that doesn't need userinfo never calls it.
    """
    signing_key, key_set = _rsa_keypair_and_jwks()
    monkeypatch.setattr(auth_module, 'generate_token', lambda length=30: 'fixed-oidc-test-token')
    discovery = {**_DISCOVERY_DOCUMENT, **(discovery_extra or {})}
    monkeypatch.setattr(auth_module, 'get_discovery_document', lambda issuer_url: discovery)

    if userinfo_response is not None:
        monkeypatch.setattr(auth_module, 'fetch_userinfo', lambda endpoint, access_token: userinfo_response)
    elif userinfo_raises:
        def _raise_userinfo_error(endpoint, access_token):
            raise auth_module.OIDCError('userinfo endpoint unreachable')

        monkeypatch.setattr(auth_module, 'fetch_userinfo', _raise_userinfo_error)

    authorize_resp = client.get(f'/api/v1/auth/oidc/{slug}/authorize', follow_redirects=False)
    assert authorize_resp.status_code == 302

    id_token = _sign_id_token(signing_key, nonce='fixed-oidc-test-token', **claim_overrides)
    monkeypatch.setattr(
        auth_module, 'exchange_code_for_tokens', lambda token_endpoint, **kw: {'id_token': id_token, 'access_token': 'at'}
    )
    monkeypatch.setattr(auth_module, 'fetch_jwks', lambda jwks_uri: key_set)

    return client.get(
        f'/api/v1/auth/oidc/{slug}/callback',
        params={'code': 'auth-code-123', 'state': 'fixed-oidc-test-token'},
        follow_redirects=False,
    )


_DISCOVERY_DOCUMENT = {
    'issuer': 'https://idp.example.com',
    'authorization_endpoint': 'https://idp.example.com/auth',
    'token_endpoint': 'https://idp.example.com/token',
    'jwks_uri': 'https://idp.example.com/jwks',
}


def test_oidc_callback_happy_path_creates_user_and_logs_in(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_oidc_provider('test-oidc-happy-path')
    signing_key, key_set = _rsa_keypair_and_jwks()

    # Fixed state/nonce/code_verifier so the test can pre-compute a signed
    # ID token whose `nonce` claim will match what /authorize embeds in the
    # signed state cookie.
    monkeypatch.setattr(auth_module, 'generate_token', lambda length=30: 'fixed-oidc-test-token')
    monkeypatch.setattr(auth_module, 'get_discovery_document', lambda issuer_url: _DISCOVERY_DOCUMENT)

    authorize_resp = client.get('/api/v1/auth/oidc/test-oidc-happy-path/authorize', follow_redirects=False)
    assert authorize_resp.status_code == 302
    location = authorize_resp.headers['location']
    assert location.startswith('https://idp.example.com/auth?')
    assert 'weave_ingest_oidc_state' in client.cookies

    id_token = _sign_id_token(signing_key, nonce='fixed-oidc-test-token')
    monkeypatch.setattr(
        auth_module, 'exchange_code_for_tokens', lambda token_endpoint, **kw: {'id_token': id_token, 'access_token': 'at'}
    )
    monkeypatch.setattr(auth_module, 'fetch_jwks', lambda jwks_uri: key_set)

    callback_resp = client.get(
        '/api/v1/auth/oidc/test-oidc-happy-path/callback',
        params={'code': 'auth-code-123', 'state': 'fixed-oidc-test-token'},
        follow_redirects=False,
    )
    assert callback_resp.status_code == 302
    assert 'weave_ingest_session' in client.cookies

    me_resp = client.get('/api/v1/auth/me')
    assert me_resp.status_code == 200
    me_body = me_resp.json()
    assert me_body['email'] == 'newoidcuser@example.com'
    assert me_body['username'] == 'newoidcuser'

    db = _db()
    try:
        created = db.scalar(select(User).where(User.oidc_subject == 'idp-subject-1'))
        assert created is not None
        assert created.password_hash is None
        assert created.role == UserRole.USER
    finally:
        db.close()


def test_oidc_callback_rejects_idp_initiated_login(client: TestClient) -> None:
    _make_oidc_provider()
    # No prior call to /authorize -- no OIDC state cookie is present, which
    # is exactly the IdP-initiated pattern this must fail closed on.
    resp = client.get(
        '/api/v1/auth/oidc/test-oidc/callback',
        params={'code': 'whatever', 'state': 'whatever'},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_oidc_authorize_unknown_provider_404(client: TestClient) -> None:
    resp = client.get('/api/v1/auth/oidc/does-not-exist/authorize', follow_redirects=False)
    assert resp.status_code == 404


# --- OIDC: use_email_as_username -----------------------------------------------------

def test_oidc_callback_use_email_as_username_provisions_with_email(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switch on + a real email claim: the new user's username is the full
    email address, not the (UPN-shaped) preferred_username."""
    _make_oidc_provider('test-oidc-email-username', use_email_as_username=True)
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-email-username',
        sub='idp-subject-email-username',
        email='entrauser@example.com',
        preferred_username='entrauser-upn-garbage',
    )
    assert resp.status_code == 302

    db = _db()
    try:
        created = db.scalar(select(User).where(User.oidc_subject == 'idp-subject-email-username'))
        assert created is not None
        assert created.email == 'entrauser@example.com'
        assert created.username == 'entrauser@example.com'
    finally:
        db.close()


def test_oidc_callback_use_email_as_username_off_keeps_preferred_username(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: switch off (the default) keeps the pre-existing
    preferred_username-based derivation untouched."""
    _make_oidc_provider('test-oidc-username-off', use_email_as_username=False)
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-username-off',
        sub='idp-subject-username-off',
        email='someone@example.com',
        preferred_username='someoneupn',
    )
    assert resp.status_code == 302

    db = _db()
    try:
        created = db.scalar(select(User).where(User.oidc_subject == 'idp-subject-username-off'))
        assert created is not None
        assert created.username == 'someoneupn'
    finally:
        db.close()


def test_oidc_callback_use_email_as_username_renames_existing_user_on_login(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switch on: an existing user's username is nudged to their current
    email claim on login, not just at first provisioning."""
    provider = _make_oidc_provider('test-oidc-rename', use_email_as_username=True)
    _create_user(
        username='old-upn-username',
        email='renameuser@example.com',
        password=None,
        oidc_provider_id=provider.id,
        oidc_subject='idp-subject-rename',
    )
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-rename',
        sub='idp-subject-rename',
        email='renameuser@example.com',
        preferred_username='old-upn-username',
    )
    assert resp.status_code == 302

    db = _db()
    try:
        user = db.scalar(select(User).where(User.oidc_subject == 'idp-subject-rename'))
        assert user is not None
        assert user.username == 'renameuser@example.com'
    finally:
        db.close()


def test_oidc_callback_use_email_as_username_skips_rename_on_collision(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different, unrelated user already owns the email-shaped username:
    the rename must be skipped silently and login must still succeed."""
    provider = _make_oidc_provider('test-oidc-collision', use_email_as_username=True)
    other_user = _create_user(username='collideuser@example.com', email='someoneelse@example.com', password=None)
    renaming_user = _create_user(
        username='old-username',
        email='collideuser@example.com',
        password=None,
        oidc_provider_id=provider.id,
        oidc_subject='idp-subject-collision',
    )
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-collision',
        sub='idp-subject-collision',
        email='collideuser@example.com',
        preferred_username='old-username',
    )
    assert resp.status_code == 302
    assert 'weave_ingest_session' in client.cookies

    db = _db()
    try:
        renamed = db.get(User, renaming_user.id)
        assert renamed is not None
        assert renamed.username == 'old-username'
        unaffected = db.get(User, other_user.id)
        assert unaffected is not None
        assert unaffected.username == 'collideuser@example.com'
    finally:
        db.close()


def test_oidc_callback_use_email_as_username_ignores_synthetic_fallback_email_on_provisioning(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `email` claim at all: provisioning still synthesizes
    `sub@slug.oidc.invalid` as the user's *email* (existing behavior), but
    must never use that synthetic address as the username -- even with the
    switch on it falls back to preferred_username, same as the switch-off
    case."""
    _make_oidc_provider('test-oidc-synthetic-email', use_email_as_username=True)
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-synthetic-email',
        sub='idp-subject-synthetic-email',
        email=None,
        preferred_username='realupnuser',
    )
    assert resp.status_code == 302

    db = _db()
    try:
        created = db.scalar(select(User).where(User.oidc_subject == 'idp-subject-synthetic-email'))
        assert created is not None
        assert created.email == 'idp-subject-synthetic-email@test-oidc-synthetic-email.oidc.invalid'
        assert created.username == 'realupnuser'
    finally:
        db.close()


def test_oidc_callback_use_email_as_username_no_email_claim_skips_rename_on_login(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing user, switch on, but this login's ID token carries no email
    claim: must not rename onto a synthesized address, must not rename at
    all."""
    provider = _make_oidc_provider('test-oidc-no-email-claim', use_email_as_username=True)
    _create_user(
        username='kept-username',
        email='keptuser@example.com',
        password=None,
        oidc_provider_id=provider.id,
        oidc_subject='idp-subject-no-email-claim',
    )
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-no-email-claim',
        sub='idp-subject-no-email-claim',
        email=None,
        preferred_username='old-username',
    )
    assert resp.status_code == 302

    db = _db()
    try:
        user = db.scalar(select(User).where(User.oidc_subject == 'idp-subject-no-email-claim'))
        assert user is not None
        assert user.username == 'kept-username'
    finally:
        db.close()


# --- OIDC: claim resolution (upn/unique_name/userinfo fallback) ----------------------

def test_oidc_callback_upn_only_token_resolves_email_from_upn(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token carries neither `email` nor `preferred_username` (the observed
    Entra v1.0 shape) but does carry `upn`: the real email must come from
    `upn`, never the synthetic sub@...oidc.invalid fallback."""
    _make_oidc_provider('test-oidc-upn-only')
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-upn-only',
        sub='idp-subject-upn-only',
        email=None,
        preferred_username=None,
        upn='realupn@example.com',
    )
    assert resp.status_code == 302

    db = _db()
    try:
        created = db.scalar(select(User).where(User.oidc_subject == 'idp-subject-upn-only'))
        assert created is not None
        assert created.email == 'realupn@example.com'
        assert not created.email.endswith('.oidc.invalid')
    finally:
        db.close()


def test_oidc_callback_userinfo_fallback_supplies_email(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token has no usable email/preferred_username/upn/unique_name at all,
    but the discovery document has a userinfo_endpoint and the token
    response an access_token: userinfo's email is used."""
    _make_oidc_provider('test-oidc-userinfo-email')
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-userinfo-email',
        sub='idp-subject-userinfo-email',
        email=None,
        preferred_username=None,
        discovery_extra={'userinfo_endpoint': 'https://idp.example.com/userinfo'},
        userinfo_response={'email': 'fromuserinfo@example.com'},
    )
    assert resp.status_code == 302

    db = _db()
    try:
        created = db.scalar(select(User).where(User.oidc_subject == 'idp-subject-userinfo-email'))
        assert created is not None
        assert created.email == 'fromuserinfo@example.com'
    finally:
        db.close()


def test_oidc_callback_userinfo_failure_does_not_block_login(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Userinfo is best-effort: if it raises, login still succeeds, falling
    back to the synthetic sub@slug.oidc.invalid address like before."""
    _make_oidc_provider('test-oidc-userinfo-error')
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-userinfo-error',
        sub='idp-subject-userinfo-error',
        email=None,
        preferred_username=None,
        discovery_extra={'userinfo_endpoint': 'https://idp.example.com/userinfo'},
        userinfo_raises=True,
    )
    assert resp.status_code == 302
    assert 'weave_ingest_session' in client.cookies

    db = _db()
    try:
        created = db.scalar(select(User).where(User.oidc_subject == 'idp-subject-userinfo-error'))
        assert created is not None
        assert created.email == 'idp-subject-userinfo-error@test-oidc-userinfo-error.oidc.invalid'
    finally:
        db.close()


def test_oidc_callback_self_heals_synthetic_email_on_existing_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the production incident: an account previously
    provisioned with the sub@slug.oidc.invalid fallback (because the ID
    token had no usable claim at the time) now resolves a real email on
    login -- the stored email must be healed in place, still matched
    purely by (provider, sub)."""
    provider = _make_oidc_provider('test-oidc-selfheal')
    garbage_user = _create_user(
        username='eauonfycuondabsxiu4prgku5vgz2rcllhsqecxi5wk',
        email='idp-subject-selfheal@test-oidc-selfheal.oidc.invalid',
        password=None,
        oidc_provider_id=provider.id,
        oidc_subject='idp-subject-selfheal',
    )
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-selfheal',
        sub='idp-subject-selfheal',
        email='realuser@example.com',
        preferred_username='realuser',
    )
    assert resp.status_code == 302

    db = _db()
    try:
        healed = db.get(User, garbage_user.id)
        assert healed is not None
        assert healed.email == 'realuser@example.com'
        assert healed.oidc_provider_id == provider.id
        assert healed.oidc_subject == 'idp-subject-selfheal'
    finally:
        db.close()


def test_oidc_callback_self_heal_skips_on_email_collision(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolved email is already owned by a different user: the
    self-heal must be skipped (no email update), and login must still
    succeed rather than fail over a cosmetic data-refresh conflict."""
    provider = _make_oidc_provider('test-oidc-selfheal-collision')
    _create_user(username='otherowner', email='taken@example.com', password='CorrectHorse1')
    garbage_user = _create_user(
        username='sub-derived-garbage-name',
        email='idp-subject-selfheal-collision@test-oidc-selfheal-collision.oidc.invalid',
        password=None,
        oidc_provider_id=provider.id,
        oidc_subject='idp-subject-selfheal-collision',
    )
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-selfheal-collision',
        sub='idp-subject-selfheal-collision',
        email='taken@example.com',
        preferred_username='someupn',
    )
    assert resp.status_code == 302
    assert 'weave_ingest_session' in client.cookies

    db = _db()
    try:
        unchanged = db.get(User, garbage_user.id)
        assert unchanged is not None
        assert unchanged.email == 'idp-subject-selfheal-collision@test-oidc-selfheal-collision.oidc.invalid'
    finally:
        db.close()


def test_oidc_callback_logs_claim_diagnostic_on_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful OIDC login writes a worker_log_entries row (logger_name
    'app.auth') carrying the provider, the user, and which claims the ID
    token actually had -- the diagnostic that would have immediately
    explained the sub-as-username production incident."""
    _wipe_worker_logs()
    _make_oidc_provider('test-oidc-diagnostic-log')
    resp = _oidc_login(
        client,
        monkeypatch,
        'test-oidc-diagnostic-log',
        sub='idp-subject-diagnostic-log',
        email='diagnostic@example.com',
        preferred_username='diagnosticuser',
    )
    assert resp.status_code == 302

    db = _db()
    try:
        rows = db.scalars(
            select(WorkerLogEntry).where(WorkerLogEntry.logger_name == 'app.auth').order_by(WorkerLogEntry.created_at)
        ).all()
        success_rows = [r for r in rows if 'oidc login succeeded' in r.message]
        assert len(success_rows) == 1
        message = success_rows[0].message
        assert success_rows[0].level == 'INFO'
        assert 'provider=test-oidc-diagnostic-log' in message
        assert 'user=diagnosticuser' in message
        assert 'id_token_claims=' in message
        assert 'email' in message and 'preferred_username' in message
        assert 'userinfo_fetched=False' in message

        provisioning_rows = [r for r in rows if r.message.startswith('created user')]
        assert len(provisioning_rows) == 1
        assert 'created user diagnosticuser (provider test-oidc-diagnostic-log)' == provisioning_rows[0].message
    finally:
        db.close()


# --- admin: worker logs -------------------------------------------------------

def test_admin_worker_logs_endpoint_requires_admin_role(client: TestClient) -> None:
    _create_user(username='plainuser2', email='plainuser2@example.com', password='CorrectHorse1', role=UserRole.USER)
    _login(client, 'plainuser2', 'CorrectHorse1')

    resp = client.get('/api/v1/auth/admin/worker-logs')
    assert resp.status_code == 403


def test_admin_worker_logs_filters_by_level_floor_and_worker(client: TestClient) -> None:
    _create_user(username='logsadmin', email='logsadmin@example.com', password='CorrectHorse1', role=UserRole.ADMIN)
    _login(client, 'logsadmin', 'CorrectHorse1')
    # The login above itself writes an app.auth row (see _log_auth_event) --
    # wipe it and start from a clean slate so the counts below are exact.
    _wipe_worker_logs()
    db = _db()
    try:
        db.add_all([
            WorkerLogEntry(
                level='INFO', logger_name='app.workers.tasks', worker_name='worker-a',
                message='Task process_job[1] succeeded',
            ),
            WorkerLogEntry(
                level='WARNING', logger_name='app.workers.tasks', worker_name='worker-a',
                message='Stopping redelivered job',
            ),
            WorkerLogEntry(
                level='ERROR', logger_name='billiard.pool', worker_name='worker-b',
                message="Process 'ForkPoolWorker-1' pid:47 exited with 'signal 9 (SIGKILL)'",
            ),
        ])
        db.commit()
    finally:
        db.close()

    resp = client.get('/api/v1/auth/admin/worker-logs')
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 3
    assert len(body['items']) == 3
    # newest first
    assert body['items'][0]['message'].startswith("Process 'ForkPoolWorker-1'")

    resp = client.get('/api/v1/auth/admin/worker-logs', params={'level': 'WARNING'})
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 2
    assert {item['level'] for item in body['items']} == {'WARNING', 'ERROR'}

    resp = client.get('/api/v1/auth/admin/worker-logs', params={'worker': 'worker-b'})
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 1
    assert body['items'][0]['worker_name'] == 'worker-b'

    resp = client.get('/api/v1/auth/admin/worker-logs', params={'q': 'SIGKILL'})
    assert resp.status_code == 200
    assert resp.json()['total'] == 1


# --- cross-service login handoff ----------------------------------------------
#
# The chain the chat UI logs in through: a session HERE (however it was
# obtained) becomes a one-time code, and Weave-API redeems that code for the
# identity behind it. See app/api/auth.py's own section comment.

HANDOFF_CALLBACK = 'http://weave-api.test/v1/auth/ingest/callback'
HANDOFF_SECRET = 'shared-handoff-secret'


@pytest.fixture()
def handoff_enabled(monkeypatch):
    monkeypatch.setattr(settings, 'handoff_callback_url', HANDOFF_CALLBACK)
    monkeypatch.setattr(settings, 'handoff_secret', HANDOFF_SECRET)
    return None


def _code_from_redirect(response) -> str:
    assert response.status_code == 302, response.text
    location = response.headers['location']
    assert location.startswith(HANDOFF_CALLBACK)
    return parse_qs(urlsplit(location).query)['code'][0]


def _start_handoff(client: TestClient) -> str:
    return _code_from_redirect(
        client.get('/api/v1/auth/handoff/start', follow_redirects=False)
    )


def _exchange(client: TestClient, code: str, *, secret: str | None = HANDOFF_SECRET):
    headers = {} if secret is None else {'X-Weave-Handoff-Secret': secret}
    return client.post('/api/v1/auth/handoff/exchange', json={'code': code}, headers=headers)


def test_handoff_start_is_404_while_unconfigured(client: TestClient) -> None:
    _create_user(username='handoffoff', email='handoffoff@example.com', password='CorrectHorse1')
    _login(client, 'handoffoff', 'CorrectHorse1')

    resp = client.get('/api/v1/auth/handoff/start', follow_redirects=False)

    assert resp.status_code == 404


def test_handoff_start_requires_a_session(client: TestClient, handoff_enabled) -> None:
    client.cookies.clear()

    resp = client.get('/api/v1/auth/handoff/start', follow_redirects=False)

    assert resp.status_code == 401


def test_handoff_round_trip_carries_username_email_team_and_admin_bit(
    client: TestClient, handoff_enabled
) -> None:
    db = _db()
    try:
        team = Team(name='rechtsabteilung')
        db.add(team)
        db.commit()
        team_id = team.id
    finally:
        db.close()
    user = _create_user(
        username='handoffuser', email='handoff@example.com', password='CorrectHorse1', role=UserRole.ADMIN
    )
    db = _db()
    try:
        db.query(User).filter(User.id == user.id).update({'team_id': team_id})
        db.commit()
    finally:
        db.close()
    _login(client, 'handoffuser', 'CorrectHorse1')

    resp = _exchange(client, _start_handoff(client))

    assert resp.status_code == 200
    body = resp.json()
    # `subject` is this service's user id -- the one identifier an admin
    # cannot change out from under the consuming service.
    assert body['subject'] == user.id
    assert body['username'] == 'handoffuser'
    assert body['email'] == 'handoff@example.com'
    # the team NAME, because that is what a collection's read_teams matches
    assert body['team'] == 'rechtsabteilung'
    assert body['is_admin'] is True


def test_handoff_reports_no_team_for_a_user_without_one(client: TestClient, handoff_enabled) -> None:
    _create_user(username='teamless', email='teamless@example.com', password='CorrectHorse1')
    _login(client, 'teamless', 'CorrectHorse1')

    body = _exchange(client, _start_handoff(client)).json()

    assert body['team'] is None
    assert body['is_admin'] is False


def test_handoff_code_cannot_be_redeemed_twice(client: TestClient, handoff_enabled) -> None:
    _create_user(username='replayuser', email='replay@example.com', password='CorrectHorse1')
    _login(client, 'replayuser', 'CorrectHorse1')
    code = _start_handoff(client)

    assert _exchange(client, code).status_code == 200
    # The code travels in a URL and will sit in access logs; a second
    # redemption must be worthless.
    assert _exchange(client, code).status_code == 401


def test_handoff_code_expires(client: TestClient, handoff_enabled, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'handoff_code_ttl_seconds', 0)
    _create_user(username='expireduser', email='expired@example.com', password='CorrectHorse1')
    _login(client, 'expireduser', 'CorrectHorse1')

    assert _exchange(client, _start_handoff(client)).status_code == 401


def test_handoff_exchange_rejects_a_wrong_or_missing_secret(client: TestClient, handoff_enabled) -> None:
    _create_user(username='secretuser', email='secret@example.com', password='CorrectHorse1')
    _login(client, 'secretuser', 'CorrectHorse1')
    code = _start_handoff(client)

    assert _exchange(client, code, secret=None).status_code == 401
    assert _exchange(client, code, secret='not-the-secret').status_code == 401
    # ...and the code survived both attempts, so a wrong guess cannot be
    # used to burn somebody else's login.
    assert _exchange(client, code).status_code == 200


def test_handoff_exchange_fails_closed_without_a_configured_secret(
    client: TestClient, handoff_enabled, monkeypatch
) -> None:
    _create_user(username='nosecret', email='nosecret@example.com', password='CorrectHorse1')
    _login(client, 'nosecret', 'CorrectHorse1')
    code = _start_handoff(client)
    monkeypatch.setattr(settings, 'handoff_secret', '')

    # An empty secret would compare equal to every caller's empty header and
    # turn this endpoint into an open identity oracle -- 503, never 200.
    resp = _exchange(client, code, secret=None)

    assert resp.status_code == 503


def test_handoff_exchange_rejects_a_deactivated_user(client: TestClient, handoff_enabled) -> None:
    user = _create_user(username='goneuser', email='gone@example.com', password='CorrectHorse1')
    _login(client, 'goneuser', 'CorrectHorse1')
    code = _start_handoff(client)

    db = _db()
    try:
        db.query(User).filter(User.id == user.id).update({'is_active': False})
        db.commit()
    finally:
        db.close()

    assert _exchange(client, code).status_code == 401


def test_handoff_start_writes_a_worker_log_entry(client: TestClient, handoff_enabled) -> None:
    _wipe_worker_logs()
    _create_user(username='loggeduser', email='logged@example.com', password='CorrectHorse1')
    _login(client, 'loggeduser', 'CorrectHorse1')
    _exchange(client, _start_handoff(client))

    db = _db()
    try:
        messages = [
            row.message
            for row in db.scalars(select(WorkerLogEntry).where(WorkerLogEntry.logger_name == 'app.auth')).all()
        ]
    finally:
        db.close()
    assert 'login handoff started for user loggeduser' in messages
    assert 'login handoff redeemed for user loggeduser' in messages


def test_handoff_echoes_the_callers_state_untouched(client: TestClient, handoff_enabled) -> None:
    # The caller compares this against its own signed cookie; without it a
    # forged callback URL could sign a victim's browser into somebody
    # else's account.
    _create_user(username='stateuser', email='state@example.com', password='CorrectHorse1')
    _login(client, 'stateuser', 'CorrectHorse1')

    resp = client.get(
        '/api/v1/auth/handoff/start', params={'state': 'opaque-state-123'}, follow_redirects=False
    )

    assert resp.status_code == 302
    assert parse_qs(urlsplit(resp.headers['location']).query)['state'] == ['opaque-state-123']


def test_handoff_stores_only_the_hash_of_the_code(client: TestClient, handoff_enabled) -> None:
    _create_user(username='hashuser', email='hash@example.com', password='CorrectHorse1')
    _login(client, 'hashuser', 'CorrectHorse1')
    code = _start_handoff(client)

    db = _db()
    try:
        rows = db.scalars(select(LoginHandoffCode)).all()
        stored = {row.code_hash for row in rows}
    finally:
        db.close()
    assert code not in stored
