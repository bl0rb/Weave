"""Possible contradictions from Weave-Knowledge: POST
/api/v1/quality/contradictions -> `contradiction` Prüfaufgaben
(app/services/quality_tasks.ingest_contradictions)."""

import uuid

from sqlalchemy import select

from app.models.models import QualitySignal, ReviewTask
from tests.conftest import client, login_as
from tests.test_quality_review_tasks import AUTH, _db, _job, _setup, _space  # noqa: F401

URL = '/api/v1/quality/contradictions'


def _report(space, source: str, other: str, *, excerpt='Tagegeld: 24 Euro.', other_excerpt='Tagegeld: 28 Euro.', **extra):
    return {
        'collection_slug': space.slug, 'source_job_id': source, 'other_source_job_id': other,
        'similarity': 0.91, 'heading_path': ['Reisekosten', 'Tagegeld'], 'other_heading_path': ['Tagegeld'],
        'excerpt': excerpt, 'other_excerpt': other_excerpt, 'facts': ['24 Euro'], 'other_facts': ['28 Euro'],
        **extra,
    }


def _post(*reports, headers=AUTH):
    return client.post(URL, json={'reports': list(reports)}, headers=headers)


def _contradiction_tasks(collection_id: str) -> list[ReviewTask]:
    with _db() as db:
        return list(db.scalars(
            select(ReviewTask).where(ReviewTask.collection_id == collection_id, ReviewTask.kind == 'contradiction')
            .order_by(ReviewTask.created_at, ReviewTask.id)
        ).all())


def test_requires_service_token(monkeypatch):
    space = _space()
    a, b = _job(space, 'a.pdf'), _job(space, 'b.pdf')
    assert _post(_report(space, a, b), headers={}).status_code == 401
    assert _post(_report(space, a, b), headers={'Authorization': 'Bearer wrong'}).status_code == 401
    owner = login_as(space.owner.username)
    assert owner.post(URL, json={'reports': [_report(space, a, b)]}).status_code == 401
    from app.core.config import settings

    monkeypatch.setattr(settings, 'knowledge_ingest_api_token', '')
    assert _post(_report(space, a, b)).status_code == 503
    assert _contradiction_tasks(space.id) == []


def test_one_task_per_unordered_pair_with_excerpt_pairs():
    space = _space()
    a, b = _job(space, 'reisekosten.pdf'), _job(space, 'reiserichtlinie.pdf')
    response = _post(_report(space, a, b))
    assert response.status_code == 200 and response.json() == {'accepted': 1}, response.text
    tasks = _contradiction_tasks(space.id)
    assert len(tasks) == 1
    task = tasks[0]
    low, high = sorted((a, b))
    assert (task.cluster_key, task.job_id, task.related_job_id, task.status) == (f'contradiction:{low}:{high}', low, high, 'open')
    assert '„reisekosten.pdf“' in task.title and '„reiserichtlinie.pdf“' in task.title
    pair = task.details['pairs'][0]
    # Oriented to the task: `excerpt` belongs to job_id (the smaller id).
    expected = ('Tagegeld: 24 Euro.', 'Tagegeld: 28 Euro.') if low == a else ('Tagegeld: 28 Euro.', 'Tagegeld: 24 Euro.')
    assert (pair['excerpt'], pair['other_excerpt']) == expected
    assert pair['job_id'] == low and pair['other_job_id'] == high
    assert task.details['max_similarity'] == 0.91

    # Same pair reversed, same texts: idempotent (no new signal, no new task).
    reversed_report = _report(space, b, a, excerpt='Tagegeld: 28 Euro.', other_excerpt='Tagegeld: 24 Euro.')
    assert _post(reversed_report).json() == {'accepted': 1}
    tasks = _contradiction_tasks(space.id)
    assert len(tasks) == 1 and tasks[0].occurrence_count == 1

    # A new excerpt pair for the same documents joins the task.
    assert _post(_report(space, a, b, excerpt='Frist: 30 Tage.', other_excerpt='Frist: 60 Tage.', similarity=0.95)).json() == {'accepted': 1}
    task = _contradiction_tasks(space.id)[0]
    assert task.occurrence_count == 2 and len(task.details['pairs']) == 2
    assert task.details['max_similarity'] == 0.95
    with _db() as db:
        keys = db.scalars(select(QualitySignal.signal_key).where(QualitySignal.task_id == task.id)).all()
    assert len(keys) == 2 and all(key.startswith(f'contradiction:{low}:{high}:') for key in keys)


