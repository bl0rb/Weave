"""POST /v1/chat -- the real chat turn (app/api/chat.py), against a mocked
Weave-Runtime. Mocked at `app.services.runtime_client.chat` -- app/api/chat.py
imports the module (`from app.services import runtime_client`) and calls
`runtime_client.chat(...)`, so patching that module attribute is visible
regardless of which module patches it, same reasoning as
tests/test_ratelimit.py's own note on patching origin vs. call site.
"""

import uuid

import pytest

from app.core import ratelimit
from app.core.config import settings
from app.core.ratelimit import FixedWindowRateLimiter
from app.models.models import Conversation, Message, MessageRole
from tests.conftest import auth_headers, client, make_user_with_token


class _RecordingChat:
    """Stands in for runtime_client.chat(): records every call it receives
    (for asserting what app/api/chat.py sent) and either returns a fixed
    result or raises a fixed exception."""

    def __init__(self, *, result: dict | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._error = error

    def __call__(
        self, *, bot_id: str, message: str, history: list[dict], user: dict, collections: list[str] | None = None
    ) -> dict:
        self.calls.append(
            {'bot_id': bot_id, 'message': message, 'history': history, 'user': user, 'collections': collections}
        )
        if self._error is not None:
            raise self._error
        return self._result


def _seed_conversation(db_session, *, user_id, bot_id: str = 'faq-bot') -> Conversation:
    conversation = Conversation(user_id=user_id, bot_id=bot_id, title='Erste Frage')
    db_session.add(conversation)
    db_session.flush()
    db_session.add_all(
        [
            Message(conversation_id=conversation.id, role=MessageRole.USER, content='Erste Frage'),
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content='Erste Antwort',
                sources=[{'text': 'alte-quelle'}],
                trace={'timings_ms': {'total': 1}},
            ),
        ]
    )
    db_session.commit()
    return conversation


def test_chat_requires_authentication():
    response = client.post('/v1/chat', json={'bot_id': 'faq-bot', 'message': 'hi'})
    assert response.status_code == 401


def test_chat_with_new_conversation_persists_the_turn_and_returns_the_answer(db_session, monkeypatch):
    user, raw_token = make_user_with_token(db_session, username='chat-new', team='Support')
    db_session.commit()  # release make_user_with_token's own read snapshot before the request writes

    recorder = _RecordingChat(
        result={
            'answer': 'Setz dein VPN-Passwort im Self-Service-Portal zurueck.',
            'sources': [{'document_id': str(uuid.uuid4()), 'text': 'VPN-FAQ'}],
            'trace': {'timings_ms': {'total': 12}},
        }
    )
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)

    response = client.post(
        '/v1/chat',
        json={'bot_id': 'faq-bot', 'message': 'Wie setze ich mein VPN-Passwort zurueck?'},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body['answer'] == 'Setz dein VPN-Passwort im Self-Service-Portal zurueck.'
    assert body['sources'][0]['text'] == 'VPN-FAQ'
    assert body['trace']['timings_ms']['total'] == 12
    conversation_id = uuid.UUID(body['conversation_id'])

    # A brand-new conversation has no prior turns -- history must be empty,
    # and the just-sent message must never appear inside it too.
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call['bot_id'] == 'faq-bot'
    assert call['message'] == 'Wie setze ich mein VPN-Passwort zurueck?'
    assert call['history'] == []
    assert call['user'] == {'id': str(user.id), 'team': 'Support', 'teams': ['Support']}
    # No `collections` in the request body -- app/api/chat.py must forward
    # exactly that absence (None), not invent a filter that was never asked
    # for. See test_chat_forwards_a_collections_filter_to_runtime below for
    # the "filter was requested" counterpart.
    assert call['collections'] is None

    conversation = db_session.get(Conversation, conversation_id)
    assert conversation is not None
    assert conversation.user_id == user.id
    assert conversation.bot_id == 'faq-bot'
    assert conversation.title == 'Wie setze ich mein VPN-Passwort zurueck?'

    messages = db_session.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.id).all()
    assert len(messages) == 2
    assert messages[0].role == MessageRole.USER
    assert messages[0].content == 'Wie setze ich mein VPN-Passwort zurueck?'
    assert messages[1].role == MessageRole.ASSISTANT
    assert messages[1].sources[0]['text'] == 'VPN-FAQ'
    assert messages[1].trace['timings_ms']['total'] == 12


