"""Review dates ('Prüfung & Aktualität', app/services/document_freshness.py):
release hook, portal review endpoints, the review_due list filter, the
space's review interval, the document_review_tick and the webhook event
document.review_due.

Every test builds its own knowledge space with a unique slug; assertions
are scoped by job/collection id because test.db is shared.
"""

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.models.models import (
    Collection,
    CollectionGrant,
    CollectionRole,
    CollectionVisibility,
    DocumentRelease,
    Job,
    JobStatus,
    KnowledgeWithdrawal,
    ReviewTask,
    UserRole,
    WebhookConnection,
    WebhookDelivery,
)
from app.services import document_freshness, security
from app.services.webhooks import build_review_due_payload
from app.workers import freshness_tasks, publication_tasks, webhook_tasks
from tests.conftest import TestingSessionLocal, add_legacy_collection, create_test_user, login_as


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    security.rate_limiter.reset()
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'secret')
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    monkeypatch.setattr(freshness_tasks, 'SessionLocal', TestingSessionLocal)
    with TestingSessionLocal() as db:
        before = set(db.scalars(select(DocumentRelease.id)).all())
    yield
    # test.db is shared process-wide and some suites count every release
    # (e.g. the knowledge index rebuild): leave none behind.
    with TestingSessionLocal() as db:
        db.execute(delete(DocumentRelease).where(DocumentRelease.id.not_in(before)))
        db.commit()


def _db():
    return TestingSessionLocal()


def _user(prefix: str, role: UserRole = UserRole.USER):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'{prefix}-{suffix}', email=f'{prefix}-{suffix}@example.com', role=role)


def _space(*, interval: int | None = None, public: bool = False, name: str = 'Reiseregeln'):
    owner, member, reader, outsider = _user('fowner'), _user('fmember'), _user('freader'), _user('foutsider')
    with _db() as db:
        collection = add_legacy_collection(db, owner_id=owner.id, slug=f'fresh-{uuid.uuid4().hex[:12]}', name=name)
        collection.review_interval_days = interval
        if public:
            collection.visibility = CollectionVisibility.PUBLIC
        db.add(CollectionGrant(collection_id=collection.id, user_id=member.id, role=CollectionRole.MEMBER))
        db.add(CollectionGrant(collection_id=collection.id, user_id=reader.id, role=CollectionRole.READER))
        db.commit()
        collection_id, slug = collection.id, collection.slug
    return SimpleNamespace(
        id=collection_id, slug=slug, name=name, owner=owner, member=member, reader=reader, outsider=outsider,
    )


def markdown(body: str, frontmatter: str = '') -> str:
    return f'---\ncollection: edited\nengine: test\nprocessed_at: now\n{frontmatter}---\n\n{body}'


def make_doc(space, body: str = '# Reisekosten\n\nTagegeld 24 Euro.\n', *, frontmatter: str = '',
             previous: str | None = None, version: int = 1, filename: str = 'reiseregeln.pdf') -> str:
    with _db() as db:
        job = Job(
            original_filename=filename,
            upload_path=f'/tmp/{filename}',
            status=JobStatus.FINISHED,
            owner_id=space.owner.id,
            content_sha256=hashlib.sha256(f'{filename}-{version}-{uuid.uuid4()}'.encode()).hexdigest(),
            result_markdown=markdown(body, frontmatter),
            previous_job_id=previous,
            document_version=version,
            processing_info={
                'settings': {'collection_id': space.id},
                'execution': {'quality_gate': {'grade': 'A', 'recommendation': 'allow'}},
            },
        )
        db.add(job)
        db.commit()
        return job.id


def release(client, job_id: str):
    preview = client.get(f'/api/v1/portal/documents/{job_id}')
    assert preview.status_code == 200, preview.text
    response = client.post(
        f'/api/v1/portal/documents/{job_id}/release', json={'markdown_sha256': preview.json()['markdown_sha256']}
    )
    assert response.status_code == 202, response.text
    return response


def _job(job_id: str) -> Job:
    with _db() as db:
        return db.get(Job, job_id)


def _aware(value):
    return document_freshness.aware(value)


