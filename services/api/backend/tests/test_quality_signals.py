"""app/services/quality_signals.py: the `unanswered` signal both chat
paths queue for a `no_context` guard turn, and `flush_outbox`'s
best-effort forwarding to Weave-Ingest's POST /api/v1/quality/signals
(mocked at the httpx transport, same technique as
tests/test_streaming_api.py's `runtime` fixture)."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.config import settings
from app.models.models import Conversation, Message, MessageRole, QualitySignalOutbox
from app.services import quality_signals, runtime_client
from tests.conftest import auth_headers, client, make_user_with_token

NO_CONTEXT_TRACE = {
    'guard': {'triggered': True, 'reason': 'no_context', 'topic': None},
    'retrieval': {
        'candidates': 0,
        'used': 0,
        'collections': ['hr', '__none__'],
        'rewritten_query': 'Tagegeld Ausland Hoehe',
    },
}


@pytest.fixture(autouse=True)
def _signals(monkeypatch):
    monkeypatch.setattr(settings, 'quality_signals_enabled', True)
    monkeypatch.setattr(settings, 'ingest_api_url', '')
    monkeypatch.setattr(settings, 'ingest_service_token', '')


@pytest.fixture
def ingest(monkeypatch):
    """Configure forwarding and capture every request quality_signals'
    real `_client()` sends; `respond` sets the reply (status or exception)."""

    class _Ingest:
        def __init__(self) -> None:
            self.requests: list[httpx.Request] = []
            self.reply: int | Exception = 200
            # Weave-Ingest's blind upsert by signal_key: the last payload applied.
            self.store: dict[str, dict] = {}
            # Called with each accepted request's body after it was applied.
            self.after_apply = None

        def bodies(self) -> list[dict]:
            return [json.loads(request.content) for request in self.requests]

    fake = _Ingest()

    def handler(request: httpx.Request) -> httpx.Response:
        fake.requests.append(request)
        if isinstance(fake.reply, Exception):
            raise fake.reply
        if fake.reply < 400:
            body = json.loads(request.content)
            for signal in body['signals']:
                fake.store[signal['signal_key']] = signal['payload']
            if fake.after_apply is not None:
                fake.after_apply(body)
        return httpx.Response(fake.reply, json={'accepted': 0})

    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client

    def _patched_client_cls(*args, **kwargs):
        kwargs.setdefault('transport', transport)
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(quality_signals.httpx, 'Client', _patched_client_cls)
    monkeypatch.setattr(settings, 'ingest_api_url', 'http://ingest:8000/')
    monkeypatch.setattr(settings, 'ingest_service_token', 'svc-token')
    return fake


def _outbox(db_session) -> list[QualitySignalOutbox]:
    db_session.expire_all()
    return db_session.query(QualitySignalOutbox).order_by(QualitySignalOutbox.created_at).all()


def _add_row(db_session, key: str, *, created_at: datetime, forwarded_at: datetime | None = None, payload=None):
    row = QualitySignalOutbox(
        signal_key=key, payload=payload or {'kind': 'unanswered', 'question': key}, created_at=created_at,
        forwarded_at=forwarded_at,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _stub_runtime_chat(monkeypatch, trace: dict) -> None:
    monkeypatch.setattr(
        runtime_client, 'chat', lambda **kwargs: {'answer': 'Dazu habe ich nichts gefunden.', 'sources': [], 'trace': trace}
    )
    monkeypatch.setattr(runtime_client, 'conversation_title', lambda **kwargs: 'Titel')


def _stub_runtime_stream(monkeypatch, trace: dict) -> None:
    def events():
        yield {'type': 'trace', 'trace': trace}
        yield {'type': 'delta', 'text': 'Dazu habe ich nichts gefunden.'}
        yield {'type': 'sources', 'sources': []}
        yield {'type': 'done'}

    monkeypatch.setattr(runtime_client, 'chat_stream', lambda **kwargs: events())
    monkeypatch.setattr(runtime_client, 'conversation_title', lambda **kwargs: 'Titel')


# --- unanswered signals --------------------------------------------------------


def test_chat_queues_an_unanswered_signal_for_a_no_context_turn(db_session, monkeypatch):
    user, token = make_user_with_token(db_session, username='qs-chat', team='Personal')
    db_session.commit()
    _stub_runtime_chat(monkeypatch, NO_CONTEXT_TRACE)

    response = client.post(
        '/v1/chat', json={'bot_id': 'hr-bot', 'message': 'Wie hoch ist das Tagegeld im Ausland?'},
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    message_id = response.json()['message_id']
    rows = _outbox(db_session)
    assert len(rows) == 1
    assert rows[0].signal_key == f'unanswered:{message_id}'
    payload = rows[0].payload
    assert payload['kind'] == 'unanswered'
    assert payload['question'] == 'Wie hoch ist das Tagegeld im Ausland?'
    assert payload['guard_reason'] == 'no_context'
    assert payload['collections'] == ['hr']
    assert payload['bot_id'] == 'hr-bot'
    assert payload['rewritten_query'] == 'Tagegeld Ausland Hoehe'
    assert payload['created_at']
    serialized = json.dumps(payload)
    assert str(user.id) not in serialized and 'qs-chat' not in serialized and 'Personal' not in serialized


def test_chat_stream_queues_an_unanswered_signal_for_a_no_context_turn(db_session, monkeypatch):
    _, token = make_user_with_token(db_session, username='qs-stream')
    db_session.commit()
    _stub_runtime_stream(monkeypatch, NO_CONTEXT_TRACE)

    response = client.post(
        '/v1/chat/stream', json={'bot_id': 'hr-bot', 'message': 'Tagegeld Ausland?'}, headers=auth_headers(token)
    )

    assert response.status_code == 200
    message = db_session.query(Message).filter(Message.role == MessageRole.ASSISTANT).one()
    rows = _outbox(db_session)
    assert [row.signal_key for row in rows] == [f'unanswered:{message.id}']
    assert rows[0].payload['question'] == 'Tagegeld Ausland?'


def test_unanswered_signal_is_queued_even_without_collections(db_session, monkeypatch):
    _, token = make_user_with_token(db_session, username='qs-nocoll')
    db_session.commit()
    _stub_runtime_chat(monkeypatch, {'guard': {'triggered': True, 'reason': 'no_context'}, 'retrieval': None})

    client.post('/v1/chat', json={'bot_id': 'hr-bot', 'message': 'Frage'}, headers=auth_headers(token))

    rows = _outbox(db_session)
    assert len(rows) == 1
    assert rows[0].payload['collections'] == []
    assert rows[0].payload['rewritten_query'] is None


@pytest.mark.parametrize(
    'trace',
    [
        {},
        {'guard': None},
        {'guard': {'triggered': True, 'reason': 'no_collections'}},
        {'guard': {'triggered': True, 'reason': 'clarify'}},
        {'guard': {'triggered': False, 'reason': 'no_context'}},
    ],
)
def test_answered_or_other_guard_turns_queue_nothing(db_session, monkeypatch, trace):
    _, token = make_user_with_token(db_session, username='qs-other')
    db_session.commit()
    _stub_runtime_chat(monkeypatch, trace)

    assert client.post('/v1/chat', json={'bot_id': 'hr-bot', 'message': 'Frage'}, headers=auth_headers(token)).status_code == 200
    assert _outbox(db_session) == []


def test_disabled_quality_signals_queue_no_unanswered_signal(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'quality_signals_enabled', False)
    _, token = make_user_with_token(db_session, username='qs-disabled')
    db_session.commit()
    _stub_runtime_chat(monkeypatch, NO_CONTEXT_TRACE)

    assert client.post('/v1/chat', json={'bot_id': 'hr-bot', 'message': 'Frage'}, headers=auth_headers(token)).status_code == 200
    assert _outbox(db_session) == []


def test_a_failing_enqueue_never_fails_the_chat_turn(db_session, monkeypatch):
    _, token = make_user_with_token(db_session, username='qs-enqueue-fails')
    db_session.commit()
    _stub_runtime_chat(monkeypatch, NO_CONTEXT_TRACE)

    def boom(*args, **kwargs):
        raise RuntimeError('outbox broken')

    monkeypatch.setattr(quality_signals, 'enqueue_unanswered_signal', boom)

    response = client.post('/v1/chat', json={'bot_id': 'hr-bot', 'message': 'Frage'}, headers=auth_headers(token))

    assert response.status_code == 200
    assert db_session.get(Message, response.json()['message_id']).content == 'Dazu habe ich nichts gefunden.'


def test_chat_flushes_the_queued_signal_to_ingest_after_the_response(db_session, monkeypatch, ingest):
    _, token = make_user_with_token(db_session, username='qs-chat-flush')
    db_session.commit()
    _stub_runtime_chat(monkeypatch, NO_CONTEXT_TRACE)

    response = client.post('/v1/chat', json={'bot_id': 'hr-bot', 'message': 'Frage'}, headers=auth_headers(token))

    assert response.status_code == 200
    assert len(ingest.requests) == 1
    assert ingest.bodies()[0]['signals'][0]['signal_key'] == f"unanswered:{response.json()['message_id']}"
    assert _outbox(db_session)[0].forwarded_at is not None


def test_chat_stream_flushes_the_queued_signal_to_ingest(db_session, monkeypatch, ingest):
    _, token = make_user_with_token(db_session, username='qs-stream-flush')
    db_session.commit()
    _stub_runtime_stream(monkeypatch, NO_CONTEXT_TRACE)

    response = client.post('/v1/chat/stream', json={'bot_id': 'hr-bot', 'message': 'Frage'}, headers=auth_headers(token))

    assert response.status_code == 200
    assert len(ingest.requests) == 1
    assert _outbox(db_session)[0].forwarded_at is not None


def test_ingest_down_never_fails_the_chat_turn(db_session, monkeypatch, ingest):
    _, token = make_user_with_token(db_session, username='qs-ingest-down')
    db_session.commit()
    _stub_runtime_chat(monkeypatch, NO_CONTEXT_TRACE)
    ingest.reply = httpx.ConnectError('connection refused')

    response = client.post('/v1/chat', json={'bot_id': 'hr-bot', 'message': 'Frage'}, headers=auth_headers(token))

    assert response.status_code == 200
    row = _outbox(db_session)[0]
    assert row.forwarded_at is None
    assert row.attempts == 1


def test_feedback_is_flushed_to_ingest_after_the_response(db_session, ingest):
    user, token = make_user_with_token(db_session, username='qs-feedback-flush')
    conversation = Conversation(user_id=user.id, bot_id='hr-bot')
    db_session.add(conversation)
    db_session.flush()
    db_session.add(Message(conversation_id=conversation.id, role=MessageRole.USER, content='Frage'))
    answer = Message(conversation_id=conversation.id, role=MessageRole.ASSISTANT, content='Antwort')
    db_session.add(answer)
    db_session.commit()

    response = client.put(f'/v1/messages/{answer.id}/feedback', json={'rating': 'wrong'}, headers=auth_headers(token))

    assert response.status_code == 200
    assert len(ingest.requests) == 1
    signal = ingest.bodies()[0]['signals'][0]
    assert signal['signal_key'].startswith('feedback:')
    assert signal['payload']['rating'] == 'wrong'
    assert signal['payload']['question'] == 'Frage'
    assert str(user.id) not in json.dumps(signal)


# --- flush_outbox ---------------------------------------------------------------


def test_flush_is_a_noop_without_ingest_configuration(db_session, monkeypatch):
    _add_row(db_session, 'unanswered:1', created_at=datetime.now(timezone.utc))
    monkeypatch.setattr(settings, 'ingest_api_url', 'http://ingest:8000')
    monkeypatch.setattr(settings, 'ingest_service_token', '')

    assert quality_signals.flush_outbox(db_session) == 0

    monkeypatch.setattr(settings, 'ingest_api_url', '')
    monkeypatch.setattr(settings, 'ingest_service_token', 'svc-token')
    assert quality_signals.flush_outbox(db_session) == 0
    assert _outbox(db_session)[0].attempts == 0


def test_flush_posts_the_batch_oldest_first_and_marks_it_forwarded(db_session, ingest):
    now = datetime.now(timezone.utc)
    _add_row(db_session, 'unanswered:2', created_at=now - timedelta(minutes=1), payload={'kind': 'unanswered', 'n': 2})
    _add_row(db_session, 'unanswered:1', created_at=now - timedelta(minutes=5), payload={'kind': 'unanswered', 'n': 1})
    _add_row(db_session, 'unanswered:0', created_at=now - timedelta(days=2), forwarded_at=now - timedelta(days=1))

    assert quality_signals.flush_outbox(db_session) == 2

    assert len(ingest.requests) == 1
    request = ingest.requests[0]
    assert request.method == 'POST'
    assert str(request.url) == 'http://ingest:8000/api/v1/quality/signals'
    assert request.headers['authorization'] == 'Bearer svc-token'
    assert json.loads(request.content) == {
        'signals': [
            {'signal_key': 'unanswered:1', 'payload': {'kind': 'unanswered', 'n': 1}},
            {'signal_key': 'unanswered:2', 'payload': {'kind': 'unanswered', 'n': 2}},
        ]
    }
    rows = {row.signal_key: row for row in _outbox(db_session)}
    assert rows['unanswered:1'].forwarded_at is not None
    assert rows['unanswered:2'].forwarded_at is not None


def test_flush_respects_the_batch_limit(db_session, ingest):
    now = datetime.now(timezone.utc)
    for index in range(3):
        _add_row(db_session, f'unanswered:{index}', created_at=now - timedelta(minutes=10 - index))

    # One call drains the backlog, but never more than `limit` rows per request.
    assert quality_signals.flush_outbox(db_session, limit=2) == 3

    assert [s['signal_key'] for s in ingest.bodies()[0]['signals']] == ['unanswered:0', 'unanswered:1']
    assert [s['signal_key'] for s in ingest.bodies()[1]['signals']] == ['unanswered:2']
    assert quality_signals.flush_outbox(db_session, limit=2) == 0


def test_flush_with_nothing_pending_sends_nothing(db_session, ingest):
    assert quality_signals.flush_outbox(db_session) == 0
    assert ingest.requests == []


@pytest.mark.parametrize(
    ('reply', 'error'),
    [(500, 'Weave-Ingest returned HTTP 500'), (401, 'Weave-Ingest returned HTTP 401'),
     (httpx.ConnectError('ingest:8000 refused'), 'Weave-Ingest unreachable')],
)
def test_flush_failure_records_attempts_and_is_retried_by_the_next_flush(db_session, ingest, reply, error):
    _add_row(db_session, 'unanswered:1', created_at=datetime.now(timezone.utc))
    ingest.reply = reply

    assert quality_signals.flush_outbox(db_session) == 0
    assert quality_signals.flush_outbox(db_session) == 0

    row = _outbox(db_session)[0]
    assert row.forwarded_at is None
    assert row.attempts == 2
    assert row.last_error == error
    assert 'ingest:8000' not in row.last_error

    ingest.reply = 200
    assert quality_signals.flush_outbox(db_session) == 1
    row = _outbox(db_session)[0]
    assert row.forwarded_at is not None
    assert row.last_error is None


def test_flush_prunes_rows_forwarded_more_than_30_days_ago(db_session, ingest):
    now = datetime.now(timezone.utc)
    _add_row(db_session, 'unanswered:old', created_at=now - timedelta(days=40), forwarded_at=now - timedelta(days=31))
    _add_row(db_session, 'unanswered:recent', created_at=now - timedelta(days=10), forwarded_at=now - timedelta(days=5))

    quality_signals.flush_outbox(db_session)

    assert [row.signal_key for row in _outbox(db_session)] == ['unanswered:recent']


def test_a_row_rewritten_while_in_flight_is_not_marked_forwarded(db_session, ingest, monkeypatch):
    """A feedback changed (or retracted) during the POST must still be sent
    in its newer state by the next flush."""
    _add_row(db_session, 'feedback:1', created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
             payload={'kind': 'feedback', 'rating': 'wrong'})
    real_client = quality_signals._client

    def rewriting_client():
        from app.core.db import SessionLocal

        with SessionLocal() as other:
            row = other.query(QualitySignalOutbox).one()
            quality_signals._enqueue(other, row.signal_key, {'kind': 'feedback', 'rating': 'wrong', 'retracted': True})
            other.commit()
        return real_client()

    monkeypatch.setattr(quality_signals, '_client', rewriting_client)

    assert quality_signals.flush_outbox(db_session) == 0
    row = _outbox(db_session)[0]
    assert row.forwarded_at is None
    assert row.payload['retracted'] is True

    monkeypatch.setattr(quality_signals, '_client', real_client)
    assert quality_signals.flush_outbox(db_session) == 1
    assert ingest.bodies()[-1]['signals'][0]['payload']['retracted'] is True


S1 = {'kind': 'feedback', 'rating': 'wrong', 'retracted': False}
S2 = {'kind': 'feedback', 'rating': 'wrong', 'retracted': True}


def _rewrite_in_other_session(payload: dict) -> None:
    from app.core.db import SessionLocal

    with SessionLocal() as other:
        quality_signals._enqueue(other, 'feedback:1', payload)
        other.commit()


def _flush_in_other_session() -> int:
    from app.core.db import SessionLocal

    with SessionLocal() as other:
        return quality_signals.flush_outbox(other)


def test_overlapping_flushes_applied_out_of_order_resend_the_latest_state(db_session, ingest, monkeypatch):
    """Flush A reads the rating (S1); the user retracts it (S2) and flush B
    sends S2, which Weave-Ingest applies BEFORE A's stale S1 and B marks
    forwarded. A must re-queue the row and resend S2, or Weave-Ingest keeps
    the retracted rating for good."""
    _add_row(db_session, 'feedback:1', created_at=datetime.now(timezone.utc) - timedelta(minutes=1), payload=S1)
    real_client = quality_signals._client
    calls = {'n': 0}

    def interleaving_client():
        calls['n'] += 1
        if calls['n'] == 1:  # A's request is in flight
            _rewrite_in_other_session(S2)
            assert _flush_in_other_session() == 1  # B: sends S2, marks forwarded
        return real_client()

    monkeypatch.setattr(quality_signals, '_client', interleaving_client)

    assert quality_signals.flush_outbox(db_session) == 1  # A's follow-up resend

    sent = [body['signals'][0]['payload'] for body in ingest.bodies()]
    assert sent == [S2, S1, S2]  # B, A (stale, applied last), A's follow-up
    assert ingest.store['feedback:1'] == S2
    row = _outbox(db_session)[0]
    assert row.forwarded_at is not None
    assert row.payload == S2


def test_a_pending_mark_of_the_newer_flush_cannot_outrun_the_requeue(db_session, ingest):
    """Same race, but A completes (and re-queues) while B's response is
    still on its way: B's conditional mark must then fail, and the latest
    state is still resent after A's stale request."""
    _add_row(db_session, 'feedback:1', created_at=datetime.now(timezone.utc) - timedelta(minutes=1), payload=S1)
    row = _outbox(db_session)[0]
    a_sent = [(row.id, row.created_at)]  # A read S1
    db_session.commit()
    _rewrite_in_other_session(S2)

    def a_lands_after_b(body):
        if body['signals'][0]['payload'] == S2 and ingest.after_apply is not None:
            ingest.after_apply = None
            ingest.store['feedback:1'] = S1  # A's stale request applied after B's
            from app.core.db import SessionLocal

            with SessionLocal() as other:  # A completes before B's mark
                assert quality_signals._requeue_superseded(other, a_sent) == 1

    ingest.after_apply = a_lands_after_b

    # B: its mark fails (re-queued meanwhile), so it re-queues and resends.
    assert quality_signals.flush_outbox(db_session) == 1

    assert [body['signals'][0]['payload'] for body in ingest.bodies()] == [S2, S2]
    assert ingest.store['feedback:1'] == S2
    row = _outbox(db_session)[0]
    assert row.forwarded_at is not None
    assert row.payload == S2