def test_chat_forwards_a_collections_filter_to_runtime(db_session, monkeypatch):
    """ChatRequest.collections (app/schemas/chat.py) reaches
    runtime_client.chat() verbatim -- see that contract's own docstring:
    this is ALWAYS a further restriction on this caller's own read-
    authority, resolved entirely on Weave-Runtime's side, never a grant
    Weave-API itself interprets or narrows here."""
    user, raw_token = make_user_with_token(db_session, username='chat-with-filter', team='Support')
    db_session.commit()

    recorder = _RecordingChat(result={'answer': 'ok', 'sources': None, 'trace': None})
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)

    response = client.post(
        '/v1/chat',
        json={'bot_id': 'faq-bot', 'message': 'Wo steht das?', 'collections': ['handbuch', 'hr-policies']},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 200
    assert len(recorder.calls) == 1
    assert recorder.calls[0]['collections'] == ['handbuch', 'hr-policies']


def test_chat_forwards_an_empty_collections_filter_distinctly_from_no_filter(db_session, monkeypatch):
    """`[]` ("match nothing") must reach runtime_client.chat() as `[]`, not
    silently collapsed into `None` ("no filter") -- the two are distinct
    values in the contract (ChatRequest.collections' own docstring)."""
    _, raw_token = make_user_with_token(db_session, username='chat-empty-filter')
    db_session.commit()

    recorder = _RecordingChat(result={'answer': 'ok', 'sources': None, 'trace': None})
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)

    response = client.post(
        '/v1/chat',
        json={'bot_id': 'faq-bot', 'message': 'Hallo?', 'collections': []},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 200
    assert recorder.calls[0]['collections'] == []


def test_chat_with_existing_conversation_forwards_history_and_appends_the_new_turn(db_session, monkeypatch):
    user, raw_token = make_user_with_token(db_session, username='chat-existing')
    conversation = _seed_conversation(db_session, user_id=user.id)

    recorder = _RecordingChat(result={'answer': 'Zweite Antwort', 'sources': None, 'trace': None})
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)

    response = client.post(
        '/v1/chat',
        json={'bot_id': 'faq-bot', 'message': 'Zweite Frage', 'conversation_id': str(conversation.id)},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body['conversation_id'] == str(conversation.id)
    assert body['answer'] == 'Zweite Antwort'

    # The two pre-existing turns are forwarded as history, chronologically,
    # and the just-persisted new user message is NOT duplicated into it.
    assert len(recorder.calls) == 1
    assert recorder.calls[0]['message'] == 'Zweite Frage'
    assert recorder.calls[0]['history'] == [
        {'role': 'user', 'content': 'Erste Frage'},
        {'role': 'assistant', 'content': 'Erste Antwort'},
    ]

    messages = (
        db_session.query(Message).filter(Message.conversation_id == conversation.id).order_by(Message.id).all()
    )
    assert len(messages) == 4
    assert messages[2].role == MessageRole.USER
    assert messages[2].content == 'Zweite Frage'
    assert messages[3].role == MessageRole.ASSISTANT
    assert messages[3].content == 'Zweite Antwort'

    # The title was set from the FIRST turn only -- a later turn must never
    # overwrite a name the user has since seen and recognised their
    # conversation by (app/services/conversations.py's append_message).
    db_session.refresh(conversation)
    assert conversation.title == 'Erste Frage'


def test_chat_on_another_users_conversation_returns_404(db_session, monkeypatch):
    owner, _ = make_user_with_token(db_session, username='chat-owner')
    conversation = _seed_conversation(db_session, user_id=owner.id)
    _, intruder_token = make_user_with_token(db_session, username='chat-intruder')
    db_session.commit()

    recorder = _RecordingChat(result={'answer': 'should never be called', 'sources': None, 'trace': None})
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)

    response = client.post(
        '/v1/chat',
        json={'bot_id': 'faq-bot', 'message': 'Hallo?', 'conversation_id': str(conversation.id)},
        headers=auth_headers(intruder_token),
    )
    assert response.status_code == 404
    assert recorder.calls == []


