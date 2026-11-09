"""HTTP-level tests for the REST mirror (app/api/tools.py): the
TOOLS_API_TOKEN service gate, scope resolution wired to the real
Authorization header, and that a caller-supplied team/user field in the
request body has zero effect.
"""

from __future__ import annotations

from unittest.mock import patch

from app.core.config import settings
from tests.conftest import (
    TOOLS_SERVICE_HEADERS,
    WEAVE_DELEGATION_SECRET,
    client,
    fake_response,
    forge_delegation_token_with_empty_secret_key,
    make_delegation_token,
)


def _auth_headers(token: str) -> dict:
    return {**TOOLS_SERVICE_HEADERS, 'Authorization': f'Bearer {token}'}


# --- TOOLS_API_TOKEN service gate --------------------------------------------


def test_collections_without_service_token_header_is_401():
    token = make_delegation_token()
    resp = client.get('/api/v1/tools/collections', headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 401


def test_collections_with_wrong_service_token_is_401():
    token = make_delegation_token()
    resp = client.get(
        '/api/v1/tools/collections',
        headers={'X-Tools-Service-Token': 'wrong-value', 'Authorization': f'Bearer {token}'},
    )
    assert resp.status_code == 401


def test_collections_with_unconfigured_tools_api_token_is_503(monkeypatch):
    monkeypatch.setattr(settings, 'tools_api_token', '')
    token = make_delegation_token()
    resp = client.get('/api/v1/tools/collections', headers=_auth_headers(token))
    assert resp.status_code == 503


def test_search_bearer_only_when_service_token_not_required(monkeypatch):
    monkeypatch.setattr(settings, 'tools_rest_require_service_token', False)
    monkeypatch.setattr(settings, 'tools_api_token', '')
    token = make_delegation_token(collections=['handbuch'])
    retrieval_resp = fake_response(200, {'results': []})
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp):
        resp = client.post('/api/v1/tools/search', json={'query': 'x'}, headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 200


def test_bearer_still_required_when_service_token_not_required(monkeypatch):
    monkeypatch.setattr(settings, 'tools_rest_require_service_token', False)
    resp = client.post('/api/v1/tools/search', json={'query': 'x'})
    assert resp.status_code == 401
    resp = client.post('/api/v1/tools/search', json={'query': 'x'}, headers={'Authorization': 'Bearer forged.token'})
    assert resp.status_code == 401


# --- WEAVE_DELEGATION_SECRET unconfigured: fail-closed 503, never a 200 ------
#
# Regression coverage for the fail-open bug in app/services/scope.py's
# _verify_delegation_token: an empty settings.weave_delegation_secret used
# to be handed straight to hmac.new(), which computes a perfectly valid
# signature for an empty key -- letting ANY caller who knows the (documented)
# wire format mint a self-signed token for arbitrary collections. These
# hit the real REST surface end-to-end, proving the 503 the unit tests in
# test_scope.py already establish actually reaches an HTTP caller instead of
# a 200 with search results.


def test_search_with_unconfigured_delegation_secret_and_syntactically_valid_token_is_503(monkeypatch):
    # The token itself is completely well-formed and was genuinely signed by
    # a properly-configured issuer -- exactly the "drift between two
    # deployments" scenario (Weave-Runtime still has the real secret,
    # Weave-Tools' own copy went missing). Must never resolve to 200.
    token = make_delegation_token(collections=['handbuch'])
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')

    resp = client.post('/api/v1/tools/search', json={'query': 'x'}, headers=_auth_headers(token))

    assert resp.status_code == 503
    assert resp.status_code != 200


def test_search_with_unconfigured_delegation_secret_and_self_forged_token_is_503(monkeypatch):
    # The actual exploit this closes: a token an attacker built themselves
    # (empty HMAC key, no inside knowledge needed beyond the documented wire
    # format), naming a collection they were never granted. Must be refused
    # at 503 -- never accepted, and never a 401 either (that would look like
    # "the token is malformed", masking that this deployment's OWN secret is
    # the actual problem).
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')
    forged = forge_delegation_token_with_empty_secret_key(collections=['streng-geheime-collection'])

    with patch('app.services.tools.httpx.post') as mock_post:
        resp = client.post(
            '/api/v1/tools/search',
            json={'query': 'geheim', 'collection': 'streng-geheime-collection'},
            headers=_auth_headers(forged),
        )

    assert resp.status_code == 503
    mock_post.assert_not_called()

    collections_resp = fake_response(200, [])
    with patch('app.services.tools.httpx.get', return_value=collections_resp) as mock_get:
        resp = client.get('/api/v1/tools/collections', headers=_auth_headers(forged))
    assert resp.status_code == 503
    mock_get.assert_not_called()


def test_search_with_configured_delegation_secret_still_works_after_the_fix():
    # The fail-closed check must never fire for a properly configured
    # deployment (the autouse fixture in conftest.py sets a real secret) --
    # same 200 end-to-end behaviour as before this fix.
    token = make_delegation_token(collections=['handbuch'])
    retrieval_resp = fake_response(200, {'results': []})

    with patch('app.services.tools.httpx.post', return_value=retrieval_resp):
        resp = client.post(
            '/api/v1/tools/search',
            json={'query': 'x', 'collection': 'handbuch'},
            headers=_auth_headers(token),
        )

    assert resp.status_code == 200


def test_delegation_secret_never_appears_in_the_503_response_body(monkeypatch):
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')
    forged = forge_delegation_token_with_empty_secret_key()
    resp = client.post('/api/v1/tools/search', json={'query': 'x'}, headers=_auth_headers(forged))

    assert resp.status_code == 503
    # There is no real secret configured to leak in this scenario -- checked
    # against a stand-in value to prove the response body is the fixed,
    # generic detail string, never anything built from
    # settings.weave_delegation_secret.
    assert 'a-real-secret' not in resp.text
    assert WEAVE_DELEGATION_SECRET not in resp.text


def test_search_service_gate_is_independent_of_authorization_header():
    # A perfectly valid Authorization/Scope but a MISSING service-token
    # header must still be refused -- the two credentials are checked
    # independently.
    token = make_delegation_token()
    resp = client.post('/api/v1/tools/search', json={'query': 'x'}, headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 401


# --- scope resolution on the REST surface ------------------------------------


def test_collections_without_authorization_header_is_401_even_with_valid_service_token():
    resp = client.get('/api/v1/tools/collections', headers=TOOLS_SERVICE_HEADERS)
    assert resp.status_code == 401


def test_collections_end_to_end_with_delegation_token():
    token = make_delegation_token(team='kundenservice', collections=['handbuch'])
    retrieval_resp = fake_response(
        200, [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None, 'public': True}]
    )
    with patch('app.services.tools.httpx.get', return_value=retrieval_resp):
        resp = client.get('/api/v1/tools/collections', headers=_auth_headers(token))

    assert resp.status_code == 200
    assert resp.json() == [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None}]


