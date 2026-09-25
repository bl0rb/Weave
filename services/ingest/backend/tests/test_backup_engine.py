"""Tests for the disaster-recovery export/import engine (app/services/backup.py).

Round-trips a rich source dataset into a SECOND, independent SQLite database
under a DIFFERENT SECRET_KEY and a different uploads_dir/results_dir, the
way a real "fresh install, then restore" disaster-recovery flow works.

Both the source and target databases here are dedicated per-test SQLite
files (via `_new_engine`), deliberately NOT the shared conftest.py
`TestingSessionLocal`/test.db -- that database persists across every test in
the whole backend suite, and this module needs a source dataset it fully
controls (exact row counts, no cross-test accumulation) to assert against.
"""

import hashlib
import json
import tarfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.core.config as config_module
from app.models.models import (
    AuthProvider,
    Base,
    Collection,
    CollectionVisibility,
    DocumentRelease,
    ImportAuthType,
    ImportSource,
    Job,
    JobArtifact,
    JobStatus,
    KnowledgeWithdrawal,
    ManagedBot,
    Session as SessionModel,
    Team,
    User,
    UserRole,
    WebhookConnection,
)
from app.services import backup
from app.services.security import (
    encrypt_client_secret,
    encrypt_import_credential,
    encrypt_managed_bot_auth_token,
    encrypt_webhook_secret,
    hash_password,
    hash_session_token,
)


def _new_engine(tmp_path: Path, name: str):
    engine = create_engine(f'sqlite:///{tmp_path / name}', future=True)
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _use_storage_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, label: str) -> None:
    monkeypatch.setattr(config_module.settings, 'uploads_dir', tmp_path / label / 'uploads')
    monkeypatch.setattr(config_module.settings, 'results_dir', tmp_path / label / 'results')


