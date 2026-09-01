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