def test_search_out_of_scope_collection_returns_empty_200_not_an_error():
    token = make_delegation_token(collections=['handbuch'])
    with patch('app.services.tools.httpx.post') as mock_post:
        resp = client.post(
            '/api/v1/tools/search',
            json={'query': 'geheim', 'collection': 'streng-geheime-collection'},
            headers=_auth_headers(token),
        )

    assert resp.status_code == 200
    assert resp.json() == {'query': 'geheim', 'results': [], 'trace': None}
    mock_post.assert_not_called()


# --- a request-body team/user override is ignored -----------------------------


def test_search_body_team_and_user_fields_are_ignored():
    token = make_delegation_token(user_id='real-user', team='real-team', collections=['handbuch'])
    retrieval_resp = fake_response(200, {'results': []})

    with patch('app.services.tools.httpx.post', return_value=retrieval_resp) as mock_post:
        resp = client.post(
            '/api/v1/tools/search',
            json={
                'query': 'x',
                'team': 'attacker-team',
                'user_id': 'someone-else',
                'allowed_collections': ['every-collection-ever'],
            },
            headers=_auth_headers(token),
        )

    assert resp.status_code == 200
    body = mock_post.call_args.kwargs['json']
    # The scope's REAL team, from the token -- never the injected one.
    assert body['allowed_teams'] == ['real-team']
    # The scope's REAL allowed_collections, never the injected override.
    assert body['allowed_collections'] == ['handbuch']


# --- final_k, scores, metadata and trace ---------------------------------------


def test_search_final_k_above_top_k_is_422_and_never_reaches_retrieval():
    token = make_delegation_token(collections=['handbuch'])
    with patch('app.services.tools.httpx.post') as mock_post:
        resp = client.post(
            '/api/v1/tools/search',
            json={'query': 'x', 'top_k': 3, 'final_k': 5},
            headers=_auth_headers(token),
        )
    assert resp.status_code == 422
    mock_post.assert_not_called()