def _build_source_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, SourceSession) -> dict:
    """Populate a dedicated source DB with one row touching every mechanism
    the engine needs to prove: an encrypted column, a large-blob column, a
    normalized upload/result path pair backed by a real file, a
    self-referential job chain, a delivered and a withdrawn DocumentRelease,
    and one excluded-table row."""
    monkeypatch.setattr(config_module.settings, 'secret_key', 'source-instance-secret-key')
    _use_storage_dirs(monkeypatch, tmp_path, 'source')
    config_module.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    config_module.settings.results_dir.mkdir(parents=True, exist_ok=True)

    unique = uuid.uuid4().hex[:8]

    db = SourceSession()
    try:
        team = Team(name=f'Team A {unique}')
        db.add(team)
        db.flush()

        owner_username = f'regular-{unique}'
        owner_email = f'Regular-{unique}@Example.com'
        owner = User(
            username=owner_username, email=owner_email, password_hash=hash_password('IrrelevantPw1'),
            role=UserRole.USER, team_id=team.id,
        )
        db.add(owner)
        db.flush()

        auth_provider = AuthProvider(
            slug=f'keycloak-{unique}', display_name='Keycloak', issuer_url='https://idp.example.com',
            client_id='client-1', client_secret_encrypted=encrypt_client_secret('oidc-secret-value'),
        )
        import_source = ImportSource(
            owner_id=owner.id, name='Docs Space', base_url='https://confluence.example.com',
            auth_type=ImportAuthType.PAT_BEARER, credential_encrypted=encrypt_import_credential('confluence-token'),
        )
        managed_bot = ManagedBot(
            id=f'bot-{unique}', name='Bot One', auth_token_encrypted=encrypt_managed_bot_auth_token('bot-token-value'),
        )
        webhook_connection = WebhookConnection(
            name='Hook', url='https://hooks.example.com/x', secret_encrypted=encrypt_webhook_secret('hook-secret'),
        )
        collection = Collection(slug=f'docs-{unique}', name='Docs')
        db.add_all([auth_provider, import_source, managed_bot, webhook_connection, collection])
        db.flush()

        upload_dir = config_module.settings.uploads_dir / 'folder1'
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_file = upload_dir / 'job1.pdf'
        upload_file.write_bytes(b'pdf bytes on disk')
        result_dir = config_module.settings.results_dir / 'folder1'
        result_dir.mkdir(parents=True, exist_ok=True)
        result_file = result_dir / 'job1.md'
        result_file.write_text('# Result', encoding='utf-8')

        job1 = Job(
            original_filename='job1.pdf', upload_path=str(upload_file.resolve()),
            upload_content=b'pdf blob content', result_path=str(result_file.resolve()),
            result_markdown='# Result', status=JobStatus.FINISHED, owner_id=owner.id,
        )
        db.add(job1)
        db.flush()

        job2 = Job(
            original_filename='job1.pdf', upload_path=str(upload_file.resolve()), status=JobStatus.FINISHED,
            owner_id=owner.id, previous_job_id=job1.id, document_version=2,
        )
        db.add(job2)
        db.flush()

        artifact = JobArtifact(
            job_id=job1.id, kind='attachment', filename='img.png', content_type='image/png',
            content=b'artifact bytes', size_bytes=13, sha256=hashlib.sha256(b'artifact bytes').hexdigest(),
        )
        db.add(artifact)

        release_live = DocumentRelease(
            job_id=job1.id, owner_id=owner.id, markdown_snapshot='snap', markdown_sha256='a' * 64,
            payload={'event': 'document.released', 'job_id': job1.id}, status='sent', attempts=1,
            lease_token='stale-token',
        )
        release_withdrawn = DocumentRelease(
            job_id=job2.id, owner_id=owner.id, markdown_snapshot='snap2', markdown_sha256='b' * 64,
            payload={'event': 'document.released', 'job_id': job2.id}, status='sent', attempts=1,
        )
        db.add_all([release_live, release_withdrawn])
        db.flush()

        withdrawal = KnowledgeWithdrawal(job_id=job2.id, status='sent')
        db.add(withdrawal)

        # Excluded-table row: must never appear in the archive.
        db.add(SessionModel(
            token_hash=hash_session_token(f'irrelevant-token-{unique}'), user_id=owner.id,
            expires_at=job1.created_at,
        ))

        db.commit()

        return {
            'team_id': team.id, 'owner_id': owner.id, 'owner_username': owner_username, 'owner_email': owner_email,
            'job1_id': job1.id, 'job2_id': job2.id,
            'artifact_id': artifact.id, 'auth_provider_id': auth_provider.id,
            'import_source_id': import_source.id, 'managed_bot_id': managed_bot.id,
            'webhook_connection_id': webhook_connection.id, 'collection_id': collection.id,
            'upload_file': upload_file, 'result_file': result_file,
        }
    finally:
        db.close()


