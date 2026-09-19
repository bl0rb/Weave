"""app/services/tools.py -- the shared implementation behind both the MCP
tools and the REST mirror: collection-scope intersection, the search
request actually shaped/sent to Weave-Retrieval, and result mapping.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from app.services.scope import Scope
from app.services.tools import list_collections_for_scope, search_for_scope
from tests.conftest import fake_response

_PERSONAL_SCOPE = Scope(
    kind='personal',
    user_id='user-1',
    username='alice',
    team='kundenservice',
    allowed_collections=['handbuch', 'faq'],
)


# --- list_collections_for_scope ----------------------------------------------


def test_list_collections_filters_to_allowed_slugs():
    retrieval_resp = fake_response(
        200,
        [
            {'slug': 'handbuch', 'name': 'Handbuch', 'description': 'Das Handbuch', 'public': True},
            {'slug': 'faq', 'name': 'FAQ', 'description': None, 'public': True},
            # Readable by the team per Weave-Retrieval, but NOT in this
            # caller's own resolved scope (e.g. a Delegations-Token loaned a
            # narrower set than the whole team can read) -- must never
            # surface.
            {'slug': 'geheim', 'name': 'Geheime Ablage', 'description': None, 'public': False},
        ],
    )
    with patch('app.services.tools.httpx.get', return_value=retrieval_resp) as mock_get:
        result = list_collections_for_scope(_PERSONAL_SCOPE)

    slugs = {c.slug for c in result}
    assert slugs == {'handbuch', 'faq'}
    assert 'geheim' not in slugs

    _, kwargs = mock_get.call_args
    assert kwargs['params'] == {'team': 'kundenservice'}
    assert kwargs['headers']['Authorization'] == 'Bearer test-retrieval-api-token'


def test_list_collections_omits_team_param_when_scope_has_no_team():
    scope = Scope(kind='delegated', user_id='u', username='u', team=None, allowed_collections=['handbuch'])
    retrieval_resp = fake_response(200, [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None, 'public': True}])

    with patch('app.services.tools.httpx.get', return_value=retrieval_resp) as mock_get:
        list_collections_for_scope(scope)

    _, kwargs = mock_get.call_args
    assert kwargs['params'] == {}


def test_list_collections_never_returns_no_collection_sentinel_as_a_row():
    scope = Scope(
        kind='delegated', user_id='u', username='u', team=None, allowed_collections=['__none__', 'handbuch']
    )
    retrieval_resp = fake_response(200, [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None, 'public': True}])
    with patch('app.services.tools.httpx.get', return_value=retrieval_resp):
        result = list_collections_for_scope(scope)
    assert [c.slug for c in result] == ['handbuch']


# --- search_for_scope: out-of-scope collection --------------------------------


def test_search_with_out_of_scope_collection_returns_empty_without_calling_retrieval():
    with patch('app.services.tools.httpx.post') as mock_post:
        result = search_for_scope(_PERSONAL_SCOPE, query='hallo', collection='fremde-collection', top_k=None)

    assert result.query == 'hallo'
    assert result.results == []
    mock_post.assert_not_called()


# --- search_for_scope: request shaping ----------------------------------------


def test_search_sends_full_scope_allowed_collections_when_no_collection_given():
    retrieval_resp = fake_response(200, {'results': []})
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp) as mock_post:
        search_for_scope(_PERSONAL_SCOPE, query='hallo', collection=None, top_k=None)

    _, kwargs = mock_post.call_args
    body = kwargs['json']
    assert body['query'] == 'hallo'
    assert body['allowed_collections'] == ['handbuch', 'faq']
    assert body['allowed_teams'] == ['kundenservice']
    assert 'top_k' not in body
    assert kwargs['headers']['Authorization'] == 'Bearer test-retrieval-api-token'


def test_search_intersects_requested_collection_into_a_single_element_list():
    retrieval_resp = fake_response(200, {'results': []})
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp) as mock_post:
        search_for_scope(_PERSONAL_SCOPE, query='hallo', collection='faq', top_k=None)

    _, kwargs = mock_post.call_args
    assert kwargs['json']['allowed_collections'] == ['faq']


def test_search_forwards_top_k_only_when_given():
    retrieval_resp = fake_response(200, {'results': []})
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp) as mock_post:
        search_for_scope(_PERSONAL_SCOPE, query='hallo', collection=None, top_k=3)

    assert mock_post.call_args.kwargs['json']['top_k'] == 3


def test_search_with_teamless_scope_sends_empty_allowed_teams_list_not_none():
    scope = Scope(kind='delegated', user_id='u', username='u', team=None, allowed_collections=['handbuch'])
    retrieval_resp = fake_response(200, {'results': []})
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp) as mock_post:
        search_for_scope(scope, query='hallo', collection=None, top_k=None)

    body = mock_post.call_args.kwargs['json']
    # Weave-Retrieval's own SearchRequest.allowed_teams is `list[str] | None`
    # with `None` meaning "no restriction at all" -- a meaning this service
    # must never forward. `[None]` is not even a valid `list[str]` either.
    assert body['allowed_teams'] == []
    assert None not in body['allowed_teams']


def test_search_for_granted_technical_scope_does_not_zero_itself_out_via_allowed_teams():
    """Regression test for the Round-2 Schritt-5 finding: a technical
    identity's Scope always has `team=None`/`teams=None` (see
    contracts/technical-identities.md), so naively forwarding
    `scope.effective_teams` (`[]`) as `allowed_teams` would make
    Weave-Retrieval's own `apply_filters()` AND that empty team list with
    `allowed_collections`, and an empty `allowed_teams` there means "no team
    authorized" -> zero rows via `IN()`, REGARDLESS of which collections
    were granted (see services/retrieval/backend/app/services/search.py).
    A granted collection must actually be able to surface hits, so
    `allowed_teams` must be `None` ("no team restriction") for a technical
    identity, never `[]`.
    """
    retrieval_resp = fake_response(
        200, {'results': [{'chunk_id': 1, 'document_id': 'doc-1', 'text': 'x', 'page_start': None, 'page_end': None}]}
    )
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp) as mock_post:
        result = search_for_scope(_TECHNICAL_SCOPE, query='hallo', collection='handbuch', top_k=None)

    body = mock_post.call_args.kwargs['json']
    # The bug: `[]` here is indistinguishable from "authorized for zero
    # teams" and would silently zero out every technical-identity search no
    # matter what `allowed_collections` says.
    assert body['allowed_teams'] is None
    # And the actual hit Weave-Retrieval returned still surfaces to the caller.
    assert len(result.results) == 1


# --- search_for_scope: result mapping -----------------------------------------


def test_search_maps_hits_to_text_and_source():
    retrieval_resp = fake_response(
        200,
        {
            'results': [
                {
                    'chunk_id': 1,
                    'document_id': 'doc-1',
                    'text': 'Die Kundennummer steht oben rechts.',
                    'page_start': 3,
                    'page_end': 3,
                    'original_filename': 'handbuch.pdf',
                    'collection': 'handbuch',
                },
                {
                    'chunk_id': 2,
                    'document_id': 'doc-2',
                    'text': 'Mehrseitiger Abschnitt.',
                    'page_start': 5,
                    'page_end': 7,
                    'original_filename': None,
                    'collection': 'handbuch',
                },
            ]
        },
    )
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp):
        result = search_for_scope(_PERSONAL_SCOPE, query='kundennummer', collection='handbuch', top_k=None)

    assert len(result.results) == 2

    first = result.results[0]
    assert first.text == 'Die Kundennummer steht oben rechts.'
    assert first.source.document == 'handbuch.pdf'
    assert first.source.document_id == 'doc-1'
    assert first.source.chunk_id == 1
    assert first.source.page == '3'
    assert first.source.collection == 'handbuch'

    second = result.results[1]
    # No original_filename -- falls back to the document id.
    assert second.source.document == 'doc-2'
    # A page range is rendered "start-end", not just the start page.
    assert second.source.page == '5-7'


def test_search_source_collection_reflects_the_hits_own_value_when_query_was_not_scoped_to_one():
    retrieval_resp = fake_response(
        200,
        {
            'results': [
                {
                    'chunk_id': 1,
                    'document_id': 'doc-1',
                    'text': 'x',
                    'page_start': None,
                    'page_end': None,
                    'collection': 'faq',
                }
            ]
        },
    )
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp):
        result = search_for_scope(_PERSONAL_SCOPE, query='x', collection=None, top_k=None)

    # Even though the request wasn't scoped to a single collection, the
    # hit's own actual collection is echoed through -- not None.
    assert result.results[0].source.collection == 'faq'
    assert result.results[0].source.document_id == 'doc-1'
    assert result.results[0].source.chunk_id == 1
    assert result.results[0].source.page is None


def test_search_source_collection_is_none_when_the_hit_itself_has_none():
    retrieval_resp = fake_response(
        200,
        {'results': [{'chunk_id': 1, 'document_id': 'doc-1', 'text': 'x', 'page_start': None, 'page_end': None}]},
    )
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp):
        result = search_for_scope(_PERSONAL_SCOPE, query='x', collection=None, top_k=None)

    assert result.results[0].source.collection is None
    assert result.results[0].source.page is None


# --- audit logging (Schritt 5) ------------------------------------------------

_TECHNICAL_SCOPE = Scope(
    kind='technical', user_id='ident-1', username='n8n-integration', team=None, allowed_collections=['handbuch'],
)


def test_search_denied_out_of_scope_collection_is_audited(caplog):
    with caplog.at_level(logging.INFO, logger='app.services.tools'):
        with patch('app.services.tools.httpx.post') as mock_post:
            search_for_scope(_TECHNICAL_SCOPE, query='hallo', collection='fremde-collection', top_k=None)
    mock_post.assert_not_called()
    [record] = [r for r in caplog.records if 'tools call' in r.getMessage()]
    message = record.getMessage()
    assert 'kind=technical' in message
    assert 'caller=ident-1' in message
    assert 'collection=fremde-collection' in message
    assert 'denied=True' in message


def test_search_success_is_audited_with_result_count(caplog):
    retrieval_resp = fake_response(
        200, {'results': [{'chunk_id': 1, 'document_id': 'doc-1', 'text': 'x', 'page_start': None, 'page_end': None}]}
    )
    with caplog.at_level(logging.INFO, logger='app.services.tools'):
        with patch('app.services.tools.httpx.post', return_value=retrieval_resp):
            search_for_scope(_TECHNICAL_SCOPE, query='hallo', collection='handbuch', top_k=None)
    [record] = [r for r in caplog.records if 'tools call' in r.getMessage()]
    message = record.getMessage()
    assert 'result_count=1' in message
    assert 'denied=False' in message
    # Never leaks the hit's own text into the audit line.
    assert 'x' not in message or 'text' not in message


def test_list_collections_is_audited(caplog):
    retrieval_resp = fake_response(200, [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None}])
    with caplog.at_level(logging.INFO, logger='app.services.tools'):
        with patch('app.services.tools.httpx.get', return_value=retrieval_resp):
            list_collections_for_scope(_TECHNICAL_SCOPE)
    [record] = [r for r in caplog.records if 'tools call' in r.getMessage()]
    assert 'tool=list_collections' in record.getMessage()
    assert 'result_count=1' in record.getMessage()