def test_search_forwards_final_k_and_maps_scores_metadata_and_trace():
    token = make_delegation_token(collections=['handbuch'])
    hit = {
        'chunk_id': 7,
        'document_id': 'doc-1',
        'text': 'Urlaub',
        'page_start': 2,
        'page_end': 3,
        'original_filename': 'handbuch.pdf',
        'collection': 'handbuch',
        'rank': 1,
        'heading_path': ['HR', 'Urlaub'],
        'document_version': 4,
        'chunk_index': 12,
        'title': 'Handbuch',
        'language': 'de',
        'document_type': 'policy',
        'tags': ['hr'],
        'source_kind': 'upload',
        'source_url': 'https://wiki.example/hr',
        'uploaded_at': '2026-01-01T00:00:00Z',
        'legal_status': 'approved',
        'scores': {'vector': 0.71, 'fulltext': 0.12, 'rrf': 0.031, 'rerank': 0.92},
    }
    trace = {
        'top_k': 20,
        'final_k': 5,
        'vector_candidates': 20,
        'fulltext_candidates': 8,
        'fused': 24,
        'reranked': 24,
        'returned': 1,
        'rerank_error': False,
        'timings_ms': {'total': 12.5},
        'embedding_model': 'not-copied',
    }
    retrieval_resp = fake_response(200, {'query': 'Urlaub', 'results': [hit], 'trace': trace})
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp) as mock_post:
        resp = client.post(
            '/api/v1/tools/search',
            json={'query': 'Urlaub', 'top_k': 20, 'final_k': 5},
            headers=_auth_headers(token),
        )

    assert resp.status_code == 200
    body = mock_post.call_args.kwargs['json']
    assert (body['top_k'], body['final_k']) == (20, 5)
    result = resp.json()['results'][0]
    assert result['rank'] == 1
    assert result['scores'] == {'vector': 0.71, 'fulltext': 0.12, 'rrf': 0.031, 'rerank': 0.92}
    source = result['source']
    assert source['page'] == '2-3'
    assert source['heading_path'] == ['HR', 'Urlaub']
    assert source['document_version'] == 4
    assert (source['page_start'], source['page_end'], source['chunk_index']) == (2, 3, 12)
    assert (source['title'], source['language'], source['document_type']) == ('Handbuch', 'de', 'policy')
    assert source['tags'] == ['hr']
    assert source['source_url'] == 'https://wiki.example/hr'
    assert source['fetched_at'] is None
    assert resp.json()['trace'] == {key: value for key, value in trace.items() if key != 'embedding_model'}


def test_search_tolerates_malformed_trace_and_scores():
    token = make_delegation_token(collections=['handbuch'])
    hit = {'chunk_id': 1, 'document_id': 'doc-1', 'text': 'x', 'scores': 'bogus'}
    retrieval_resp = fake_response(200, {'results': [hit], 'trace': 'bogus'})
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp):
        resp = client.post('/api/v1/tools/search', json={'query': 'x'}, headers=_auth_headers(token))

    assert resp.status_code == 200
    assert resp.json()['trace'] is None
    assert resp.json()['results'][0]['rank'] == 1
    assert resp.json()['results'][0]['scores'] == {'vector': None, 'fulltext': None, 'rrf': None, 'rerank': None}


def test_search_final_k_rejected_by_retrieval_is_422_not_500():
    # Only final_k sent, larger than Weave-Retrieval's own default top_k.
    token = make_delegation_token(collections=['handbuch'])
    retrieval_resp = fake_response(400, {'detail': 'final_k (30) must not exceed top_k (20)'}, method='POST')
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp):
        resp = client.post('/api/v1/tools/search', json={'query': 'x', 'final_k': 30}, headers=_auth_headers(token))
    assert resp.status_code == 422
    assert resp.json()['detail'] == 'final_k (30) must not exceed top_k (20)'


def test_search_source_url_is_http_only():
    token = make_delegation_token(collections=['handbuch'])
    hit = {'chunk_id': 1, 'document_id': 'doc-1', 'text': 'x', 'source_url': 'javascript:alert(1)'}
    retrieval_resp = fake_response(200, {'results': [hit]})
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp):
        resp = client.post('/api/v1/tools/search', json={'query': 'x'}, headers=_auth_headers(token))
    assert resp.status_code == 200
    assert resp.json()['results'][0]['source']['source_url'] is None