def test_round_trip_preserves_data_ids_files_blobs_and_reencrypts(monkeypatch, tmp_path):
    source_engine, SourceSession = _new_engine(tmp_path, 'source.db')
    ids = _build_source_data(monkeypatch, tmp_path, SourceSession)

    archive_path = tmp_path / 'export.weave-backup.tar.gz'
    db = SourceSession()
    try:
        manifest = backup.export_backup(db, passphrase='correct horse battery', out_path=archive_path)
    finally:
        db.close()
    assert manifest['format_version'] == 1
    assert manifest['tables']['jobs'] == 2

    # Import onto a fresh, independent target DB under a DIFFERENT
    # SECRET_KEY and a different storage layout.
    target_engine, TargetSession = _new_engine(tmp_path, 'target.db')
    monkeypatch.setattr(config_module.settings, 'secret_key', 'target-instance-secret-key')
    _use_storage_dirs(monkeypatch, tmp_path, 'target')

    target_db = TargetSession()
    try:
        admin = User(username='bootstrap', email='bootstrap@example.com', password_hash=hash_password('Boots1'), role=UserRole.ADMIN)
        target_db.add(admin)
        target_db.commit()
        target_db.refresh(admin)
        admin_id = admin.id

        report = backup.import_backup(
            target_db, path=archive_path, passphrase='correct horse battery', importing_admin_id=admin_id,
        )
        target_db.commit()

        assert report['tables']['jobs'] == 2
        assert report['files_restored'] >= 2
        assert report['requires_relogin'] is True

        job1 = target_db.get(Job, ids['job1_id'])
        job2 = target_db.get(Job, ids['job2_id'])
        assert job1 is not None and job2 is not None
        # ids preserved, self-referential FK fixed up by the deferred pass.
        assert job2.previous_job_id == job1.id
        # blob restored
        assert job1.upload_content == b'pdf blob content'
        artifact = target_db.get(JobArtifact, ids['artifact_id'])
        assert artifact.content == b'artifact bytes'
        # files restored, re-anchored under the TARGET's own uploads/results roots
        assert Path(job1.upload_path).is_file()
        assert Path(job1.upload_path).read_bytes() == b'pdf bytes on disk'
        assert str(config_module.settings.uploads_dir.resolve()) in job1.upload_path
        assert Path(job1.result_path).read_text(encoding='utf-8') == '# Result'

        # every *_encrypted column decrypts correctly under the TARGET's key
        from app.services.security import (
            decrypt_client_secret, decrypt_import_credential, decrypt_managed_bot_auth_token, decrypt_webhook_secret,
        )
        auth_provider = target_db.get(AuthProvider, ids['auth_provider_id'])
        assert decrypt_client_secret(auth_provider.client_secret_encrypted) == 'oidc-secret-value'
        import_source = target_db.get(ImportSource, ids['import_source_id'])
        assert decrypt_import_credential(import_source.credential_encrypted) == 'confluence-token'
        managed_bot = target_db.get(ManagedBot, ids['managed_bot_id'])
        assert decrypt_managed_bot_auth_token(managed_bot.auth_token_encrypted) == 'bot-token-value'
        webhook_connection = target_db.get(WebhookConnection, ids['webhook_connection_id'])
        assert decrypt_webhook_secret(webhook_connection.secret_encrypted) == 'hook-secret'

        # release whose job has NO KnowledgeWithdrawal is re-queued...
        release_live = target_db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == ids['job1_id']))
        assert release_live.status == 'pending'
        assert release_live.attempts == 0
        assert release_live.next_attempt_at is not None
        assert release_live.lease_token is None
        # ...one whose job WAS withdrawn is left alone (but leases cleared).
        release_withdrawn = target_db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == ids['job2_id']))
        assert release_withdrawn.status == 'sent'
        assert release_withdrawn.lease_token is None
        assert report['requeued_releases'] == 1
    finally:
        target_db.close()


def test_transient_tables_excluded_from_archive(monkeypatch, tmp_path):
    source_engine, SourceSession = _new_engine(tmp_path, 'source.db')
    _build_source_data(monkeypatch, tmp_path, SourceSession)
    archive_path = tmp_path / 'export.weave-backup.tar.gz'
    db = SourceSession()
    try:
        manifest = backup.export_backup(db, passphrase='pw', out_path=archive_path)
    finally:
        db.close()

    assert set(backup.EXCLUDED_TABLES) == {'sessions', 'login_handoff_codes', 'backup_runs'}
    for excluded in backup.EXCLUDED_TABLES:
        assert excluded not in manifest['tables']
    with tarfile.open(archive_path, 'r:gz') as tar:
        names = tar.getnames()
    for excluded in backup.EXCLUDED_TABLES:
        assert f'tables/{excluded}.jsonl' not in names


def test_wrong_passphrase_rejected_before_any_write(monkeypatch, tmp_path):
    source_engine, SourceSession = _new_engine(tmp_path, 'source.db')
    _build_source_data(monkeypatch, tmp_path, SourceSession)
    archive_path = tmp_path / 'export.weave-backup.tar.gz'
    db = SourceSession()
    try:
        backup.export_backup(db, passphrase='right-passphrase', out_path=archive_path)
    finally:
        db.close()

    with pytest.raises(backup.WrongPassphraseError):
        backup.inspect_backup(archive_path, 'wrong-passphrase')

    target_engine, TargetSession = _new_engine(tmp_path, 'target_wrong_pw.db')
    target_db = TargetSession()
    try:
        admin = User(username='bootstrap', email='bootstrap@example.com', password_hash=hash_password('Boots1'), role=UserRole.ADMIN)
        target_db.add(admin)
        target_db.commit()
        target_db.refresh(admin)

        with pytest.raises(backup.WrongPassphraseError):
            backup.import_backup(
                target_db, path=archive_path, passphrase='wrong-passphrase', importing_admin_id=admin.id,
            )
        # Refused before any write: still exactly the one bootstrap admin.
        assert target_db.scalar(select(Job).limit(1)) is None
        assert len(target_db.scalars(select(User)).all()) == 1
    finally:
        target_db.close()