def _review_tasks(job_id: str) -> list[ReviewTask]:
    with _db() as db:
        return list(db.scalars(
            select(ReviewTask).where(ReviewTask.kind == 'review_due', ReviewTask.job_id == job_id)
            .order_by(ReviewTask.created_at, ReviewTask.id)
        ).all())


def _set_due(job_id: str, value: datetime | None) -> None:
    with _db() as db:
        db.get(Job, job_id).review_due_at = value
        db.commit()


# --- release hook ---------------------------------------------------------------


def test_release_uses_frontmatter_review_due_over_interval():
    space = _space(interval=30)
    job_id = make_doc(space, frontmatter='review_due: 2027-03-31\n')
    client = login_as(space.owner.username)
    release(client, job_id)
    job = _job(job_id)
    assert _aware(job.review_due_at) == datetime(2027, 3, 31, tzinfo=timezone.utc)
    assert job.last_reviewed_by_id == space.owner.id
    assert abs((_aware(job.last_reviewed_at) - datetime.now(timezone.utc)).total_seconds()) < 60


def test_release_uses_space_interval_and_detail_shows_review():
    space = _space(interval=30)
    job_id = make_doc(space)
    client = login_as(space.member.username)
    release(client, job_id)
    due = _aware(_job(job_id).review_due_at)
    assert timedelta(days=29, hours=23) < due - datetime.now(timezone.utc) <= timedelta(days=30)
    review = client.get(f'/api/v1/portal/documents/{job_id}').json()['review']
    assert review['last_reviewed_by'] == space.member.username
    assert review['overdue'] is False and review['review_due_at'] and review['last_reviewed_at']


def test_release_without_date_or_interval_records_review_only():
    space = _space()
    job_id = make_doc(space, frontmatter='review_due: kein-datum\n')
    client = login_as(space.owner.username)
    assert client.get(f'/api/v1/portal/documents/{job_id}').json()['review'] is None
    release(client, job_id)
    job = _job(job_id)
    assert job.review_due_at is None and job.last_reviewed_at is not None


def test_new_version_release_clears_old_due_date_and_closes_its_task():
    space = _space(interval=10)
    client = login_as(space.owner.username)
    v1 = make_doc(space)
    release(client, v1)
    _set_due(v1, datetime.now(timezone.utc) - timedelta(days=1))
    document_freshness_pass()
    assert [task.status for task in _review_tasks(v1)] == ['open']
    v2 = make_doc(space, '# Reisekosten\n\nTagegeld 28 Euro.\n', previous=v1, version=2)
    release(client, v2)
    assert _job(v1).review_due_at is None
    closed = _review_tasks(v1)[0]
    assert (closed.status, closed.resolution_note, closed.resolved_by_id) == ('done', 'Neue Version freigegeben', space.owner.id)
    assert _job(v2).review_due_at is not None


def test_bulk_release_applies_review_dates():
    space = _space(interval=5)
    job_id = make_doc(space)
    client = login_as(space.owner.username)
    response = client.post('/api/v1/portal/documents/bulk', json={'action': 'release', 'job_ids': [job_id]})
    assert response.status_code == 200 and response.json()['done'] == 1, response.text
    job = _job(job_id)
    assert job.review_due_at is not None and job.last_reviewed_by_id == space.owner.id


# --- review endpoints -------------------------------------------------------------


