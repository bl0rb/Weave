"""Quality signals -> review tasks (app/services/quality_tasks.py) and the
'Prüfaufgaben' portal surface (app/api/review_tasks.py).

Every test builds its own knowledge space(s) with unique slugs, so the
shared test.db never makes counts ambiguous; portal assertions are scoped by
`collection_id` or made as a user who only sees the test's own space.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.main import app
from app.models.models import (
    CollectionGrant,
    CollectionRole,
    Job,
    JobStatus,
    QualitySignal,
    ReviewTask,
    UserRole,
)
from app.services import quality_tasks, security
from app.services.quality_tasks import normalize_terms, terms_similarity
from tests.conftest import TestingSessionLocal, add_legacy_collection, client, create_test_user, login_as

TOKEN = 'quality-signal-test-token'
AUTH = {'Authorization': f'Bearer {TOKEN}'}


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    security.rate_limiter.reset()
    monkeypatch.setattr(settings, 'knowledge_ingest_api_token', TOKEN)
    yield


def _db():
    return TestingSessionLocal()


def _user(prefix: str, role: UserRole = UserRole.USER):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'{prefix}-{suffix}', email=f'{prefix}-{suffix}@example.com', role=role)


def _space(name: str = 'Reiseregeln'):
    owner, member, reader = _user('qowner'), _user('qmember'), _user('qreader')
    with _db() as db:
        collection = add_legacy_collection(
            db, owner_id=owner.id, slug=f'quality-{uuid.uuid4().hex[:12]}', name=name,
        )
        db.add(CollectionGrant(collection_id=collection.id, user_id=member.id, role=CollectionRole.MEMBER))
        db.add(CollectionGrant(collection_id=collection.id, user_id=reader.id, role=CollectionRole.READER))
        db.commit()
        collection_id, slug = collection.id, collection.slug
    return SimpleNamespace(id=collection_id, slug=slug, name=name, owner=owner, member=member, reader=reader)


def _job(space, filename: str = 'reisekosten.pdf', *, previous: str | None = None, version: int = 1) -> str:
    with _db() as db:
        job = Job(
            original_filename=filename,
            upload_path=f'/tmp/{filename}',
            status=JobStatus.FINISHED,
            owner_id=space.owner.id,
            previous_job_id=previous,
            document_version=version,
            result_markdown='# Doc\n',
            processing_info={'settings': {'collection_id': space.id}},
        )
        db.add(job)
        db.commit()
        return job.id


def _post(*signals, headers=AUTH):
    return client.post(
        '/api/v1/quality/signals',
        json={'signals': [{'signal_key': key, 'payload': payload} for key, payload in signals]},
        headers=headers,
    )


def _unanswered(question: str, *slugs: str, rewritten: str | None = None, created_at: str | None = None) -> dict:
    return {
        'kind': 'unanswered', 'question': question, 'guard_reason': 'no_context', 'collections': list(slugs),
        'bot_id': 'bot-1', 'created_at': created_at or datetime.now(timezone.utc).isoformat(),
        'rewritten_query': rewritten,
    }


def _feedback(rating, *, jobs=(), slugs=(), retracted=False, comment=None, question='Wie hoch ist das Tagegeld?') -> dict:
    return {
        'kind': 'feedback', 'rating': rating, 'comment': comment, 'question': question,
        'answer_excerpt': 'Das Tagegeld beträgt 24 Euro.', 'guard_reason': None,
        'sources': [
            {
                'source_job_id': job_id, 'collection': None, 'document_version': 1,
                'heading_path': ['Reisekosten', 'Tagegeld'], 'chunk_index': 3, 'chunk_id': 7,
                'original_filename': 'reisekosten.pdf',
            }
            for job_id in jobs
        ],
        'collections': list(slugs), 'bot_id': 'bot-1',
        'created_at': datetime.now(timezone.utc).isoformat(), 'retracted': retracted,
    }


def _tasks(collection_id: str, kind: str | None = None) -> list[ReviewTask]:
    with _db() as db:
        query = select(ReviewTask).where(ReviewTask.collection_id == collection_id)
        if kind:
            query = query.where(ReviewTask.kind == kind)
        return list(db.scalars(query.order_by(ReviewTask.created_at, ReviewTask.id)).all())


def _key(prefix: str = 'feedback') -> str:
    return f'{prefix}:{uuid.uuid4()}'


# --- normalisation ------------------------------------------------------------


def test_normalize_terms_folds_umlauts_drops_stopwords_and_stems():
    assert normalize_terms('Wie hoch ist die Erstattung für Übernachtungen?') == normalize_terms(
        'erstattung uebernachtung hoch'
    )
    assert normalize_terms('Was ist das?') == []
    assert normalize_terms('Urlaubstage, Urlaubstagen; URLAUBSTAG!') == ['urlaubstag']
    assert normalize_terms('30 Tage Frist') == ['30', 'frist', 'tag']
    assert 'how' not in normalize_terms('How do I book a trip?')


def test_terms_similarity_jaccard_and_containment():
    assert terms_similarity({'a', 'b'}, {'a', 'b'}) == 1.0
    assert terms_similarity({'a', 'b', 'c'}, {'a', 'b', 'd'}) == 0.5
    assert terms_similarity({'a', 'b'}, {'a', 'b', 'c', 'd', 'e'}) == 0.5  # contained, 2 tokens
    assert terms_similarity({'a'}, {'a', 'b', 'c'}) == 0.0  # contained but only 1 token
    assert terms_similarity({'a', 'b', 'c'}, {'d', 'e'}) == 0.0
    assert terms_similarity(set(), {'a'}) == 0.0


# --- service token ------------------------------------------------------------


def test_signal_intake_requires_the_service_token(monkeypatch):
    space = _space()
    signal = ('unanswered:1', _unanswered('Tagegeld Ausland', space.slug))
    assert _post(signal, headers={}).status_code == 401
    assert _post(signal, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert _post(signal, headers={'Authorization': f'Basic {TOKEN}'}).status_code == 401
    # A logged-in admin session is not enough: service token only.
    admin = _user('qadmin', UserRole.ADMIN)
    session = login_as(admin.username)
    assert session.post('/api/v1/quality/signals', json={'signals': []}).status_code == 401
    monkeypatch.setattr(settings, 'knowledge_ingest_api_token', '')
    assert _post(signal, headers={'Authorization': 'Bearer '}).status_code == 503
    assert _tasks(space.id) == []


def test_signal_batch_validation():
    too_many = [(f'unanswered:{index}', {'kind': 'unanswered'}) for index in range(501)]
    assert _post(*too_many).status_code == 422
    assert _post(('bad key with spaces', {'kind': 'unanswered'})).status_code == 422
    assert _post(('x' * 92, {'kind': 'unanswered'})).status_code == 422
    assert _post(('feedback:abc|suffix', {'kind': 'unanswered'})).status_code == 422
    assert client.post('/api/v1/quality/signals', json={}, headers=AUTH).status_code == 422
    assert _post().json() == {'accepted': 0}


def test_malformed_signals_are_skipped_not_failing_the_batch():
    space = _space()
    response = _post(
        (_key('x'), {'kind': 'mystery'}),
        (_key(), {'kind': 'feedback', 'rating': 'meh', 'collections': [space.slug]}),
        (_key('unanswered'), _unanswered('Reisekosten Ausland Pauschale', space.slug)),
    )
    assert response.status_code == 200
    assert response.json() == {'accepted': 1}
    assert len(_tasks(space.id)) == 1


# --- knowledge gaps -------------------------------------------------------------


def test_unanswered_questions_cluster_into_one_knowledge_gap():
    space = _space()
    response = _post(
        ('unanswered:101', _unanswered('Wie hoch ist das Tagegeld bei Dienstreisen ins Ausland?', space.slug, rewritten='Tagegeld Ausland')),
        ('unanswered:102', _unanswered('Tagegeld Dienstreise Ausland Höhe', space.slug)),
        ('unanswered:103', _unanswered('Wer genehmigt Homeoffice?', space.slug)),
        ('unanswered:104', _unanswered('Wie hoch ist das Tagegeld bei Dienstreisen ins Ausland?', space.slug)),
    )
    assert response.json() == {'accepted': 4}
    tasks = _tasks(space.id, 'knowledge_gap')
    assert len(tasks) == 2
    gap, other = tasks
    assert gap.title == 'Wie hoch ist das Tagegeld bei Dienstreisen ins Ausland?'
    assert gap.occurrence_count == 3
    assert gap.status == 'open'
    assert gap.details['tokens'] == normalize_terms(gap.title)
    questions = {entry['text']: entry['count'] for entry in gap.details['questions']}
    assert questions == {
        'Wie hoch ist das Tagegeld bei Dienstreisen ins Ausland?': 2,
        'Tagegeld Dienstreise Ausland Höhe': 1,
    }
    assert gap.details['rewritten_queries'] == ['Tagegeld Ausland']
    assert other.occurrence_count == 1 and other.title == 'Wer genehmigt Homeoffice?'


def test_unanswered_signal_is_idempotent_and_attributed_per_collection():
    first, second = _space(), _space()
    signal = ('unanswered:201', _unanswered('Kilometerpauschale Privatwagen', first.slug, second.slug, 'unknown-space'))
    assert _post(signal).json() == {'accepted': 1}
    assert _post(signal).json() == {'accepted': 1}
    for space in (first, second):
        tasks = _tasks(space.id, 'knowledge_gap')
        assert len(tasks) == 1 and tasks[0].occurrence_count == 1
    with _db() as db:
        keys = sorted(db.scalars(select(QualitySignal.signal_key).where(QualitySignal.signal_key.like('unanswered:201|%'))).all())
    assert keys == sorted([f'unanswered:201|{first.id}', f'unanswered:201|{second.id}'])


def test_unattributable_unanswered_signal_is_accepted_and_dropped():
    key = _key('unanswered')
    assert _post((key, _unanswered('Irgendwas Neues', 'no-such-space'))).json() == {'accepted': 1}
    assert _post((_key('unanswered'), _unanswered('Irgendwas', ))).json() == {'accepted': 1}
    with _db() as db:
        assert db.scalars(select(QualitySignal).where(QualitySignal.signal_key.like(f'{key}%'))).all() == []


def test_closed_task_is_never_reopened_by_a_new_signal():
    space = _space()
    _post(('unanswered:301-' + uuid.uuid4().hex[:6], _unanswered('Parkplatz Firmengelände Regeln', space.slug)))
    (task,) = _tasks(space.id)
    session = login_as(space.member.username)
    assert session.patch(f'/api/v1/portal/review-tasks/{task.id}', json={'status': 'done'}).status_code == 200
    _post(('unanswered:302-' + uuid.uuid4().hex[:6], _unanswered('Regeln Parkplatz Firmengelände', space.slug)))
    tasks = _tasks(space.id)
    assert [item.status for item in tasks] == ['done', 'open']
    assert tasks[0].occurrence_count == 1 and tasks[1].occurrence_count == 1


# --- answer feedback ------------------------------------------------------------


def test_negative_feedback_creates_one_task_per_document_lineage():
    space = _space()
    v1 = _job(space, 'reisekosten.pdf')
    v2 = _job(space, 'reisekosten-v2.pdf', previous=v1, version=2)
    other = _job(space, 'urlaub.pdf')
    first, second = _key(), _key()
    _post(
        (first, _feedback('wrong', jobs=[v1, other], comment='Seit April 28 Euro')),
        (second, _feedback('outdated', jobs=[v2, v2])),
    )
    tasks = _tasks(space.id, 'answer_feedback')
    assert len(tasks) == 2
    lineage = next(task for task in tasks if task.cluster_key == f'feedback:{v1}')
    assert lineage.job_id == v2  # newest cited version
    assert lineage.title == 'Rückmeldungen zu „reisekosten-v2.pdf“'
    assert lineage.occurrence_count == 2
    assert lineage.details['counts'] == {'wrong': 1, 'outdated': 1}
    assert {sample['rating'] for sample in lineage.details['samples']} == {'wrong', 'outdated'}
    sample = next(sample for sample in lineage.details['samples'] if sample['rating'] == 'wrong')
    assert sample['comment'] == 'Seit April 28 Euro'
    assert sample['heading_path'] == ['Reisekosten', 'Tagegeld']
    assert sample['answer_excerpt'] == 'Das Tagegeld beträgt 24 Euro.'
    single = next(task for task in tasks if task.cluster_key == f'feedback:{other}')
    assert single.job_id == other and single.occurrence_count == 1

    # Retracting one signal recomputes; retracting the last dismisses.
    _post((second, _feedback('outdated', jobs=[v2], retracted=True)))
    lineage = next(task for task in _tasks(space.id, 'answer_feedback') if task.id == lineage.id)
    assert lineage.status == 'open'
    assert lineage.occurrence_count == 1
    assert lineage.details['counts'] == {'wrong': 1, 'outdated': 0}
    _post((first, {'kind': 'feedback', 'retracted': True}))
    for task in _tasks(space.id, 'answer_feedback'):
        assert task.status == 'dismissed'
        assert task.resolution_note == 'zurückgezogen'
        assert task.occurrence_count == 0
        assert task.resolved_at is not None and task.resolved_by_id is None


def test_helpful_feedback_is_stored_without_task_and_rating_change_dismisses():
    space = _space()
    job = _job(space)
    helpful, changed = _key(), _key()
    _post((helpful, _feedback('helpful', jobs=[job])))
    assert _tasks(space.id) == []
    _post((changed, _feedback('wrong', jobs=[job])))
    (task,) = _tasks(space.id)
    assert task.occurrence_count == 1
    _post((changed, _feedback('helpful', jobs=[job])))
    (task,) = _tasks(space.id)
    assert task.status == 'dismissed' and task.occurrence_count == 0
    with _db() as db:
        rows = db.scalars(select(QualitySignal).where(QualitySignal.job_id == job)).all()
    assert sorted(row.rating for row in rows) == ['helpful', 'helpful']


def test_rating_that_becomes_negative_again_opens_a_new_task():
    space = _space()
    job = _job(space)
    key = _key()
    _post((key, _feedback('wrong', jobs=[job])))
    _post((key, _feedback('helpful', jobs=[job])))
    _post((key, _feedback('wrong', jobs=[job], comment='doch falsch')))
    tasks = _tasks(space.id, 'answer_feedback')
    assert [(task.status, task.occurrence_count) for task in tasks] == [('dismissed', 0), ('open', 1)]
    assert tasks[1].details['counts'] == {'wrong': 1, 'outdated': 0}
    assert tasks[1].details['samples'][0]['comment'] == 'doch falsch'
    with _db() as db:
        (row,) = db.scalars(select(QualitySignal).where(QualitySignal.signal_key == f'{key}|{job}')).all()
    assert row.task_id == tasks[1].id
    # Resending the now live signal keeps it on the new task.
    _post((key, _feedback('wrong', jobs=[job], comment='doch falsch')))
    assert [(task.status, task.occurrence_count) for task in _tasks(space.id)] == [('dismissed', 0), ('open', 1)]


def test_resend_after_owner_closed_keeps_task_closed_until_signal_is_live_again():
    space = _space()
    job = _job(space)
    key = _key()
    _post((key, _feedback('wrong', jobs=[job])))
    (task,) = _tasks(space.id)
    session = login_as(space.member.username)
    assert session.patch(f'/api/v1/portal/review-tasks/{task.id}', json={'status': 'done'}).status_code == 200
    _post((key, _feedback('wrong', jobs=[job])))  # outbox retry: no new task
    assert [item.status for item in _tasks(space.id)] == ['done']
    _post((key, _feedback('outdated', jobs=[job])))  # still live: the owner handled it
    assert [item.status for item in _tasks(space.id)] == ['done']
    _post((key, {'kind': 'feedback', 'retracted': True}))
    _post((key, _feedback('wrong', jobs=[job])))  # live again: new task
    tasks = _tasks(space.id)
    assert [(item.status, item.occurrence_count) for item in tasks] == [('done', 1), ('open', 1)]


def test_target_named_again_after_being_dropped_opens_a_new_task():
    space = _space()
    first, second = _job(space, 'a.pdf'), _job(space, 'b.pdf')
    key = _key()
    _post((key, _feedback('wrong', jobs=[first])))
    _post((key, _feedback('wrong', jobs=[second])))
    _post((key, _feedback('wrong', jobs=[first])))
    tasks = _tasks(space.id)
    by_job = sorted((task.job_id == first, task.status, task.occurrence_count) for task in tasks)
    assert by_job == [(False, 'dismissed', 0), (True, 'dismissed', 0), (True, 'open', 1)]


def test_retracting_last_signal_of_in_progress_task_clears_its_details():
    space = _space()
    job = _job(space)
    key = _key()
    _post((key, _feedback('wrong', jobs=[job], comment='geheim', question='Geheime Frage?')))
    (task,) = _tasks(space.id)
    session = login_as(space.member.username)
    url = f'/api/v1/portal/review-tasks/{task.id}'
    assert session.patch(url, json={'status': 'in_progress'}).status_code == 200
    _post((key, {'kind': 'feedback', 'retracted': True}))
    (task,) = _tasks(space.id)
    assert task.status == 'in_progress' and task.occurrence_count == 0
    assert task.details == {'counts': {'wrong': 0, 'outdated': 0}, 'samples': []}
    body = session.get(url).json()
    assert 'geheim' not in repr(body) and 'Geheime Frage' not in repr(body)
    # A renewed negative rating feeds the still active task again.
    _post((key, _feedback('outdated', jobs=[job])))
    (task,) = _tasks(space.id)
    assert task.status == 'in_progress' and task.occurrence_count == 1
    assert task.details['counts'] == {'wrong': 0, 'outdated': 1}


def test_retracting_last_question_of_in_progress_gap_task_clears_questions():
    space = _space()
    key = 'unanswered:' + uuid.uuid4().hex
    _post((key, _unanswered('Parkplatz Firmengelände Regeln', space.slug, rewritten='parkplatz regeln')))
    (task,) = _tasks(space.id)
    tokens = task.details['tokens']
    session = login_as(space.member.username)
    assert session.patch(f'/api/v1/portal/review-tasks/{task.id}', json={'status': 'in_progress'}).status_code == 200
    _post((key, _unanswered('Parkplatz Firmengelände Regeln', space.slug) | {'retracted': True}))
    (task,) = _tasks(space.id)
    assert task.status == 'in_progress' and task.occurrence_count == 0
    assert task.details == {'tokens': tokens, 'questions': [], 'rewritten_queries': []}


def test_feedback_without_resolvable_sources_goes_to_the_spaces():
    space = _space()
    _post(
        (_key(), _feedback('wrong', slugs=[space.slug])),
        (_key(), _feedback('outdated', jobs=[str(uuid.uuid4())], slugs=[space.slug])),
    )
    (task,) = _tasks(space.id, 'answer_feedback')
    assert task.cluster_key == 'feedback:none'
    assert task.job_id is None
    assert task.occurrence_count == 2
    assert task.details['counts'] == {'wrong': 1, 'outdated': 1}


def test_payload_is_whitelisted_no_identity_is_stored():
    space = _space()
    key = _key()
    payload = _feedback('wrong', slugs=[space.slug]) | {'user_id': 'u-1', 'email': 'a@example.com', 'team': 'HR'}
    payload['sources'] = [{'source_job_id': None, 'user': 'someone'}]
    _post((key, payload))
    with _db() as db:
        row = db.scalar(select(QualitySignal).where(QualitySignal.signal_key == f'{key}|{space.id}'))
    assert row is not None
    stored = repr(row.payload)
    for leaked in ('u-1', 'a@example.com', 'HR', 'someone', 'user_id', 'email'):
        assert leaked not in stored


def test_signal_moving_to_another_document_retracts_the_old_target():
    space = _space()
    first, second = _job(space, 'a.pdf'), _job(space, 'b.pdf')
    key = _key()
    _post((key, _feedback('wrong', jobs=[first])))
    _post((key, _feedback('wrong', jobs=[second])))
    tasks = {task.job_id: task for task in _tasks(space.id)}
    assert tasks[first].status == 'dismissed'
    assert tasks[second].status == 'open' and tasks[second].occurrence_count == 1


# --- generic primitives (reused by review_due / contradiction) -------------------


def test_ensure_task_dedups_active_tasks_and_close_active_tasks():
    space = _space()
    job = _job(space)
    with _db() as db:
        task, created = quality_tasks.ensure_task(
            db, collection_id=space.id, kind='review_due', cluster_key=f'review_due:{job}', title='Prüfung fällig',
            job_id=job, details={'overdue': False},
        )
        again, created_again = quality_tasks.ensure_task(
            db, collection_id=space.id, kind='review_due', cluster_key=f'review_due:{job}', title='x',
        )
        assert created and not created_again and again.id == task.id
        quality_tasks.refresh_task(db, task)  # not signal-backed: untouched
        assert task.occurrence_count == 1 and task.details == {'overdue': False}
        assert quality_tasks.close_active_tasks(db, kind='review_due', job_id=job, user_id=space.owner.id) == 1
        assert task.status == 'done' and task.resolved_by_id == space.owner.id
        third, created_third = quality_tasks.ensure_task(
            db, collection_id=space.id, kind='review_due', cluster_key=f'review_due:{job}', title='Prüfung fällig',
        )
        assert created_third and third.id != task.id
        db.commit()


# --- portal ----------------------------------------------------------------------


def _seed_tasks(space):
    job = _job(space)
    _post(
        ('unanswered:' + uuid.uuid4().hex, _unanswered('Dienstwagen private Nutzung', space.slug)),
        (_key(), _feedback('wrong', jobs=[job], comment='falsch')),
    )
    return job


def test_portal_visibility_member_owner_admin_but_not_reader_or_outsider():
    space = _space()
    _seed_tasks(space)
    task_ids = {task.id for task in _tasks(space.id)}
    some_id = next(iter(task_ids))
    for user in (space.member, space.owner):
        session = login_as(user.username)
        listing = session.get('/api/v1/portal/review-tasks', params={'collection_id': space.id})
        assert listing.status_code == 200, listing.text
        assert {item['id'] for item in listing.json()['items']} == task_ids
        assert session.get(f'/api/v1/portal/review-tasks/{some_id}').status_code == 200
    admin = login_as(_user('qadmin', UserRole.ADMIN).username)
    assert admin.get(f'/api/v1/portal/review-tasks/{some_id}').status_code == 200
    assert admin.get('/api/v1/portal/review-tasks', params={'collection_id': space.id}).json()['total'] == 2
    outsider = _user('qoutsider')
    for user in (space.reader, outsider):
        session = login_as(user.username)
        listing = session.get('/api/v1/portal/review-tasks')
        assert listing.status_code == 200
        assert task_ids.isdisjoint({item['id'] for item in listing.json()['items']})
        assert session.get(f'/api/v1/portal/review-tasks/{some_id}').status_code == 404
        assert session.patch(f'/api/v1/portal/review-tasks/{some_id}', json={'status': 'done'}).status_code == 404
        assert session.get('/api/v1/portal/review-tasks/count').json()['open'] == 0
        assert session.get('/api/v1/portal/quality/summary', params={'collection_id': space.id}).status_code == 404
    assert login_as(space.member.username).get('/api/v1/portal/review-tasks/does-not-exist').status_code == 404
    assert TestClient(app).get('/api/v1/portal/review-tasks').status_code == 401


def test_portal_list_count_filters_sorting_and_output_shape():
    space = _space()
    job = _seed_tasks(space)
    session = login_as(space.member.username)
    count = session.get('/api/v1/portal/review-tasks/count').json()
    assert count == {'open': 2, 'by_kind': {'knowledge_gap': 1, 'answer_feedback': 1, 'review_due': 0, 'contradiction': 0}}

    listing = session.get('/api/v1/portal/review-tasks', params={'kind': 'answer_feedback'}).json()
    assert listing['total'] == 1 and listing['page'] == 1 and listing['page_size'] == 25
    item = listing['items'][0]
    assert item['kind'] == 'answer_feedback' and item['status'] == 'open'
    assert item['collection'] == {'id': space.id, 'name': space.name, 'slug': space.slug}
    assert item['document'] == {'job_id': job, 'title': 'reisekosten.pdf', 'document_version': 1}
    assert item['related_document'] is None and item['assignee'] is None
    assert item['details']['counts'] == {'wrong': 1, 'outdated': 0}
    assert item['occurrence_count'] == 1 and item['first_seen_at'] and item['last_seen_at']

    gap_id = next(task.id for task in _tasks(space.id, 'knowledge_gap'))
    assert session.patch(f'/api/v1/portal/review-tasks/{gap_id}', json={'status': 'in_progress'}).status_code == 200
    statuses = [entry['status'] for entry in session.get('/api/v1/portal/review-tasks').json()['items']]
    assert statuses == ['open', 'in_progress']
    assert session.get('/api/v1/portal/review-tasks', params={'status': 'in_progress'}).json()['total'] == 1
    assert session.get('/api/v1/portal/review-tasks', params={'status': 'done'}).json()['total'] == 0
    paged = session.get('/api/v1/portal/review-tasks', params={'page': 2, 'page_size': 1}).json()
    assert paged['total'] == 2 and [entry['status'] for entry in paged['items']] == ['in_progress']
    assert session.get('/api/v1/portal/review-tasks', params={'status': 'bogus'}).status_code == 422
    assert session.get('/api/v1/portal/review-tasks', params={'kind': 'bogus'}).status_code == 422
    assert session.get('/api/v1/portal/review-tasks', params={'page_size': 101}).status_code == 422


def test_portal_patch_assign_resolve_and_reopen_rules():
    space = _space()
    _seed_tasks(space)
    gap = _tasks(space.id, 'knowledge_gap')[0]
    session = login_as(space.member.username)
    url = f'/api/v1/portal/review-tasks/{gap.id}'

    taken = session.patch(url, json={'status': 'in_progress', 'assignee_id': space.member.id})
    assert taken.status_code == 200, taken.text
    assert taken.json()['assignee'] == {'id': space.member.id, 'username': space.member.username}
    assert taken.json()['status'] == 'in_progress' and taken.json()['resolved_at'] is None

    assert session.patch(url, json={'assignee_id': space.reader.id}).status_code == 422
    assert session.patch(url, json={'assignee_id': 'no-such-user'}).status_code == 422
    assert session.patch(url, json={'status': 'closed'}).status_code == 422
    assert session.patch(url, json={'resolution_note': 'x' * 2001}).status_code == 422
    assert session.patch(url, json={'assignee_id': space.owner.id}).json()['assignee']['id'] == space.owner.id
    assert session.patch(url, json={'assignee_id': None}).json()['assignee'] is None

    done = session.patch(url, json={'status': 'done', 'resolution_note': '  Abschnitt ergänzt  '})
    assert done.status_code == 200
    assert done.json()['status'] == 'done' and done.json()['resolution_note'] == 'Abschnitt ergänzt'
    assert done.json()['resolved_at'] is not None
    with _db() as db:
        assert db.get(ReviewTask, gap.id).resolved_by_id == space.member.id

    # Reopen is allowed while no other active task covers the cluster ...
    reopened = session.patch(url, json={'status': 'open'})
    assert reopened.status_code == 200 and reopened.json()['resolved_at'] is None
    assert session.patch(url, json={'status': 'dismissed'}).status_code == 200
    # ... and refused once a new signal opened a fresh one.
    _post(('unanswered:' + uuid.uuid4().hex, _unanswered('Dienstwagen private Nutzung', space.slug)))
    assert session.patch(url, json={'status': 'open'}).status_code == 409
    assert session.patch(url, json={'resolution_note': None}).json()['resolution_note'] is None


def test_quality_summary_counts_visible_spaces_once_per_signal():
    space, hidden = _space(), _space()
    job_a, job_b = _job(space, 'a.pdf'), _job(space, 'b.pdf')
    _post(
        (_key(), _feedback('wrong', jobs=[job_a, job_b])),  # one signal, two documents
        (_key(), _feedback('outdated', jobs=[job_b])),
        (_key(), _feedback('helpful', jobs=[job_a])),
        (_key(), _feedback('wrong', jobs=[job_a], retracted=True)),
        ('unanswered:' + uuid.uuid4().hex, _unanswered('Fahrradleasing Bedingungen', space.slug)),
        ('unanswered:' + uuid.uuid4().hex, _unanswered('Fahrradleasing Bedingungen', space.slug, hidden.slug)),
        (_key(), _feedback('wrong', slugs=[hidden.slug])),
    )
    session = login_as(space.member.username)
    summary = session.get('/api/v1/portal/quality/summary')
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body['feedback'] == {'helpful': 1, 'wrong': 1, 'outdated': 1}
    assert body['unanswered'] == 2
    assert body['tasks'] == {'open': 3, 'in_progress': 0}
    assert body['documents'] == [
        {'job_id': job_b, 'title': 'b.pdf', 'helpful': 0, 'wrong': 1, 'outdated': 1},
        {'job_id': job_a, 'title': 'a.pdf', 'helpful': 1, 'wrong': 1, 'outdated': 0},
    ]
    scoped = session.get('/api/v1/portal/quality/summary', params={'collection_id': space.id}).json()
    assert scoped == body
    assert session.get('/api/v1/portal/quality/summary', params={'collection_id': hidden.id}).status_code == 404

    # Signals older than 90 days drop out of the counts.
    with _db() as db:
        for row in db.scalars(select(QualitySignal).where(QualitySignal.collection_id == space.id)).all():
            row.created_at = datetime.now(timezone.utc) - timedelta(days=91)
        db.commit()
    old = session.get('/api/v1/portal/quality/summary', params={'collection_id': space.id}).json()
    assert old['feedback'] == {'helpful': 0, 'wrong': 0, 'outdated': 0}
    assert old['unanswered'] == 0 and old['documents'] == []
    assert old['tasks'] == {'open': 3, 'in_progress': 0}
