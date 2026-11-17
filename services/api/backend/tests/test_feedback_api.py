"""Answer feedback (app/api/feedback.py): PUT/DELETE
/v1/messages/{message_id}/feedback, the `feedback` replayed by
GET /v1/conversations/{id}, the `message_id` both chat paths now return, and
the anonymous quality signal each rating change queues in
`quality_signal_outbox` (app/services/quality_signals.py)."""

import json
import uuid

import pytest

from app.core import ratelimit
from app.core.config import settings
from app.core.ratelimit import FixedWindowRateLimiter
from app.models.models import Conversation, Message, MessageFeedback, MessageRole, QualitySignalOutbox
from app.services import runtime_client
from tests.conftest import auth_headers, client, make_user_with_token

SOURCES = [
    {
        'source': 'upload',
        'document_id': 'doc-1',
        'chunk_id': 7,
        'source_job_id': 'job-1',
        'collection': 'hr',
        'document_version': 3,
        'heading_path': ['Reisekosten', 'Tagegeld'],
        'chunk_index': 4,
        'original_filename': 'Reiserichtlinie.pdf',
        'text': 'Das Tagegeld betraegt 28 Euro.',
        'uploaded_by': 'max.mustermann',
    },
    {
        'source': 'upload',
        'document_id': 'doc-2',
        'chunk_id': 1,
        'source_job_id': 'job-2',
        'collection': 'hr',
        'original_filename': 'FAQ.pdf',
    },
    {'source': 'upload', 'document_id': 'doc-3', 'chunk_id': 2, 'collection': None},
]


@pytest.fixture(autouse=True)
def _signals_enabled(monkeypatch):
    """Quality signals on, forwarding unconfigured -- so queued rows stay
    in the outbox for inspection and no test reaches a real Weave-Ingest."""
    monkeypatch.setattr(settings, 'quality_signals_enabled', True)
    monkeypatch.setattr(settings, 'ingest_api_url', '')
    monkeypatch.setattr(settings, 'ingest_service_token', '')


def _seed(db_session, user, *, bot_id='hr-bot', sources=SOURCES, trace=None) -> tuple[Conversation, Message, Message]:
    conversation = Conversation(user_id=user.id, bot_id=bot_id, title='Tagegeld')
    db_session.add(conversation)
    db_session.flush()
    question = Message(conversation_id=conversation.id, role=MessageRole.USER, content='Wie hoch ist das Tagegeld?')
    db_session.add(question)
    db_session.flush()
    answer = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content='Das Tagegeld betraegt 28 Euro [1]. ' + 'x' * 600,
        sources=sources,
        trace=trace if trace is not None else {'guard': None, 'retrieval': {'collections': ['hr', '__none__']}},
    )
    db_session.add(answer)
    db_session.commit()
    return conversation, question, answer


def _outbox(db_session) -> list[QualitySignalOutbox]:
    db_session.expire_all()
    return db_session.query(QualitySignalOutbox).order_by(QualitySignalOutbox.created_at).all()


# --- PUT ----------------------------------------------------------------------


def test_put_feedback_requires_authentication():
    response = client.put('/v1/messages/1/feedback', json={'rating': 'helpful'})
    assert response.status_code == 401


def test_put_feedback_creates_the_rating_and_returns_it(db_session):
    user, token = make_user_with_token(db_session, username='fb-create')
    _, _, answer = _seed(db_session, user)

    response = client.put(
        f'/v1/messages/{answer.id}/feedback',
        json={'rating': 'wrong', 'comment': '  Es sind 32 Euro.  '},
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body['message_id'] == answer.id
    assert body['rating'] == 'wrong'
    assert body['comment'] == 'Es sind 32 Euro.'
    assert body['updated_at']
    rows = db_session.query(MessageFeedback).all()
    assert len(rows) == 1
    assert rows[0].user_id == user.id and rows[0].message_id == answer.id


def test_put_feedback_again_updates_the_same_row(db_session):
    user, token = make_user_with_token(db_session, username='fb-update')
    _, _, answer = _seed(db_session, user)
    headers = auth_headers(token)

    first = client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'wrong', 'comment': 'alt'}, headers=headers)
    second = client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'helpful'}, headers=headers)

    assert first.status_code == second.status_code == 200
    assert second.json()['rating'] == 'helpful'
    assert second.json()['comment'] is None
    db_session.expire_all()
    rows = db_session.query(MessageFeedback).all()
    assert len(rows) == 1
    assert rows[0].rating == 'helpful' and rows[0].comment is None