def test_a_failed_stale_flush_requeues_a_row_another_flush_forwarded(db_session, ingest, monkeypatch):
    """A timed-out request may still have been applied: if the row was
    rewritten and forwarded meanwhile, it is re-queued for the next flush
    (no immediate follow-up against a failing Weave-Ingest)."""
    _add_row(db_session, 'feedback:1', created_at=datetime.now(timezone.utc) - timedelta(minutes=1), payload=S1)
    real_client = quality_signals._client
    calls = {'n': 0}

    def interleaving_client():
        calls['n'] += 1
        if calls['n'] == 1:
            _rewrite_in_other_session(S2)
            assert _flush_in_other_session() == 1
            ingest.reply = httpx.ReadTimeout('ingest:8000 timed out')
        return real_client()

    monkeypatch.setattr(quality_signals, '_client', interleaving_client)

    assert quality_signals.flush_outbox(db_session) == 0
    assert len(ingest.requests) == 2  # B, then A's failing one -- no follow-up
    row = _outbox(db_session)[0]
    assert row.forwarded_at is None
    assert row.payload == S2

    ingest.reply = 200
    assert quality_signals.flush_outbox(db_session) == 1
    assert ingest.bodies()[-1]['signals'][0]['payload'] == S2


def test_unchanged_rows_are_not_requeued(db_session, ingest):
    """Rows another flush already forwarded in the SAME state stay
    forwarded; only a changed `created_at` re-queues."""
    _add_row(db_session, 'unanswered:1', created_at=datetime.now(timezone.utc), payload={'kind': 'unanswered'})
    row = _outbox(db_session)[0]
    sent = [(row.id, row.created_at)]
    db_session.commit()
    assert quality_signals.flush_outbox(db_session) == 1

    assert quality_signals._requeue_superseded(db_session, sent) == 0
    assert _outbox(db_session)[0].forwarded_at is not None


