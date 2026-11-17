"""Topic subscriptions, change notifications and the ad-hoc change search
(app/services/change_feed.py, app/api/document_freshness.py).

Keywords carry a per-test random suffix so releases of other tests in the
shared test.db never match; feed assertions are per user.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select, update

from app.models.models import ChangeNotification, DocumentRelease, KnowledgeWithdrawal, TopicSubscription, User
from app.services import change_feed
from tests.conftest import login_as
from tests.test_document_freshness import _db, _setup, _space, _user, make_doc, release  # noqa: F401


def _word() -> str:
    return f'reiseregel{uuid.uuid4().hex[:8]}'


def _run() -> int:
    with _db() as db:
        return change_feed.process_pending_releases(db)


def _notifications(user_id: str) -> list[ChangeNotification]:
    with _db() as db:
        return list(db.scalars(
            select(ChangeNotification).where(ChangeNotification.user_id == user_id).order_by(ChangeNotification.created_at)
        ).all())


# --- subscriptions --------------------------------------------------------------------


def test_subscription_crud_validation_and_idor():
    space = _space()
    reader = login_as(space.reader.username)
    word = _word()
    created = reader.post('/api/v1/portal/subscriptions', json={'query': f'Was gilt für {word}?', 'collection_id': space.id})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body['terms'] == [word] and body['collection']['id'] == space.id
    assert body['label'] == f'Was gilt für {word}?'
    space_only = reader.post('/api/v1/portal/subscriptions', json={'collection_id': space.id, 'label': 'Alles'})
    assert space_only.status_code == 201 and space_only.json()['label'] == 'Alles'
    assert space_only.json()['terms'] == []

    assert reader.post('/api/v1/portal/subscriptions', json={'collection_id': space.id}).status_code == 409  # duplicate
    assert reader.post('/api/v1/portal/subscriptions', json={}).status_code == 422
    assert reader.post('/api/v1/portal/subscriptions', json={'query': '  '}).status_code == 422
    assert reader.post('/api/v1/portal/subscriptions', json={'query': 'was ist das'}).status_code == 422  # stopwords only
    assert reader.post('/api/v1/portal/subscriptions', json={'query': 'x' * 201}).status_code == 422
    other_space = _space()
    assert reader.post('/api/v1/portal/subscriptions', json={'collection_id': other_space.id}).status_code == 404
    assert reader.post('/api/v1/portal/subscriptions', json={'collection_id': str(uuid.uuid4())}).status_code == 404

    listed = reader.get('/api/v1/portal/subscriptions').json()['items']
    assert [item['id'] for item in listed] == [body['id'], space_only.json()['id']]

    outsider = login_as(space.outsider.username)
    assert outsider.get('/api/v1/portal/subscriptions').json()['items'] == []
    assert outsider.delete(f'/api/v1/portal/subscriptions/{body["id"]}').status_code == 404
    assert reader.delete(f'/api/v1/portal/subscriptions/{body["id"]}').status_code == 204
    assert reader.delete(f'/api/v1/portal/subscriptions/{body["id"]}').status_code == 404
    assert [item['id'] for item in reader.get('/api/v1/portal/subscriptions').json()['items']] == [space_only.json()['id']]


def test_public_space_can_be_subscribed_by_anyone():
    space = _space(public=True)
    outsider = login_as(space.outsider.username)
    assert outsider.post('/api/v1/portal/subscriptions', json={'collection_id': space.id}).status_code == 201


def test_subscription_limit(monkeypatch):
    from app.api import document_freshness as api

    monkeypatch.setattr(api, 'MAX_SUBSCRIPTIONS_PER_USER', 2)
    client = login_as(_user('many').username)
    for _ in range(2):
        assert client.post('/api/v1/portal/subscriptions', json={'query': _word()}).status_code == 201
    assert client.post('/api/v1/portal/subscriptions', json={'query': _word()}).status_code == 409


# --- notifications ---------------------------------------------------------------------


def test_tick_notifies_matching_subscribers_new_and_updated():
    space = _space()
    word = _word()
    owner = login_as(space.owner.username)
    reader = login_as(space.reader.username)
    outsider = login_as(space.outsider.username)
    keyword_sub = reader.post('/api/v1/portal/subscriptions', json={'query': word}).json()
    space_sub = reader.post('/api/v1/portal/subscriptions', json={'collection_id': space.id}).json()
    other_word = reader.post('/api/v1/portal/subscriptions', json={'query': _word()}).json()
    # The outsider subscribes to the keyword too, but may not read the space.
    outsider.post('/api/v1/portal/subscriptions', json={'query': word})

    v1 = make_doc(space, f'# Reisekosten\n\nTagegeld 24 Euro. {word}\n\n# Übernachtung\n\n80 Euro.\n')
    release(owner, v1)
    assert _run() >= 2
    first = _notifications(space.reader.id)
    assert {(n.subscription_id, n.change_type, n.job_id) for n in first} == {
        (keyword_sub['id'], 'new', v1), (space_sub['id'], 'new', v1),
    }
    assert next(n for n in first if n.subscription_id == keyword_sub['id']).matched_terms == [word]
    assert all(n.subscription_id != other_word['id'] for n in first)
    assert _notifications(space.outsider.id) == []
    with _db() as db:
        assert db.scalar(select(DocumentRelease.subscribers_notified_at).where(DocumentRelease.job_id == v1)) is not None

    # Update that does not touch the keyword: only the space subscription.
    v2 = make_doc(space, f'# Reisekosten\n\nTagegeld 24 Euro. {word}\n\n# Übernachtung\n\n90 Euro.\n', previous=v1, version=2)
    release(owner, v2)
    _run()
    second = [n for n in _notifications(space.reader.id) if n.job_id == v2]
    assert [(n.subscription_id, n.change_type, n.previous_job_id) for n in second] == [(space_sub['id'], 'updated', v1)]
    assert second[0].summary['lines'] == ['Abschnitt „Übernachtung“ geändert: 1 Zeile ersetzt']
    assert second[0].summary['stats']['sections_changed'] == 1

    # Update that changes the keyword's line: both.
    v3 = make_doc(space, f'# Reisekosten\n\nTagegeld 28 Euro. {word}\n\n# Übernachtung\n\n90 Euro.\n', previous=v2, version=3)
    release(owner, v3)
    _run()
    third = {n.subscription_id for n in _notifications(space.reader.id) if n.job_id == v3}
    assert third == {keyword_sub['id'], space_sub['id']}
    # Re-running never duplicates.
    _run()
    assert len([n for n in _notifications(space.reader.id) if n.job_id == v3]) == 2


def test_keyword_prefix_matches_compounds_and_title_and_requires_every_keyword():
    space = _space()
    stem = f'zug{uuid.uuid4().hex[:6]}'
    owner = login_as(space.owner.username)
    member = login_as(space.member.username)
    prefix_sub = member.post('/api/v1/portal/subscriptions', json={'query': stem}).json()
    both_sub = member.post('/api/v1/portal/subscriptions', json={'query': f'{stem} tagegeld'}).json()
    missing_sub = member.post('/api/v1/portal/subscriptions', json={'query': f'{stem} dienstwagen'}).json()
    job_id = make_doc(space, '# Tagegeld\n\nGilt immer.\n', filename=f'{stem}kosten.pdf')
    release(owner, job_id)
    _run()
    matched = {n.subscription_id for n in _notifications(space.member.id) if n.job_id == job_id}
    assert prefix_sub['id'] in matched and both_sub['id'] in matched
    assert missing_sub['id'] not in matched


def test_access_lost_hides_feed_and_unreadable_releases_are_not_notified():
    space = _space()
    owner = login_as(space.owner.username)
    reader = login_as(space.reader.username)
    reader.post('/api/v1/portal/subscriptions', json={'collection_id': space.id})
    job_id = make_doc(space)
    release(owner, job_id)
    _run()
    assert reader.get('/api/v1/portal/changes/count').json()['unread'] >= 1
    from app.models.models import CollectionGrant

    with _db() as db:
        db.execute(CollectionGrant.__table__.delete().where(
            CollectionGrant.collection_id == space.id, CollectionGrant.user_id == space.reader.id
        ))
        db.commit()
    assert reader.get('/api/v1/portal/changes').json()['items'] == []
    assert reader.get('/api/v1/portal/changes/count').json()['unread'] == 0
    second = make_doc(space, filename='neu.pdf')
    release(owner, second)
    _run()
    assert [n for n in _notifications(space.reader.id) if n.job_id == second] == []


def test_backlog_and_withdrawn_releases_are_marked_without_notifying():
    space = _space()
    owner = login_as(space.owner.username)
    member = login_as(space.member.username)
    member.post('/api/v1/portal/subscriptions', json={'collection_id': space.id})
    old = make_doc(space, filename='alt.pdf')
    release(owner, old)
    withdrawn = make_doc(space, filename='weg.pdf')
    release(owner, withdrawn)
    with _db() as db:
        db.execute(update(DocumentRelease).where(DocumentRelease.job_id == old).values(
            created_at=datetime.now(timezone.utc) - timedelta(days=30)
        ))
        db.add(KnowledgeWithdrawal(job_id=withdrawn))
        db.commit()
    _run()
    assert [n for n in _notifications(space.member.id) if n.job_id in (old, withdrawn)] == []
    with _db() as db:
        marked = db.scalars(select(DocumentRelease.subscribers_notified_at).where(DocumentRelease.job_id.in_([old, withdrawn]))).all()
    assert all(value is not None for value in marked)


def test_broken_release_is_skipped_and_marked():
    space = _space()
    owner = login_as(space.owner.username)
    member = login_as(space.member.username)
    member.post('/api/v1/portal/subscriptions', json={'collection_id': space.id})
    job_id = make_doc(space)
    release(owner, job_id)
    with patch.object(change_feed, 'describe_release', side_effect=RuntimeError('boom')):
        _run()
    with _db() as db:
        assert db.scalar(select(DocumentRelease.subscribers_notified_at).where(DocumentRelease.job_id == job_id)) is not None
    assert [n for n in _notifications(space.member.id) if n.job_id == job_id] == []


def test_feed_list_count_read_and_idor():
    space = _space()
    owner = login_as(space.owner.username)
    reader = login_as(space.reader.username)
    sub = reader.post('/api/v1/portal/subscriptions', json={'collection_id': space.id}).json()
    first = make_doc(space, filename='eins.pdf')
    release(owner, first)
    second = make_doc(space, filename='zwei.pdf')
    release(owner, second)
    _run()
    feed = reader.get('/api/v1/portal/changes').json()
    mine = [item for item in feed['items'] if item['job_id'] in (first, second)]
    assert len(mine) == 2 and feed['total'] >= 2
    item = next(entry for entry in mine if entry['job_id'] == first)
    assert item['subscription'] == {'id': sub['id'], 'label': space.name}
    assert item['collection']['id'] == space.id and item['change_type'] == 'new'
    assert item['document_version'] == 1 and item['read_at'] is None and item['title'] == 'eins.pdf'
    assert reader.get('/api/v1/portal/changes/count').json()['unread'] == feed['total']

    # Someone else's ids are ignored.
    other = login_as(space.outsider.username)
    assert other.post('/api/v1/portal/changes/read', json={'ids': [item['id']]}).json()['updated'] == 0
    assert other.get('/api/v1/portal/changes').json()['items'] == []

    assert reader.post('/api/v1/portal/changes/read', json={'ids': [item['id']]}).json()['updated'] == 1
    assert reader.post('/api/v1/portal/changes/read', json={'ids': [item['id']]}).json()['updated'] == 0
    unread = reader.get('/api/v1/portal/changes', params={'unread': True}).json()['items']
    assert item['id'] not in [entry['id'] for entry in unread]
    assert reader.post('/api/v1/portal/changes/read', json={}).json()['updated'] == 0
    assert reader.post('/api/v1/portal/changes/read', json={'all': True}).json()['updated'] >= 1
    assert reader.get('/api/v1/portal/changes/count').json()['unread'] == 0
    paged = reader.get('/api/v1/portal/changes', params={'page': 2, 'page_size': 1}).json()
    assert paged['page'] == 2 and len(paged['items']) == 1

    # Deleting the subscription removes its notifications.
    assert reader.delete(f'/api/v1/portal/subscriptions/{sub["id"]}').status_code == 204
    with _db() as db:
        assert db.scalar(select(ChangeNotification.id).where(ChangeNotification.subscription_id == sub['id'])) is None
        assert db.get(TopicSubscription, sub['id']) is None


# --- ad-hoc search ------------------------------------------------------------------------


def test_change_search_matches_readable_recent_releases_with_summary():
    space = _space()
    word = _word()
    owner = login_as(space.owner.username)
    v1 = make_doc(space, f'# Reisekosten\n\nTagegeld 24 Euro für {word}.\n')
    release(owner, v1)
    v2 = make_doc(space, f'# Reisekosten\n\nTagegeld 28 Euro für {word}.\n', previous=v1, version=2)
    release(owner, v2)
    unrelated = make_doc(space, '# Kantine\n\nMenü.\n', filename='kantine.pdf')
    release(owner, unrelated)

    reader = login_as(space.reader.username)
    response = reader.get('/api/v1/portal/changes/search', params={'q': f'Was hat sich an {word} geändert?'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['terms'] == [word]
    assert [item['job_id'] for item in body['items']] == [v2, v1]  # newest first
    updated = body['items'][0]
    assert updated['change_type'] == 'updated' and updated['previous_job_id'] == v1
    assert updated['summary']['lines'] == ['Abschnitt „Reisekosten“ geändert: 1 Zeile ersetzt']
    assert body['items'][1]['change_type'] == 'new' and body['items'][1]['summary']['stats'] is None

    scoped = reader.get('/api/v1/portal/changes/search', params={'collection_id': space.id}).json()
    assert {item['job_id'] for item in scoped['items']} == {v1, v2, unrelated}

    # No read access: nothing; foreign collection_id: 404; validation.
    outsider = login_as(space.outsider.username)
    assert outsider.get('/api/v1/portal/changes/search', params={'q': word}).json()['items'] == []
    assert outsider.get('/api/v1/portal/changes/search', params={'collection_id': space.id}).status_code == 404
    assert reader.get('/api/v1/portal/changes/search', params={'days': 0}).status_code == 422
    assert reader.get('/api/v1/portal/changes/search', params={'days': 366}).status_code == 422

    # Older than `days`: excluded.
    with _db() as db:
        db.execute(update(DocumentRelease).where(DocumentRelease.job_id == v1).values(
            created_at=datetime.now(timezone.utc) - timedelta(days=40)
        ))
        db.commit()
    recent = reader.get('/api/v1/portal/changes/search', params={'q': word, 'days': 30}).json()
    assert [item['job_id'] for item in recent['items']] == [v2]


def test_keyword_matches_unit():
    assert change_feed.keyword_matches([], set())
    assert change_feed.keyword_matches(['reis'], {'reisekost'})
    assert not change_feed.keyword_matches(['rei'], {'reisekost'})  # short keywords match exactly only
    assert not change_feed.keyword_matches(['reis', 'tagegeld'], {'reisekost'})
    assert change_feed.query_terms('Was hat sich an unseren Reiseregeln geändert?') == ['reiseregel']
    assert change_feed.query_terms('neu') == [] and change_feed.query_terms(None) == []


def test_change_search_filters_access_in_sql_and_caps_described_releases(monkeypatch):
    word = _word()
    mine = _space()
    owner = login_as(mine.owner.username)
    readable = make_doc(mine, f'# Reisekosten\n\nTagegeld für {word}.\n')
    release(owner, readable)
    # Newer releases in a space the reader cannot see must not use up the scan.
    foreign = _space()
    foreign_owner = login_as(foreign.owner.username)
    for index in range(4):
        job_id = make_doc(foreign, f'# Intern\n\n{word} {index}\n', filename=f'intern-{index}.pdf')
        release(foreign_owner, job_id)
    monkeypatch.setattr(change_feed, '_SEARCH_SCAN', 3)
    described: list[str] = []
    original = change_feed.describe_release

    def counting(db, release_row, job, collection):
        described.append(job.id)
        return original(db, release_row, job, collection)

    monkeypatch.setattr(change_feed, 'describe_release', counting)
    reader = login_as(mine.reader.username)
    body = reader.get('/api/v1/portal/changes/search', params={'q': word}).json()
    assert [item['job_id'] for item in body['items']] == [readable]
    assert body['incomplete'] is False
    assert readable in described and len(described) <= 3

    # Withdrawn releases are excluded before describing.
    with _db() as db:
        db.add(KnowledgeWithdrawal(job_id=readable))
        db.commit()
    described.clear()
    assert reader.get('/api/v1/portal/changes/search', params={'q': word}).json()['items'] == []
    assert readable not in described


def test_change_search_reports_incomplete_when_the_cap_is_reached(monkeypatch):
    space = _space()
    word = _word()
    owner = login_as(space.owner.username)
    older = make_doc(space, f'# Reisekosten\n\nTagegeld für {word}.\n', filename='alt.pdf')
    release(owner, older)
    newer = make_doc(space, '# Kantine\n\nMenü.\n', filename='kantine.pdf')
    release(owner, newer)
    reader = login_as(space.reader.username)
    params = {'q': word, 'collection_id': space.id}
    full = reader.get('/api/v1/portal/changes/search', params=params).json()
    assert ([item['job_id'] for item in full['items']], full['incomplete']) == ([older], False)

    monkeypatch.setattr(change_feed, '_SEARCH_SCAN', 1)
    capped = reader.get('/api/v1/portal/changes/search', params=params).json()
    assert (capped['items'], capped['incomplete']) == ([], True)
    # Exactly as many candidates as the cap: complete.
    monkeypatch.setattr(change_feed, '_SEARCH_SCAN', 2)
    exact = reader.get('/api/v1/portal/changes/search', params=params).json()
    assert ([item['job_id'] for item in exact['items']], exact['incomplete']) == ([older], False)


def test_change_search_without_any_readable_space_is_empty():
    outsider = _user('nospace')
    word = _word()
    with _db() as db:
        user = db.get(User, outsider.id)
        changes, terms, incomplete = change_feed.search_changes(db, user, q=word, collection_id=None, days=30)
    assert (changes, terms, incomplete) == ([], [word], False)