@pytest.mark.parametrize(
    'payload',
    [
        {'rating': 'great'},
        {'rating': ''},
        {},
        {'rating': 'wrong', 'comment': 'x' * 2001},
        {'rating': 'wrong', 'comment': 42},
    ],
)
def test_put_feedback_rejects_invalid_bodies(db_session, payload):
    user, token = make_user_with_token(db_session, username='fb-invalid')
    _, _, answer = _seed(db_session, user)

    response = client.put(f'/v1/messages/{answer.id}/feedback', json=payload, headers=auth_headers(token))

    assert response.status_code == 422
    assert db_session.query(MessageFeedback).count() == 0


def test_put_feedback_strips_the_comment_before_the_length_check_and_blank_becomes_null(db_session):
    user, token = make_user_with_token(db_session, username='fb-strip')
    _, _, answer = _seed(db_session, user)
    headers = auth_headers(token)

    padded = client.put(
        f'/v1/messages/{answer.id}/feedback', json={'rating': 'outdated', 'comment': '  ' + 'y' * 2000 + '  '},
        headers=headers,
    )
    blank = client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'outdated', 'comment': '   '}, headers=headers)

    assert padded.status_code == 200
    assert padded.json()['comment'] == 'y' * 2000
    assert blank.status_code == 200
    assert blank.json()['comment'] is None


def test_put_feedback_on_another_users_message_is_404(db_session):
    owner, _ = make_user_with_token(db_session, username='fb-owner')
    _, intruder_token = make_user_with_token(db_session, username='fb-intruder')
    _, _, answer = _seed(db_session, owner)

    response = client.put(
        f'/v1/messages/{answer.id}/feedback', json={'rating': 'wrong'}, headers=auth_headers(intruder_token)
    )

    assert response.status_code == 404
    assert response.json()['detail'] == 'Message not found'
    assert db_session.query(MessageFeedback).count() == 0
    assert db_session.query(QualitySignalOutbox).count() == 0


def test_put_feedback_on_a_user_message_is_404(db_session):
    user, token = make_user_with_token(db_session, username='fb-user-msg')
    _, question, _ = _seed(db_session, user)

    response = client.put(f'/v1/messages/{question.id}/feedback', json={'rating': 'helpful'}, headers=auth_headers(token))

    assert response.status_code == 404


@pytest.mark.parametrize('message_id', ['999999', 'abc', '0', '-1', str(2**31), '1.5'])
def test_put_feedback_on_unknown_or_malformed_ids_is_404(db_session, message_id):
    user, token = make_user_with_token(db_session, username='fb-unknown')
    _seed(db_session, user)

    response = client.put(f'/v1/messages/{message_id}/feedback', json={'rating': 'helpful'}, headers=auth_headers(token))

    assert response.status_code == 404


def test_put_feedback_is_rate_limited(db_session, monkeypatch):
    user, token = make_user_with_token(db_session, username='fb-ratelimit')
    _, _, answer = _seed(db_session, user)
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=1))
    headers = auth_headers(token)

    assert client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'helpful'}, headers=headers).status_code == 200
    assert client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'helpful'}, headers=headers).status_code == 429


# --- DELETE -------------------------------------------------------------------


def test_delete_feedback_removes_the_rating_and_is_idempotent(db_session):
    user, token = make_user_with_token(db_session, username='fb-delete')
    _, _, answer = _seed(db_session, user)
    headers = auth_headers(token)
    client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'wrong'}, headers=headers)

    first = client.delete(f'/v1/messages/{answer.id}/feedback', headers=headers)
    second = client.delete(f'/v1/messages/{answer.id}/feedback', headers=headers)

    assert first.status_code == second.status_code == 204
    db_session.expire_all()
    assert db_session.query(MessageFeedback).count() == 0


def test_delete_feedback_on_another_users_message_is_404_and_keeps_their_rating(db_session):
    owner, owner_token = make_user_with_token(db_session, username='fb-del-owner')
    _, intruder_token = make_user_with_token(db_session, username='fb-del-intruder')
    _, _, answer = _seed(db_session, owner)
    client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'wrong'}, headers=auth_headers(owner_token))

    response = client.delete(f'/v1/messages/{answer.id}/feedback', headers=auth_headers(intruder_token))

    assert response.status_code == 404
    db_session.expire_all()
    assert db_session.query(MessageFeedback).count() == 1


@pytest.mark.parametrize('message_id', ['999999', 'abc'])
def test_delete_feedback_on_unknown_or_malformed_ids_is_404(db_session, message_id):
    _, token = make_user_with_token(db_session, username='fb-del-unknown')

    assert client.delete(f'/v1/messages/{message_id}/feedback', headers=auth_headers(token)).status_code == 404