def test_background_flush_swallows_unexpected_errors(monkeypatch, caplog):
    def boom(db, limit=100):
        raise RuntimeError('unexpected')

    monkeypatch.setattr(quality_signals, 'flush_outbox', boom)

    quality_signals.flush_outbox_in_background()  # must not raise

    assert 'Flushing the quality signal outbox failed' in caplog.text


# --- retention, backlog draining, deleted conversations -------------------------


def test_flush_prunes_old_unforwarded_rows_even_without_ingest_config(db_session):
    now = datetime.now(timezone.utc)
    _add_row(db_session, 'unanswered:1', created_at=now - quality_signals.UNFORWARDED_RETENTION - timedelta(days=1))
    _add_row(db_session, 'unanswered:2', created_at=now - timedelta(days=1))

    assert quality_signals.flush_outbox(db_session) == 0

    assert [row.signal_key for row in _outbox(db_session)] == ['unanswered:2']


def test_flush_drains_a_backlog_in_one_call(db_session, ingest):
    now = datetime.now(timezone.utc)
    for index in range(5):
        _add_row(db_session, f'unanswered:{index + 1}', created_at=now - timedelta(minutes=10 - index))

    assert quality_signals.flush_outbox(db_session, limit=2) == 5

    assert len(ingest.requests) == 3
    assert all(row.forwarded_at is not None for row in _outbox(db_session))