def test_non_fresh_target_refused_then_wiped_with_force(monkeypatch, tmp_path):
    source_engine, SourceSession = _new_engine(tmp_path, 'source.db')
    _build_source_data(monkeypatch, tmp_path, SourceSession)
    archive_path = tmp_path / 'export.weave-backup.tar.gz'
    db = SourceSession()
    try:
        backup.export_backup(db, passphrase='pw', out_path=archive_path)
    finally:
        db.close()

    target_engine, TargetSession = _new_engine(tmp_path, 'target_nonfresh.db')
    monkeypatch.setattr(config_module.settings, 'secret_key', 'target-secret')
    _use_storage_dirs(monkeypatch, tmp_path, 'target_nonfresh')
    target_db = TargetSession()
    try:
        admin = User(username='bootstrap', email='bootstrap@example.com', password_hash=hash_password('Boots1'), role=UserRole.ADMIN)
        target_db.add(admin)
        # Pre-existing data makes this target non-fresh.
        stray_job = Job(original_filename='stray.pdf', upload_path='/tmp/stray.pdf', status=JobStatus.PENDING)
        target_db.add(stray_job)
        target_db.commit()
        target_db.refresh(admin)
        stray_job_id = stray_job.id

        state = backup.target_state(target_db)
        assert state['fresh'] is False
        assert any('Dokumente' in reason for reason in state['reasons'])

        with pytest.raises(backup.TargetNotFreshError):
            backup.import_backup(target_db, path=archive_path, passphrase='pw', importing_admin_id=admin.id, force=False)
        target_db.rollback()

        report = backup.import_backup(
            target_db, path=archive_path, passphrase='pw', importing_admin_id=admin.id, force=True,
        )
        target_db.commit()
        assert report['tables']['jobs'] == 2
        # The stray pre-existing job is gone -- force wiped it.
        assert target_db.get(Job, stray_job_id) is None
    finally:
        target_db.close()


def test_admin_merge_matched_username_keeps_login(monkeypatch, tmp_path):
    source_engine, SourceSession = _new_engine(tmp_path, 'source.db')
    ids = _build_source_data(monkeypatch, tmp_path, SourceSession)
    archive_path = tmp_path / 'export.weave-backup.tar.gz'
    db = SourceSession()
    try:
        backup.export_backup(db, passphrase='pw', out_path=archive_path)
    finally:
        db.close()

    target_engine, TargetSession = _new_engine(tmp_path, 'target_merge.db')
    monkeypatch.setattr(config_module.settings, 'secret_key', 'target-secret')
    _use_storage_dirs(monkeypatch, tmp_path, 'target_merge')
    target_db = TargetSession()
    try:
        # The importing admin happens to share the exported source owner's
        # username -- that exported row must end up as an admin, carrying
        # the importing admin's own current password hash.
        new_hash = hash_password('BrandNewPw1')
        admin = User(
            username=ids['owner_username'], email='someone-else@example.com', password_hash=new_hash,
            role=UserRole.ADMIN,
        )
        target_db.add(admin)
        target_db.commit()
        target_db.refresh(admin)

        report = backup.import_backup(target_db, path=archive_path, passphrase='pw', importing_admin_id=admin.id)
        target_db.commit()

        users = target_db.scalars(select(User)).all()
        # Exactly the exported users -- no extra "additional admin" row.
        assert {u.username for u in users} == {ids['owner_username']}
        merged = target_db.scalar(select(User).where(User.username == ids['owner_username']))
        assert merged.id == ids['owner_id']
        assert merged.role == UserRole.ADMIN
        assert merged.password_hash == new_hash
        assert report['requires_relogin'] is True
    finally:
        target_db.close()