def test_delete_feedback_requires_authentication():
    assert client.delete('/v1/messages/1/feedback').status_code == 401


# --- GET /v1/conversations/{id} ------------------------------------------------


def test_conversation_replays_the_callers_feedback_per_message(db_session):
    user, token = make_user_with_token(db_session, username='fb-replay')
    conversation, question, answer = _seed(db_session, user)
    headers = auth_headers(token)

    before = client.get(f'/v1/conversations/{conversation.id}', headers=headers).json()
    assert [m['feedback'] for m in before['messages']] == [None, None]

    client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'outdated', 'comment': 'Stand 2024'}, headers=headers)
    after = client.get(f'/v1/conversations/{conversation.id}', headers=headers).json()

    assert after['messages'][0]['feedback'] is None
    feedback = after['messages'][1]['feedback']
    assert feedback['rating'] == 'outdated'
    assert feedback['comment'] == 'Stand 2024'
    assert feedback['updated_at']
    # Existing fields are untouched.
    assert after['messages'][1]['sources'] == SOURCES
    assert after['messages'][1]['notices'] == []
    assert after['title'] == 'Tagegeld'


def test_conversation_never_shows_someone_elses_feedback_row(db_session):
    """Defence in depth: even a feedback row by another user on the
    caller's message (impossible through the API) is not replayed."""
    user, token = make_user_with_token(db_session, username='fb-scope')
    other, _ = make_user_with_token(db_session, username='fb-scope-other')
    conversation, _, answer = _seed(db_session, user)
    db_session.add(MessageFeedback(message_id=answer.id, user_id=other.id, rating='wrong'))
    db_session.commit()

    body = client.get(f'/v1/conversations/{conversation.id}', headers=auth_headers(token)).json()

    assert body['messages'][1]['feedback'] is None


# --- message_id on both chat paths --------------------------------------------


def test_chat_returns_the_persisted_assistant_message_id(db_session, monkeypatch):
    _, token = make_user_with_token(db_session, username='fb-chat-id')
    db_session.commit()
    monkeypatch.setattr(runtime_client, 'chat', lambda **kwargs: {'answer': 'Antwort', 'sources': [], 'trace': {}})
    monkeypatch.setattr(runtime_client, 'conversation_title', lambda **kwargs: 'Titel')

    response = client.post('/v1/chat', json={'bot_id': 'hr-bot', 'message': 'Frage'}, headers=auth_headers(token))

    assert response.status_code == 200
    message_id = response.json()['message_id']
    message = db_session.get(Message, message_id)
    assert message.role == MessageRole.ASSISTANT
    assert message.content == 'Antwort'
    # ...and it is the handle the feedback endpoint takes.
    put = client.put(f'/v1/messages/{message_id}/feedback', json={'rating': 'helpful'}, headers=auth_headers(token))
    assert put.status_code == 200


def test_chat_stream_done_event_carries_the_persisted_message_id(db_session, monkeypatch):
    _, token = make_user_with_token(db_session, username='fb-stream-id')
    db_session.commit()

    def fake_stream(**kwargs):
        yield {'type': 'trace', 'trace': {}}
        yield {'type': 'delta', 'text': 'Antwort'}
        yield {'type': 'sources', 'sources': []}
        yield {'type': 'done'}

    monkeypatch.setattr(runtime_client, 'chat_stream', lambda **kwargs: fake_stream())
    monkeypatch.setattr(runtime_client, 'conversation_title', lambda **kwargs: 'Titel')

    response = client.post('/v1/chat/stream', json={'bot_id': 'hr-bot', 'message': 'Frage'}, headers=auth_headers(token))

    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.split('\n') if line.startswith('data: ')]
    done = next(event for event in events if event['type'] == 'done')
    conversation_id = uuid.UUID(response.headers['x-conversation-id'])
    message = (
        db_session.query(Message)
        .filter(Message.conversation_id == conversation_id, Message.role == MessageRole.ASSISTANT)
        .one()
    )
    assert done == {'type': 'done', 'message_id': message.id}


# --- quality signals queued by feedback ---------------------------------------