def test_deleting_a_conversation_retracts_its_queued_signals_without_text(db_session, monkeypatch):
    user, token = make_user_with_token(db_session, username='qs-delete', team='Personal')
    db_session.commit()
    _stub_runtime_chat(monkeypatch, NO_CONTEXT_TRACE)
    response = client.post('/v1/chat', json={'bot_id': 'general-assistant', 'message': 'Wie hoch ist das Tagegeld?'}, headers=auth_headers(token))
    assert response.status_code == 200
    rows = _outbox(db_session)
    assert len(rows) == 1 and rows[0].payload['question'] == 'Wie hoch ist das Tagegeld?'

    deleted = client.delete(f"/v1/conversations/{response.json()['conversation_id']}", headers=auth_headers(token))

    assert deleted.status_code == 204
    rows = _outbox(db_session)
    assert len(rows) == 1
    assert rows[0].payload['retracted'] is True
    assert rows[0].payload['question'] is None
    assert rows[0].payload['rewritten_query'] is None
    assert rows[0].forwarded_at is None


def test_deleting_all_conversations_retracts_their_signals(db_session, monkeypatch):
    user, token = make_user_with_token(db_session, username='qs-delete-all', team='Personal')
    db_session.commit()
    _stub_runtime_chat(monkeypatch, NO_CONTEXT_TRACE)
    for question in ('Frage eins?', 'Frage zwei?'):
        assert client.post('/v1/chat', json={'bot_id': 'general-assistant', 'message': question}, headers=auth_headers(token)).status_code == 200

    assert client.delete('/v1/conversations', headers=auth_headers(token)).status_code == 204

    rows = _outbox(db_session)
    assert len(rows) == 2
    assert all(row.payload['retracted'] is True and row.payload['question'] is None for row in rows)