def test_put_review_sets_and_clears_due_date():
    space = _space()
    job_id = make_doc(space)
    client = login_as(space.member.username)
    response = client.put(f'/api/v1/portal/documents/{job_id}/review', json={'review_due_at': '2020-01-01'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['review_due_at'].startswith('2020-01-01') and body['overdue'] is True
    assert body['last_reviewed_at'] is None and body['last_reviewed_by'] is None
    cleared = client.put(f'/api/v1/portal/documents/{job_id}/review', json={'review_due_at': None})
    assert cleared.status_code == 200 and cleared.json()['review_due_at'] is None
    assert _job(job_id).review_due_at is None


def test_review_endpoints_hide_documents_from_readers_and_outsiders_and_validate():
    space = _space()
    job_id = make_doc(space)
    for user in (space.reader, space.outsider):
        client = login_as(user.username)
        assert client.put(f'/api/v1/portal/documents/{job_id}/review', json={'review_due_at': '2030-01-01'}).status_code == 404
        assert client.post(f'/api/v1/portal/documents/{job_id}/review/confirm').status_code == 404
    owner = login_as(space.owner.username)
    assert owner.put(f'/api/v1/portal/documents/{job_id}/review', json={'review_due_at': 'morgen'}).status_code == 422
    assert owner.put(f'/api/v1/portal/documents/{job_id}/review', json={}).status_code == 422
    assert owner.put(f'/api/v1/portal/documents/{uuid.uuid4()}/review', json={'review_due_at': None}).status_code == 404
    assert _job(job_id).review_due_at is None


def test_confirm_uses_interval_explicit_date_or_none_and_closes_tasks():
    space = _space(interval=90)
    client = login_as(space.owner.username)
    job_id = make_doc(space)
    release(client, job_id)
    _set_due(job_id, datetime.now(timezone.utc) - timedelta(days=2))
    document_freshness_pass()
    assert _review_tasks(job_id)[0].status == 'open'

    member = login_as(space.member.username)
    confirmed = member.post(f'/api/v1/portal/documents/{job_id}/review/confirm')
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body['last_reviewed_by'] == space.member.username and body['overdue'] is False
    due = datetime.fromisoformat(body['review_due_at'])
    assert timedelta(days=89) < _aware(due) - datetime.now(timezone.utc) <= timedelta(days=90)
    task = _review_tasks(job_id)[0]
    assert (task.status, task.resolved_by_id, task.resolution_note) == ('done', space.member.id, 'Als geprüft bestätigt')

    explicit = member.post(f'/api/v1/portal/documents/{job_id}/review/confirm', json={'review_due_at': '2031-05-01'})
    assert explicit.json()['review_due_at'].startswith('2031-05-01')

    with _db() as db:
        db.get(Collection, space.id).review_interval_days = None
        db.commit()
    assert member.post(f'/api/v1/portal/documents/{job_id}/review/confirm').json()['review_due_at'] is None


# --- list filter ---------------------------------------------------------------------


def test_review_due_filter_lists_only_current_released_due_versions():
    space = _space()
    client = login_as(space.owner.username)
    due_now = make_doc(space, filename='faellig.pdf')
    release(client, due_now)
    later = make_doc(space, filename='spaeter.pdf')
    release(client, later)
    unreleased = make_doc(space, filename='entwurf.pdf')
    superseded = make_doc(space, filename='alt.pdf')
    release(client, superseded)
    successor = make_doc(space, filename='alt.pdf', previous=superseded, version=2)
    release(client, successor)
    withdrawn = make_doc(space, filename='zurueckgezogen.pdf')
    release(client, withdrawn)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    for job_id in (due_now, unreleased, superseded, withdrawn):
        _set_due(job_id, past)
    _set_due(later, datetime.now(timezone.utc) + timedelta(days=3))
    with _db() as db:
        db.add(KnowledgeWithdrawal(job_id=withdrawn))
        db.commit()
    response = client.get('/api/v1/portal/documents', params={'collection_id': space.id, 'review_state': 'review_due'})
    assert response.status_code == 200, response.text
    assert [item['id'] for item in response.json()['items']] == [due_now]
    assert response.json()['total'] == 1


# --- collection review interval ----------------------------------------------------------


def test_collection_review_interval_is_validated_and_owner_only():
    space = _space()
    owner = login_as(space.owner.username)
    url = f'/api/v1/collections/{space.id}'
    response = owner.patch(url, json={'review_interval_days': 180})
    assert response.status_code == 200, response.text
    assert response.json()['review_interval_days'] == 180
    assert owner.patch(url, json={'name': 'Reiseregeln neu'}).json()['review_interval_days'] == 180
    for invalid in (0, 3651, -5):
        assert owner.patch(url, json={'review_interval_days': invalid}).status_code == 422
    member = login_as(space.member.username)
    assert member.patch(url, json={'review_interval_days': 1}).status_code in (403, 404)
    assert owner.patch(url, json={'review_interval_days': None}).json()['review_interval_days'] is None
    created = owner.post('/api/v1/collections', json={
        'name': f'Intervall {uuid.uuid4().hex[:6]}', 'description': 'Zweck', 'review_interval_days': 365,
    })
    assert created.status_code == 200, created.text
    assert created.json()['review_interval_days'] == 365
    listed = {item['collection_id']: item for item in owner.get('/api/v1/collections').json()['items']}
    assert listed[created.json()['collection_id']]['review_interval_days'] == 365


# --- tick + webhook ----------------------------------------------------------------------


def document_freshness_pass():
    with patch.object(webhook_tasks.celery_app, 'send_task') as send_task:
        result = freshness_tasks.run_document_review_pass()
    return result, send_task


def _connection(owner_id: str, events: list[str], *, enabled: bool = True) -> str:
    with _db() as db:
        connection = WebhookConnection(
            name=f'n8n-{uuid.uuid4().hex[:6]}', url='https://hooks.example/review', events=events,
            owner_id=owner_id, enabled=enabled,
        )
        db.add(connection)
        db.commit()
        return connection.id


def _deliveries(job_id: str) -> list[WebhookDelivery]:
    with _db() as db:
        return list(db.scalars(select(WebhookDelivery).where(WebhookDelivery.job_id == job_id)).all())


def test_tick_opens_one_task_and_notifies_subscribed_owners_once(monkeypatch):
    monkeypatch.setattr(settings, 'document_review_lead_days', 7)
    space = _space(interval=365)
    client = login_as(space.owner.username)
    job_id = make_doc(space)
    release(client, job_id)
    member_hook = _connection(space.member.id, ['document.review_due'])
    _connection(space.reader.id, ['document.review_due'])  # reader: no reminder
    _connection(space.outsider.id, ['document.review_due'])  # no access at all
    _connection(space.owner.id, ['job.finished'])  # not subscribed
    _connection(space.owner.id, ['document.review_due'], enabled=False)
    _set_due(job_id, datetime.now(timezone.utc) + timedelta(days=3))  # within the lead time

    (result, send_task) = document_freshness_pass()
    assert (job_id, space.id) in result['created']
    tasks = _review_tasks(job_id)
    assert len(tasks) == 1
    task = tasks[0]
    assert (task.status, task.cluster_key, task.collection_id) == ('open', f'review_due:{job_id}', space.id)
    assert task.title == 'Prüfung fällig: „reiseregeln.pdf“'
    assert task.details['overdue'] is False and task.details['review_due_at']
    deliveries = _deliveries(job_id)
    assert [(d.connection_id, d.event, d.owner_id) for d in deliveries] == [(member_hook, 'document.review_due', space.member.id)]
    send_task.assert_called_once_with('deliver_webhook', args=[deliveries[0].id])

    # Second run: same task, no second webhook; overdue flips the details.
    _set_due(job_id, datetime.now(timezone.utc) - timedelta(hours=1))
    (result, send_task) = document_freshness_pass()
    assert (job_id, space.id) not in result['created']
    tasks = _review_tasks(job_id)
    assert len(tasks) == 1 and tasks[0].details['overdue'] is True
    assert len(_deliveries(job_id)) == 1
    send_task.assert_not_called()

    # The task is visible in the owners' inbox.
    listed = client.get('/api/v1/portal/review-tasks', params={'collection_id': space.id, 'kind': 'review_due'}).json()
    assert [item['id'] for item in listed['items']] == [task.id]
    assert listed['items'][0]['document']['job_id'] == job_id


def test_tick_ignores_far_dates_respects_closed_tasks_and_dismisses_stale_ones(monkeypatch):
    monkeypatch.setattr(settings, 'document_review_lead_days', 7)
    space = _space()
    client = login_as(space.owner.username)
    job_id = make_doc(space)
    release(client, job_id)
    _set_due(job_id, datetime.now(timezone.utc) + timedelta(days=30))
    document_freshness_pass()
    assert _review_tasks(job_id) == []

    _set_due(job_id, datetime.now(timezone.utc) - timedelta(days=1))
    document_freshness_pass()
    task_id = _review_tasks(job_id)[0].id
    # Closed by hand without confirming: not reopened for the same date.
    assert client.patch(f'/api/v1/portal/review-tasks/{task_id}', json={'status': 'dismissed'}).status_code == 200
    document_freshness_pass()
    assert [task.status for task in _review_tasks(job_id)] == ['dismissed']

    # A new due date opens a new task; moving it out again dismisses it.
    _set_due(job_id, datetime.now(timezone.utc) - timedelta(hours=2))
    document_freshness_pass()
    assert [task.status for task in _review_tasks(job_id)] == ['dismissed', 'open']
    assert client.put(f'/api/v1/portal/documents/{job_id}/review', json={'review_due_at': '2099-01-01'}).status_code == 200
    (result, _send) = document_freshness_pass()
    stale = _review_tasks(job_id)[1]
    assert (stale.status, stale.resolution_note) == ('dismissed', 'Prüfung nicht mehr fällig')
    assert result['dismissed'] >= 1


def test_tick_skips_unreleased_superseded_and_withdrawn_versions():
    space = _space()
    client = login_as(space.owner.username)
    unreleased = make_doc(space)
    old = make_doc(space, filename='alt.pdf')
    release(client, old)
    new = make_doc(space, filename='alt.pdf', previous=old, version=2)
    release(client, new)
    withdrawn = make_doc(space, filename='weg.pdf')
    release(client, withdrawn)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    for job_id in (unreleased, old, withdrawn):
        _set_due(job_id, past)
    with _db() as db:
        db.add(KnowledgeWithdrawal(job_id=withdrawn))
        db.commit()
    document_freshness_pass()
    for job_id in (unreleased, old, withdrawn):
        assert _review_tasks(job_id) == []


def test_tick_without_webhooks_enabled_still_opens_task(monkeypatch):
    monkeypatch.setattr(settings, 'webhooks_enabled', False)
    space = _space()
    client = login_as(space.owner.username)
    job_id = make_doc(space)
    release(client, job_id)
    _connection(space.owner.id, ['document.review_due'])
    _set_due(job_id, datetime.now(timezone.utc) - timedelta(days=1))
    (result, send_task) = document_freshness_pass()
    assert len(_review_tasks(job_id)) == 1 and _deliveries(job_id) == []
    assert result['webhooks'] == 0


def test_review_due_payload_and_delivery(monkeypatch):
    space = _space()
    client = login_as(space.owner.username)
    job_id = make_doc(space)
    release(client, job_id)
    due = datetime(2026, 1, 15, tzinfo=timezone.utc)
    _set_due(job_id, due)
    with _db() as db:
        payload = build_review_due_payload(db, db.get(Job, job_id))
    assert payload['event'] == 'document.review_due'
    assert payload['job_id'] == job_id and payload['document_version'] == 1
    assert payload['collection'] == {'id': space.id, 'slug': space.slug, 'name': space.name}
    assert payload['review_due_at'] == due.isoformat() and payload['overdue'] is True
    assert payload['portal_path'] == f'/reviews/{job_id}'
    assert 'markdown' not in payload and space.owner.username not in str(payload)

    connection_id = _connection(space.owner.id, ['document.review_due'])
    with _db() as db:
        delivery = WebhookDelivery(
            connection_id=connection_id, connection_name='n8n', owner_id=space.owner.id,
            event='document.review_due', job_id=job_id, status='pending',
        )
        db.add(delivery)
        db.commit()
        delivery_id = delivery.id
    monkeypatch.setattr(webhook_tasks, 'SessionLocal', TestingSessionLocal)
    sent = {}

    def fake_send(url, body, secret, allowed):
        sent['payload'] = body
        return 200, None

    monkeypatch.setattr(webhook_tasks, 'send_webhook_request', fake_send)
    webhook_tasks.deliver_webhook.run(delivery_id)
    assert sent['payload']['event'] == 'document.review_due' and sent['payload']['job_id'] == job_id
    with _db() as db:
        assert db.get(WebhookDelivery, delivery_id).status == 'sent'


def test_webhook_connection_accepts_review_due_event():
    user = _user('hook')
    client = login_as(user.username)
    response = client.post('/api/v1/webhooks/connections', json={
        'name': 'n8n', 'url': 'https://hooks.example/x', 'events': ['document.review_due'],
    })
    assert response.status_code in (200, 201), response.text
    assert response.json()['events'] == ['document.review_due']


def test_parse_review_due_accepts_dates_and_datetimes():
    assert document_freshness.parse_review_due('2027-01-02') == datetime(2027, 1, 2, tzinfo=timezone.utc)
    assert document_freshness.parse_review_due(date(2027, 1, 2)) == datetime(2027, 1, 2, tzinfo=timezone.utc)
    assert document_freshness.parse_review_due('2027-01-02T10:00:00Z') == datetime(2027, 1, 2, 10, tzinfo=timezone.utc)
    for invalid in (None, '', 'bald', 42, ['2027-01-02']):
        assert document_freshness.parse_review_due(invalid) is None


def test_release_rows_are_unaffected_by_review_hook():
    """The hook only touches the Job; the frozen release payload keeps the
    frontmatter's review_due verbatim for consumers."""
    space = _space()
    job_id = make_doc(space, frontmatter='review_due: 2027-06-30\n')
    client = login_as(space.owner.username)
    release(client, job_id)
    with _db() as db:
        frozen = db.scalar(select(DocumentRelease.payload).where(DocumentRelease.job_id == job_id))
    assert frozen['frontmatter']['review_due'] == '2027-06-30'


# --- review findings: inbox 'Erledigt', system dismissals, query cap, superseded versions ---


def test_done_in_inbox_confirms_the_review_and_stops_the_reminder():
    space = _space(interval=90)
    client = login_as(space.owner.username)
    job_id = make_doc(space)
    release(client, job_id)
    _set_due(job_id, datetime.now(timezone.utc) - timedelta(days=2))
    document_freshness_pass()
    task_id = _review_tasks(job_id)[0].id

    member = login_as(space.member.username)
    response = member.patch(f'/api/v1/portal/review-tasks/{task_id}', json={'status': 'done'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'done'
    task = _review_tasks(job_id)[0]
    assert (task.status, task.resolved_by_id, task.resolution_note) == ('done', space.member.id, 'Als geprüft bestätigt')
    job = _job(job_id)
    assert job.last_reviewed_by_id == space.member.id
    assert timedelta(days=89) < _aware(job.review_due_at) - datetime.now(timezone.utc) <= timedelta(days=90)
    review = client.get(f'/api/v1/portal/documents/{job_id}').json()['review']
    assert review['overdue'] is False and review['last_reviewed_by'] == space.member.username

    # A user's own note wins over the default one.
    _set_due(job_id, datetime.now(timezone.utc) - timedelta(days=1))
    document_freshness_pass()
    second = _review_tasks(job_id)[1]
    assert second.status == 'open'
    patched = member.patch(
        f'/api/v1/portal/review-tasks/{second.id}', json={'status': 'done', 'resolution_note': 'Tagegeld geprüft'}
    )
    assert patched.json()['resolution_note'] == 'Tagegeld geprüft'
    assert _aware(_job(job_id).review_due_at) > datetime.now(timezone.utc)


def test_dismiss_in_inbox_does_not_record_a_review():
    space = _space(interval=90)
    client = login_as(space.owner.username)
    job_id = make_doc(space)
    release(client, job_id)
    past = datetime.now(timezone.utc) - timedelta(days=2)
    _set_due(job_id, past)
    before = _job(job_id).last_reviewed_at
    document_freshness_pass()
    task_id = _review_tasks(job_id)[0].id
    assert client.patch(f'/api/v1/portal/review-tasks/{task_id}', json={'status': 'dismissed'}).status_code == 200
    job = _job(job_id)
    assert _aware(job.review_due_at) == past and job.last_reviewed_at == before


def test_done_on_task_of_superseded_version_only_closes_it():
    space = _space(interval=90)
    client = login_as(space.owner.username)
    v1 = make_doc(space)
    release(client, v1)
    v2 = make_doc(space, '# Reisekosten\n\nTagegeld 28 Euro.\n', previous=v1, version=2)
    release(client, v2)
    with _db() as db:
        task = ReviewTask(
            collection_id=space.id, kind='review_due', title='alt', cluster_key=f'review_due:{v1}', job_id=v1,
            details={}, first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
        )
        db.add(task)
        db.commit()
        task_id = task.id
    before = _job(v1)
    response = client.patch(f'/api/v1/portal/review-tasks/{task_id}', json={'status': 'done'})
    assert response.status_code == 200 and response.json()['status'] == 'done'
    after = _job(v1)
    assert (after.review_due_at, after.last_reviewed_at) == (before.review_due_at, before.last_reviewed_at)


def test_system_dismissed_task_does_not_block_the_same_date_again():
    space = _space()
    client = login_as(space.owner.username)
    job_id = make_doc(space)
    release(client, job_id)
    hook = _connection(space.owner.id, ['document.review_due'])
    due = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()
    assert client.put(f'/api/v1/portal/documents/{job_id}/review', json={'review_due_at': due}).status_code == 200
    document_freshness_pass()
    assert [task.status for task in _review_tasks(job_id)] == ['open']

    # Moved away: the tick dismisses the task itself ...
    assert client.put(f'/api/v1/portal/documents/{job_id}/review', json={'review_due_at': '2099-01-01'}).status_code == 200
    document_freshness_pass()
    first = _review_tasks(job_id)[0]
    assert (first.status, first.resolved_by_id, first.resolution_note) == ('dismissed', None, 'Prüfung nicht mehr fällig')

    # ... and moving it back to the same date reminds again (task + webhook).
    assert client.put(f'/api/v1/portal/documents/{job_id}/review', json={'review_due_at': due}).status_code == 200
    (result, _send) = document_freshness_pass()
    assert (job_id, space.id) in result['created']
    assert [task.status for task in _review_tasks(job_id)] == ['dismissed', 'open']
    assert [d.connection_id for d in _deliveries(job_id)] == [hook, hook]


def test_tick_cap_does_not_dismiss_tasks_of_jobs_beyond_it(monkeypatch):
    space = _space()
    client = login_as(space.owner.username)
    later = make_doc(space, filename='spaeter.pdf')
    release(client, later)
    earlier = make_doc(space, filename='frueher.pdf')
    release(client, earlier)
    _set_due(later, datetime.now(timezone.utc) - timedelta(days=1))
    document_freshness_pass()
    assert [task.status for task in _review_tasks(later)] == ['open']

    # Another due job sorts first and fills the (tiny) cap: `later` is still
    # due, so its task stays open.
    _set_due(earlier, datetime(2000, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(document_freshness, '_MAX_DUE_PER_TICK', 1)
    document_freshness_pass()
    assert [task.status for task in _review_tasks(later)] == ['open']

    # Below the cap a job that is no longer due is still dismissed.
    monkeypatch.setattr(document_freshness, '_MAX_DUE_PER_TICK', 2000)
    _set_due(later, None)
    document_freshness_pass()
    assert [task.status for task in _review_tasks(later)] == ['dismissed']
    _set_due(earlier, None)
    document_freshness_pass()


def test_review_writes_refuse_superseded_versions():
    space = _space(interval=30)
    client = login_as(space.owner.username)
    v1 = make_doc(space)
    release(client, v1)
    v2 = make_doc(space, '# Reisekosten\n\nTagegeld 26 Euro.\n', previous=v1, version=2)  # never released
    v3 = make_doc(space, '# Reisekosten\n\nTagegeld 28 Euro.\n', previous=v2, version=3)
    release(client, v3)
    member = login_as(space.member.username)
    for job_id in (v1, v2):
        put = member.put(f'/api/v1/portal/documents/{job_id}/review', json={'review_due_at': '2020-01-01'})
        assert put.status_code == 409, put.text
        assert member.post(f'/api/v1/portal/documents/{job_id}/review/confirm').status_code == 409
        assert _job(job_id).review_due_at is None
    assert member.put(f'/api/v1/portal/documents/{v3}/review', json={'review_due_at': '2020-01-01'}).status_code == 200
    assert member.post(f'/api/v1/portal/documents/{v3}/review/confirm').status_code == 200
    # Outsiders still get 404, not the 409 that would reveal the chain.
    outsider = login_as(space.outsider.username)
    assert outsider.put(f'/api/v1/portal/documents/{v1}/review', json={'review_due_at': None}).status_code == 404

    with _db() as db:
        assert document_freshness.has_newer_release(db, db.get(Job, v1)) is True
        assert document_freshness.has_newer_release(db, db.get(Job, v2)) is True
        assert document_freshness.has_newer_release(db, db.get(Job, v3)) is False