def test_chat_on_conversation_with_a_different_bot_id_returns_409(db_session, monkeypatch):
    user, raw_token = make_user_with_token(db_session, username='chat-bot-mismatch')
    conversation = _seed_conversation(db_session, user_id=user.id, bot_id='faq-bot')

    recorder = _RecordingChat(result={'answer': 'should never be called', 'sources': None, 'trace': None})
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)

    response = client.post(
        '/v1/chat',
        json={'bot_id': 'other-bot', 'message': 'Hallo?', 'conversation_id': str(conversation.id)},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 409
    assert recorder.calls == []


def test_chat_returns_502_when_runtime_is_unavailable_but_keeps_the_user_message(db_session, monkeypatch):
    from app.services.runtime_client import RuntimeUnavailable

    user, raw_token = make_user_with_token(db_session, username='chat-runtime-down')
    db_session.commit()

    recorder = _RecordingChat(error=RuntimeUnavailable('Weave-Runtime unreachable: connection refused'))
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)

    response = client.post(
        '/v1/chat', json={'bot_id': 'faq-bot', 'message': 'Hallo?'}, headers=auth_headers(raw_token)
    )
    assert response.status_code == 502
    assert len(recorder.calls) == 1

    messages = db_session.query(Message).join(Conversation).filter(Conversation.user_id == user.id).all()
    assert len(messages) == 1
    assert messages[0].role == MessageRole.USER
    assert messages[0].content == 'Hallo?'
    assert messages[0].trace is None  # no {"error": ...} assistant turn was ever fabricated


def test_chat_passes_through_runtime_rejected_status_for_an_unknown_bot(db_session, monkeypatch):
    from app.services.runtime_client import RuntimeRejected

    user, raw_token = make_user_with_token(db_session, username='chat-unknown-bot')
    db_session.commit()

    recorder = _RecordingChat(
        error=RuntimeRejected(
            'Weave-Runtime rejected POST /internal/chat with HTTP 404',
            status_code=404,
            detail="Unknown bot_id 'no-such-bot'",
        )
    )
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)

    response = client.post(
        '/v1/chat', json={'bot_id': 'no-such-bot', 'message': 'Hallo?'}, headers=auth_headers(raw_token)
    )
    assert response.status_code == 404
    assert response.json()['detail'] == "Unknown bot_id 'no-such-bot'"

    # The user's own message still went to disk -- only the assistant turn never happened.
    messages = db_session.query(Message).join(Conversation).filter(Conversation.user_id == user.id).all()
    assert len(messages) == 1


def test_chat_route_is_rate_limited(db_session, monkeypatch):
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=1))
    _, raw_token = make_user_with_token(db_session, username='chat-ratelimited')
    db_session.commit()

    recorder = _RecordingChat(result={'answer': 'ok', 'sources': None, 'trace': None})
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)
    headers = auth_headers(raw_token)

    first = client.post('/v1/chat', json={'bot_id': 'faq-bot', 'message': 'eins'}, headers=headers)
    assert first.status_code == 200

    second = client.post('/v1/chat', json={'bot_id': 'faq-bot', 'message': 'zwei'}, headers=headers)
    assert second.status_code == 429


@pytest.mark.parametrize(
    'message',
    ['', 'x' * (settings.chat_message_max_length + 1)],
    ids=['too-short', 'too-long'],
)
def test_chat_message_length_validation_rejects_out_of_range_messages(db_session, message):
    _, raw_token = make_user_with_token(db_session, username='chat-bad-length')
    response = client.post(
        '/v1/chat', json={'bot_id': 'faq-bot', 'message': message}, headers=auth_headers(raw_token)
    )
    assert response.status_code == 422


def test_chat_message_at_the_maximum_configured_length_is_accepted(db_session, monkeypatch):
    _, raw_token = make_user_with_token(db_session, username='chat-max-length')
    db_session.commit()

    recorder = _RecordingChat(result={'answer': 'ok', 'sources': None, 'trace': None})
    monkeypatch.setattr('app.services.runtime_client.chat', recorder)

    message = 'x' * settings.chat_message_max_length
    response = client.post(
        '/v1/chat', json={'bot_id': 'faq-bot', 'message': message}, headers=auth_headers(raw_token)
    )
    assert response.status_code == 200
