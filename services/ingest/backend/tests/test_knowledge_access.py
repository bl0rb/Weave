"""The portal access gate (deps.require_knowledge_access): a browser session of
a non-admin without any explicit knowledge-space grant gets 403 on the portal
routers, /auth/me still answers (with `knowledge_access: false`) so the portal
can say why, and personal API tokens (pd_...) are deliberately not affected.
conftest.py bypasses the gate suite-wide; every test here removes that
override first.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import NO_KNOWLEDGE_ACCESS_DETAIL, require_knowledge_access
from app.main import app
from app.models.models import Collection, CollectionGrant, CollectionRole, CollectionVisibility, Team, User, UserRole
from app.services.security import rate_limiter
from conftest import BROWSER_HEADERS, TestingSessionLocal, create_test_user, login_as


@pytest.fixture(autouse=True)
def _real_gate():
    app.dependency_overrides.pop(require_knowledge_access, None)
    rate_limiter.reset()
    yield


def _user(role: UserRole = UserRole.USER, team_id: str | None = None):
    name = f'ka-{uuid.uuid4().hex[:10]}'
    create_test_user(username=name, email=f'{name}@example.com', role=role, team_id=team_id)
    return name


def _collection(*, visibility=CollectionVisibility.RESTRICTED, grants=()) -> None:
    with TestingSessionLocal() as db:
        collection = Collection(slug=f'ka-{uuid.uuid4().hex[:10]}', name='Knowledge', visibility=visibility)
        collection.grants.extend(grants)
        db.add(collection)
        db.commit()


def test_user_without_grant_is_turned_away_but_can_see_why():
    # A public space alone does not count as access.
    _collection(visibility=CollectionVisibility.PUBLIC)
    client = login_as(_user())

    jobs = client.get('/api/v1/jobs')
    assert jobs.status_code == 403
    assert jobs.json() == {'detail': NO_KNOWLEDGE_ACCESS_DETAIL}

    me = client.get('/api/v1/auth/me')
    assert me.status_code == 200
    assert me.json()['knowledge_access'] is False


def test_user_with_person_grant_passes():
    name = _user()
    with TestingSessionLocal() as db:
        user_id = db.query(User).filter(User.username == name).one().id
    _collection(grants=[CollectionGrant(user_id=user_id, role=CollectionRole.READER)])
    client = login_as(name)

    assert client.get('/api/v1/jobs').status_code == 200
    assert client.get('/api/v1/auth/me').json()['knowledge_access'] is True


def test_user_with_team_grant_passes():
    with TestingSessionLocal() as db:
        team = Team(name=f'ka-team-{uuid.uuid4().hex[:8]}')
        db.add(team)
        db.commit()
        team_id = team.id
    _collection(grants=[CollectionGrant(team_id=team_id, role=CollectionRole.READER)])
    client = login_as(_user(team_id=team_id))

    assert client.get('/api/v1/jobs').status_code == 200


def test_admin_passes_without_grant():
    client = login_as(_user(role=UserRole.ADMIN))

    assert client.get('/api/v1/jobs').status_code == 200
    assert client.get('/api/v1/auth/me').json()['knowledge_access'] is True


def test_personal_api_token_is_not_gated():
    client = login_as(_user())
    token = client.post('/api/v1/auth/tokens', json={'name': 'script'}).json()['token']

    bare = TestClient(app, headers=BROWSER_HEADERS)
    assert bare.get('/api/v1/jobs', headers={'Authorization': f'Bearer {token}'}).status_code == 200