def test_feedback_queues_an_anonymous_signal_with_the_answer_context(db_session):
    user, token = make_user_with_token(db_session, username='fb-signal', team='Personal')
    _, _, answer = _seed(db_session, user)

    client.put(
        f'/v1/messages/{answer.id}/feedback', json={'rating': 'wrong', 'comment': 'Es sind 32 Euro.'},
        headers=auth_headers(token),
    )

    rows = _outbox(db_session)
    assert len(rows) == 1
    feedback = db_session.query(MessageFeedback).one()
    assert rows[0].signal_key == f'feedback:{feedback.id}'
    assert rows[0].forwarded_at is None
    assert rows[0].attempts == 0
    payload = rows[0].payload
    assert payload['kind'] == 'feedback'
    assert payload['rating'] == 'wrong'
    assert payload['comment'] == 'Es sind 32 Euro.'
    assert payload['question'] == 'Wie hoch ist das Tagegeld?'
    assert payload['answer_excerpt'] == answer.content[:500]
    assert len(payload['answer_excerpt']) == 500
    assert payload['guard_reason'] is None
    assert payload['bot_id'] == 'hr-bot'
    assert payload['retracted'] is False
    assert payload['created_at']
    assert payload['collections'] == ['hr']
    assert payload['sources'][0] == {
        'source_job_id': 'job-1',
        'collection': 'hr',
        'document_version': 3,
        'heading_path': ['Reisekosten', 'Tagegeld'],
        'chunk_index': 4,
        'chunk_id': 7,
        'original_filename': 'Reiserichtlinie.pdf',
    }
    assert len(payload['sources']) == 3

    # Never the rater's identity (nor the uploader's, nor chunk text).
    serialized = json.dumps(payload)
    for leaked in (str(user.id), 'fb-signal', 'Personal', 'max.mustermann', 'betraegt 28 Euro.'):
        assert leaked not in serialized
    assert not {'user', 'user_id', 'username', 'email', 'team', 'teams'} & set(payload)


def test_feedback_signal_falls_back_to_the_trace_collections_without_the_sentinel(db_session):
    user, token = make_user_with_token(db_session, username='fb-signal-trace')
    _, _, answer = _seed(
        db_session, user, sources=[],
        trace={'guard': {'triggered': True, 'reason': 'no_context'}, 'retrieval': {'collections': ['it', '__none__']}},
    )

    client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'helpful'}, headers=auth_headers(token))

    payload = _outbox(db_session)[0].payload
    assert payload['sources'] == []
    assert payload['collections'] == ['it']
    assert payload['guard_reason'] == 'no_context'


def test_changing_the_rating_rewrites_the_same_outbox_row(db_session):
    user, token = make_user_with_token(db_session, username='fb-signal-update')
    _, _, answer = _seed(db_session, user)
    headers = auth_headers(token)
    client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'wrong'}, headers=headers)
    row = _outbox(db_session)[0]
    row.forwarded_at = row.created_at
    row.attempts = 3
    row.last_error = 'boom'
    db_session.commit()

    client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'outdated', 'comment': 'neu'}, headers=headers)

    rows = _outbox(db_session)
    assert len(rows) == 1
    assert rows[0].payload['rating'] == 'outdated'
    assert rows[0].payload['comment'] == 'neu'
    assert rows[0].forwarded_at is None
    assert rows[0].attempts == 0
    assert rows[0].last_error is None


def test_deleting_feedback_queues_a_retraction_under_the_same_key(db_session):
    user, token = make_user_with_token(db_session, username='fb-signal-retract')
    _, _, answer = _seed(db_session, user)
    headers = auth_headers(token)
    client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'wrong', 'comment': 'falsch'}, headers=headers)
    key = _outbox(db_session)[0].signal_key
    row = _outbox(db_session)[0]
    row.forwarded_at = row.created_at
    db_session.commit()

    response = client.delete(f'/v1/messages/{answer.id}/feedback', headers=headers)

    assert response.status_code == 204
    rows = _outbox(db_session)
    assert len(rows) == 1
    assert rows[0].signal_key == key
    assert rows[0].forwarded_at is None
    assert rows[0].payload['retracted'] is True
    assert rows[0].payload['kind'] == 'feedback'
    assert rows[0].payload['rating'] == 'wrong'


def test_deleting_absent_feedback_queues_nothing(db_session):
    user, token = make_user_with_token(db_session, username='fb-signal-noop')
    _, _, answer = _seed(db_session, user)

    assert client.delete(f'/v1/messages/{answer.id}/feedback', headers=auth_headers(token)).status_code == 204
    assert _outbox(db_session) == []


def test_disabled_quality_signals_store_feedback_but_queue_nothing(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'quality_signals_enabled', False)
    user, token = make_user_with_token(db_session, username='fb-disabled')
    _, _, answer = _seed(db_session, user)
    headers = auth_headers(token)

    put = client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'wrong'}, headers=headers)
    delete = client.delete(f'/v1/messages/{answer.id}/feedback', headers=headers)

    assert put.status_code == 200
    assert delete.status_code == 204
    assert _outbox(db_session) == []