def test_admin_merge_no_match_keeps_additional_admin(monkeypatch, tmp_path):
    source_engine, SourceSession = _new_engine(tmp_path, 'source.db')
    ids = _build_source_data(monkeypatch, tmp_path, SourceSession)
    archive_path = tmp_path / 'export.weave-backup.tar.gz'
    db = SourceSession()
    try:
        backup.export_backup(db, passphrase='pw', out_path=archive_path)
    finally:
        db.close()

    target_engine, TargetSession = _new_engine(tmp_path, 'target_nomerge.db')
    monkeypatch.setattr(config_module.settings, 'secret_key', 'target-secret')
    _use_storage_dirs(monkeypatch, tmp_path, 'target_nomerge')
    target_db = TargetSession()
    try:
        admin = User(username='new-admin', email='new-admin@example.com', password_hash=hash_password('Boots1'), role=UserRole.ADMIN)
        target_db.add(admin)
        target_db.commit()
        target_db.refresh(admin)
        admin_id, admin_username = admin.id, admin.username

        backup.import_backup(target_db, path=archive_path, passphrase='pw', importing_admin_id=admin_id)
        target_db.commit()

        users = target_db.scalars(select(User)).all()
        usernames = {u.username for u in users}
        assert ids['owner_username'] in usernames  # exported user, unmodified role
        assert admin_username in usernames  # importing admin kept as additional admin
        kept = target_db.get(User, admin_id)
        assert kept is not None
        assert kept.role == UserRole.ADMIN
        exported = target_db.scalar(select(User).where(User.username == ids['owner_username']))
        assert exported.role == UserRole.USER
    finally:
        target_db.close()


def test_admin_merge_resets_lockout_and_deactivation(monkeypatch, tmp_path):
    """An archived account that matches the importing admin by identity but
    was deactivated or lockout-throttled at export time must not leave the
    importing admin locked out of the very system they just restored --
    see the admin-identity merge in `import_backup`."""
    from datetime import datetime, timedelta, timezone

    source_engine, SourceSession = _new_engine(tmp_path, 'source_lockout.db')
    monkeypatch.setattr(config_module.settings, 'secret_key', 'source-instance-secret-key')
    _use_storage_dirs(monkeypatch, tmp_path, 'source_lockout')

    unique = uuid.uuid4().hex[:8]
    owner_username = f'locked-admin-{unique}'
    db = SourceSession()
    try:
        owner = User(
            username=owner_username, email=f'{owner_username}@example.com',
            password_hash=hash_password('OldArchivedPw1'), role=UserRole.ADMIN,
            is_active=False, locked_until=datetime.now(timezone.utc) + timedelta(hours=1),
            failed_login_count=5,
        )
        db.add(owner)
        db.commit()
    finally:
        db.close()

    archive_path = tmp_path / 'export_lockout.weave-backup.tar.gz'
    db = SourceSession()
    try:
        backup.export_backup(db, passphrase='pw', out_path=archive_path)
    finally:
        db.close()

    target_engine, TargetSession = _new_engine(tmp_path, 'target_lockout.db')
    monkeypatch.setattr(config_module.settings, 'secret_key', 'target-secret')
    _use_storage_dirs(monkeypatch, tmp_path, 'target_lockout')
    target_db = TargetSession()
    try:
        new_hash = hash_password('CurrentLoginPw1')
        admin = User(
            username=owner_username, email='someone-else@example.com', password_hash=new_hash,
            role=UserRole.ADMIN,
        )
        target_db.add(admin)
        target_db.commit()
        target_db.refresh(admin)

        backup.import_backup(target_db, path=archive_path, passphrase='pw', importing_admin_id=admin.id)
        target_db.commit()

        merged = target_db.scalar(select(User).where(User.username == owner_username))
        assert merged.role == UserRole.ADMIN
        assert merged.password_hash == new_hash
        assert merged.is_active is True
        assert merged.locked_until is None
        assert merged.failed_login_count == 0
    finally:
        target_db.close()


