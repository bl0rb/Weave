"""HTTP-level tests for /internal/bots and /internal/chat (see
app/api/internal.py). Auth gating itself lives in test_auth.py; the chat
pipeline's own behaviour (routing, retrieval, the response guard, error
mapping) is covered end-to-end in tests/test_chat_e2e.py -- this file only
covers GET /internal/bots plus POST /internal/chat's HTTP-contract edges
(request validation, response shape) against the real 'general-assistant'
bot with the 'fake' LLM provider, the same as every other route this stage
of the suite exercises."""

from tests.conftest import AUTH_HEADERS, client

# --- GET /internal/bots -------------------------------------------------------


def test_list_bots_returns_both_example_bots_with_expected_shape():
    resp = client.get('/internal/bots', headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    by_id = {bot['id']: bot for bot in body}
    assert set(by_id) == {'general-assistant', 'legal-support'}

    general = by_id['general-assistant']
    assert general['name'] == 'Allgemeiner Assistent'
    assert general['description']
    assert general['retrieval'] == {'enabled': True}
    assert general['kind'] == 'llm'
    assert general['teams'] == []
    assert general['collections'] == []

    legal = by_id['legal-support']
    assert legal['retrieval'] == {'enabled': True}
    assert legal['kind'] == 'llm'
    assert legal['teams'] == ['legal', 'management']
    assert legal['collections'] == ['vertraege']


def test_list_bots_response_does_not_leak_system_prompt():
    # BotSummary is a deliberately trimmed shape (see app/schemas/bot.py) --
    # system_prompt/model/permissions/guard are implementation detail, not
    # part of the roster-listing contract.
    resp = client.get('/internal/bots', headers=AUTH_HEADERS)
    body = resp.json()
    for bot in body:
        assert set(bot) == {'id', 'name', 'description', 'retrieval', 'kind', 'teams', 'collections'}


# --- POST /internal/chat --------------------------------------------------

_VALID_CHAT_BODY = {'bot_id': 'general-assistant', 'message': 'Hallo!'}


def test_chat_with_valid_body_returns_200_with_the_full_response_shape():
    resp = client.post('/internal/chat', json=_VALID_CHAT_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {'answer', 'sources', 'trace'}
    assert body['answer']
    assert body['sources'] == []
    assert body['trace']['intent'] == 'conversational'
    assert body['trace']['router_mode'] == 'rules'


def test_chat_still_validates_the_request_body():
    resp = client.post('/internal/chat', json={'bot_id': 'general-assistant'}, headers=AUTH_HEADERS)
    assert resp.status_code == 422


def test_chat_rejects_empty_message():
    resp = client.post(
        '/internal/chat', json={'bot_id': 'general-assistant', 'message': ''}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 422


def test_chat_accepts_history_and_user():
    body = {
        **_VALID_CHAT_BODY,
        'history': [{'role': 'user', 'content': 'Hi'}, {'role': 'assistant', 'content': 'Hallo!'}],
        'user': {'id': 'u-1', 'team': 'legal'},
    }
    resp = client.post('/internal/chat', json=body, headers=AUTH_HEADERS)
    # general-assistant has no team restriction (permissions.teams == []) --
    # this only proves the richer body shape is itself accepted end-to-end,
    # not rejected as 422/403.
    assert resp.status_code == 200


def test_chat_unknown_bot_id_returns_404():
    resp = client.post(
        '/internal/chat', json={'bot_id': 'no-such-bot', 'message': 'Hallo!'}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 404


def test_chat_denies_a_team_not_permitted_on_the_bot():
    body = {'bot_id': 'legal-support', 'message': 'Hallo!', 'user': {'team': 'sales'}}
    resp = client.post('/internal/chat', json=body, headers=AUTH_HEADERS)
    assert resp.status_code == 403