def test_details_keep_latest_five_pairs():
    space = _space()
    a, b = _job(space, 'a.pdf'), _job(space, 'b.pdf')
    _post(*[_report(space, a, b, excerpt=f'Wert {i} Euro', other_excerpt=f'Wert {i + 1} Euro') for i in range(7)])
    task = _contradiction_tasks(space.id)[0]
    assert task.occurrence_count == 7 and len(task.details['pairs']) == 5


def test_unknown_space_jobs_or_foreign_jobs_are_ignored():
    space, other_space = _space(), _space()
    a, b = _job(space, 'a.pdf'), _job(space, 'b.pdf')
    foreign = _job(other_space, 'c.pdf')
    response = _post(
        _report(space, a, str(uuid.uuid4())),
        {**_report(space, a, b), 'collection_slug': f'missing-{uuid.uuid4().hex[:6]}'},
        _report(space, a, foreign),
        _report(space, a, a),
        _report(other_space, a, b),
    )
    assert response.status_code == 200 and response.json() == {'accepted': 0}
    assert _contradiction_tasks(space.id) == [] and _contradiction_tasks(other_space.id) == []


def test_validation_and_trimming():
    space = _space()
    a, b = _job(space, 'a.pdf'), _job(space, 'b.pdf')
    assert _post({'collection_slug': space.slug, 'source_job_id': a}).status_code == 422
    assert client.post(URL, json={'reports': [_report(space, a, b)] * 501}, headers=AUTH).status_code == 422
    long_text = 'x' * 5000
    assert _post(_report(space, a, b, excerpt=long_text, similarity='hoch')).status_code == 422
    assert _post(_report(space, a, b, excerpt=long_text, similarity=None, facts=[f'{i} Euro' for i in range(50)])).json() == {'accepted': 1}
    pair = _contradiction_tasks(space.id)[0].details['pairs'][0]
    prefix = '' if pair['job_id'] == a else 'other_'
    assert len(pair[f'{prefix}excerpt']) == 2000
    assert len(pair[f'{prefix}facts']) == 20
    assert pair['similarity'] is None


def test_dismissed_pair_is_not_reopened_by_the_same_excerpts_but_by_new_ones():
    space = _space()
    a, b = _job(space, 'a.pdf'), _job(space, 'b.pdf')
    _post(_report(space, a, b))
    task = _contradiction_tasks(space.id)[0]
    member = login_as(space.member.username)
    assert member.patch(f'/api/v1/portal/review-tasks/{task.id}', json={'status': 'dismissed'}).status_code == 200
    _post(_report(space, a, b))
    assert [t.status for t in _contradiction_tasks(space.id)] == ['dismissed']
    _post(_report(space, a, b, excerpt='Neu: 26 Euro', other_excerpt='Neu: 27 Euro'))
    assert [t.status for t in _contradiction_tasks(space.id)] == ['dismissed', 'open']


def test_contradiction_tasks_visible_to_members_only_and_not_in_feedback_summary():
    space = _space()
    a, b = _job(space, 'a.pdf'), _job(space, 'b.pdf')
    _post(_report(space, a, b))
    member = login_as(space.member.username)
    listed = member.get('/api/v1/portal/review-tasks', params={'collection_id': space.id, 'kind': 'contradiction'}).json()
    assert len(listed['items']) == 1
    item = listed['items'][0]
    assert {item['document']['job_id'], item['related_document']['job_id']} == {a, b}
    reader = login_as(space.reader.username)
    assert reader.get(f'/api/v1/portal/review-tasks/{item["id"]}').status_code == 404
    summary = member.get('/api/v1/portal/quality/summary', params={'collection_id': space.id}).json()
    assert summary['feedback'] == {'helpful': 0, 'wrong': 0, 'outdated': 0} and summary['unanswered'] == 0
    assert summary['tasks']['open'] == 1