def _write_minimal_archive(path: Path, passphrase: str, tables: dict) -> None:
    """Hand-build a minimal, valid archive for one or two small tables --
    used to exercise the column-mapping rules (unknown column -> warning,
    missing column -> default) without going through a full export."""
    import base64
    import os
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    salt = os.urandom(16)
    fernet = Fernet(backup._derive_export_fernet_key(passphrase, salt))
    manifest = {
        'format_version': backup.FORMAT_VERSION,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source': {'alembic_revision': None, 'instance_name': 'test'},
        'tables': {name: len(rows) for name, rows in tables.items()},
        'encryption': 'passphrase-fernet-v1',
        'kdf': {
            'algorithm': 'pbkdf2-hmac-sha256', 'iterations': backup.PBKDF2_ITERATIONS,
            'salt': base64.b64encode(salt).decode('ascii'),
        },
        'passphrase_check': fernet.encrypt(backup.PASSPHRASE_CHECK_PLAINTEXT).decode('utf-8'),
    }
    with tarfile.open(path, 'w:gz') as tar:
        backup._add_bytes(tar, 'manifest.json', json.dumps(manifest).encode('utf-8'))
        for name, rows in tables.items():
            content = '\n'.join(json.dumps(row) for row in rows).encode('utf-8')
            backup._add_bytes(tar, f'tables/{name}.jsonl', content)


def test_unknown_column_tolerated_missing_column_defaulted(monkeypatch, tmp_path):
    archive_path = tmp_path / 'minimal.weave-backup.tar.gz'
    _write_minimal_archive(archive_path, 'pw', {
        'teams': [
            {'id': 'team-x', 'name': 'From Archive', 'nonexistent_future_column': 'surprise'},
        ],
    })

    target_engine, TargetSession = _new_engine(tmp_path, 'target_unknown_col.db')
    _use_storage_dirs(monkeypatch, tmp_path, 'target_unknown_col')
    target_db = TargetSession()
    try:
        admin = User(username='bootstrap', email='bootstrap@example.com', password_hash=hash_password('Boots1'), role=UserRole.ADMIN)
        target_db.add(admin)
        target_db.commit()
        target_db.refresh(admin)

        report = backup.import_backup(target_db, path=archive_path, passphrase='pw', importing_admin_id=admin.id)
        target_db.commit()

        team = target_db.get(Team, 'team-x')
        assert team is not None
        assert team.name == 'From Archive'
        # created_at was missing from the archived row entirely -- the
        # column's own Python default filled it in rather than erroring.
        assert team.created_at is not None
        assert any('unbekannte Spalte' in warning for warning in report['warnings'])
    finally:
        target_db.close()


def test_pre_visibility_archive_keeps_team_restricted_collections_restricted(monkeypatch, tmp_path):
    """An archive from before 0032_collection_visibility has no `visibility`
    column: restore must derive it exactly like that migration's backfill,
    never fall back to the model's default for either kind of row."""
    archive_path = tmp_path / 'pre-0032.weave-backup.tar.gz'
    _write_minimal_archive(archive_path, 'pw', {
        'collections': [
            {'id': 'c-public', 'slug': 'oeffentlich', 'name': 'Öffentlich', 'description': '', 'read_teams': []},
            {'id': 'c-team', 'slug': 'service', 'name': 'Service', 'description': '', 'read_teams': ['Kundenservice']},
        ],
    })

    target_engine, TargetSession = _new_engine(tmp_path, 'target_pre_0032.db')
    _use_storage_dirs(monkeypatch, tmp_path, 'target_pre_0032')
    target_db = TargetSession()
    try:
        admin = User(username='bootstrap', email='bootstrap@example.com', password_hash=hash_password('Boots1'), role=UserRole.ADMIN)
        target_db.add(admin)
        target_db.commit()

        backup.import_backup(target_db, path=archive_path, passphrase='pw', importing_admin_id=admin.id)
        target_db.commit()

        assert target_db.get(Collection, 'c-public').visibility == CollectionVisibility.PUBLIC
        assert target_db.get(Collection, 'c-team').visibility == CollectionVisibility.RESTRICTED
        assert target_db.get(Collection, 'c-team').read_users == []
    finally:
        target_db.close()
