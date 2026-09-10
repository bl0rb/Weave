"""GET /v1/conversations/{id} -- owner scoping (IDOR guard) and the
messages/sources/trace payload shape.
"""

import uuid

from app.models.models import Conversation, Message, MessageRole
from tests.conftest import auth_headers, client, make_user_with_token


def test_owner_can_fetch_their_conversation_with_messages(db_session):
    user, raw_token = make_user_with_token(db_session, username='owner')

    conversation = Conversation(user_id=user.id, bot_id='faq-bot', title='VPN-Frage')
    db_session.add(conversation)
    db_session.flush()

    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content='Wie setze ich mein VPN-Passwort zurueck?',
            ),
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content='Im Self-Service-Portal unter Einstellungen.',
                sources=[{'document_id': str(uuid.uuid4()), 'text': 'VPN-FAQ'}],
                trace={'timings_ms': {'total': 42}},
            ),
        ]
    )
    db_session.commit()

    response = client.get(f'/v1/conversations/{conversation.id}', headers=auth_headers(raw_token))
    assert response.status_code == 200

    body = response.json()
    assert body['id'] == str(conversation.id)
    assert body['bot_id'] == 'faq-bot'
    assert body['title'] == 'VPN-Frage'
    assert len(body['messages']) == 2
    assert body['messages'][0]['role'] == 'user'
    assert body['messages'][0]['sources'] is None
    assert body['messages'][1]['role'] == 'assistant'
    assert body['messages'][1]['sources'][0]['text'] == 'VPN-FAQ'
    assert body['messages'][1]['trace']['timings_ms']['total'] == 42


def test_conversation_belonging_to_another_user_returns_404(db_session):
    owner, _ = make_user_with_token(db_session, username='owner-b')
    _, intruder_token = make_user_with_token(db_session, username='intruder')

    conversation = Conversation(user_id=owner.id, bot_id='faq-bot')
    db_session.add(conversation)
    db_session.commit()

    response = client.get(f'/v1/conversations/{conversation.id}', headers=auth_headers(intruder_token))
    assert response.status_code == 404


def test_unknown_conversation_id_returns_404(db_session):
    _, raw_token = make_user_with_token(db_session, username='owner-c')
    response = client.get(f'/v1/conversations/{uuid.uuid4()}', headers=auth_headers(raw_token))
    assert response.status_code == 404


def test_malformed_conversation_id_returns_404_not_422(db_session):
    _, raw_token = make_user_with_token(db_session, username='owner-d')
    response = client.get('/v1/conversations/not-a-uuid', headers=auth_headers(raw_token))
    assert response.status_code == 404


def test_list_conversations_returns_only_the_caller_own_most_recent_first(db_session):
    user, raw_token = make_user_with_token(db_session, username='owner-e')
    other, _ = make_user_with_token(db_session, username='owner-f')

    older = Conversation(user_id=user.id, bot_id='faq-bot', title='Ältere Frage')
    newer = Conversation(user_id=user.id, bot_id='faq-bot', title='Neuere Frage')
    not_mine = Conversation(user_id=other.id, bot_id='faq-bot', title='Fremde Frage')
    db_session.add_all([older, newer, not_mine])
    db_session.commit()
    # Force a distinct updated_at ordering independent of insertion order.
    older.updated_at = older.updated_at.replace(year=2020)
    db_session.commit()

    response = client.get('/v1/conversations', headers=auth_headers(raw_token))
    assert response.status_code == 200
    items = response.json()['items']
    assert [item['id'] for item in items] == [str(newer.id), str(older.id)]
    assert all('messages' not in item for item in items)


def test_owner_can_delete_their_conversation_and_its_messages(db_session):
    user, raw_token = make_user_with_token(db_session, username='owner-g')
    conversation = Conversation(user_id=user.id, bot_id='faq-bot')
    db_session.add(conversation)
    db_session.flush()
    db_session.add(Message(conversation_id=conversation.id, role=MessageRole.USER, content='Hallo'))
    db_session.commit()
    conversation_id = conversation.id

    response = client.delete(f'/v1/conversations/{conversation_id}', headers=auth_headers(raw_token))
    assert response.status_code == 204
    db_session.expire_all()  # the DELETE above ran on a separate request-scoped session
    assert db_session.get(Conversation, conversation_id) is None
    assert client.get(f'/v1/conversations/{conversation_id}', headers=auth_headers(raw_token)).status_code == 404


def test_cannot_delete_another_user_conversation(db_session):
    owner, _ = make_user_with_token(db_session, username='owner-h')
    _, intruder_token = make_user_with_token(db_session, username='intruder-b')
    conversation = Conversation(user_id=owner.id, bot_id='faq-bot')
    db_session.add(conversation)
    db_session.commit()
    conversation_id = conversation.id

    response = client.delete(f'/v1/conversations/{conversation_id}', headers=auth_headers(intruder_token))
    assert response.status_code == 404
    assert db_session.get(Conversation, conversation_id) is not None
