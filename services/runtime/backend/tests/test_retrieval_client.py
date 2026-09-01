"""Unit tests for app.services.retrieval_client: `search()`'s outgoing
request body (matching Weave-Retrieval's own SearchRequest shape), response
mapping to RetrievedChunk (including per-signal scores) and to Collection,
`list_collections()`'s own request/response shape, and error classification
for both -- all with httpx.post/httpx.get mocked, no real HTTP calls
(mirrors Weave-Retrieval's own tests/test_reranker.py:_FakeResponse
pattern).
"""

from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.services.retrieval_client import (
    Collection,
    RetrievalError,
    RetrievalUnavailable,
    RetrievedChunk,
    RetrievedChunkScores,
    list_collections,
    search,
)


class _FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


def _search_result(**overrides) -> dict:
    result = {
        'chunk_id': 1,
        'document_id': 'doc-1',
        'text': 'some retrieved text',
        'heading_path': ['Section', 'Subsection'],
        'page_start': 3,
        'page_end': 4,
        'document_version': 2,
        'source': 'confluence',
        'original_filename': 'handbook.pdf',
        'team': 'legal',
        'department': 'legal',
        'collection': 'vertraege',
        'scores': {'vector': 0.8, 'fulltext': 0.5, 'rrf': 0.6, 'rerank': 0.9},
        'embedding_model': 'fake-embed',
    }
    result.update(overrides)
    return result


def _search_response(*results: dict) -> dict:
    return {
        'query': 'query text',
        'results': list(results),
        'trace': {'vector_candidates': 0, 'fulltext_candidates': 0, 'fused': 0, 'reranked': 0},
    }


@pytest.fixture(autouse=True)
def _retrieval_settings(monkeypatch):
    monkeypatch.setattr(settings, 'retrieval_base_url', 'https://retrieval.example.com')
    monkeypatch.setattr(settings, 'retrieval_api_token', 'retrieval-token')
    monkeypatch.setattr(settings, 'retrieval_timeout_seconds', 5.0)


# --- request body --------------------------------------------------------------

def test_search_sends_request_body_matching_the_retrieval_schema():
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(200, _search_response())) as mock_post:
        search(
            'kuendigungsfrist',
            filters={'department': 'legal'},
            allowed_teams=['legal'],
            allowed_collections=['vertraege'],
            top_k=20,
            final_k=5,
        )

    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == 'https://retrieval.example.com/api/v1/search'
    assert mock_post.call_args.kwargs['headers']['Authorization'] == 'Bearer retrieval-token'
    assert mock_post.call_args.kwargs['timeout'] == 5.0
    sent_json = mock_post.call_args.kwargs['json']
    assert sent_json == {
        'query': 'kuendigungsfrist',
        'filters': {'department': 'legal'},
        'allowed_teams': ['legal'],
        'allowed_collections': ['vertraege'],
        'top_k': 20,
        'final_k': 5,
    }


def test_search_sends_none_for_unset_optional_fields():
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(200, _search_response())) as mock_post:
        search('a query')

    sent_json = mock_post.call_args.kwargs['json']
    assert sent_json == {
        'query': 'a query',
        'filters': None,
        'allowed_teams': None,
        'allowed_collections': None,
        'top_k': None,
        'final_k': None,
    }


def test_search_strips_trailing_slash_from_base_url(monkeypatch):
    monkeypatch.setattr(settings, 'retrieval_base_url', 'https://retrieval.example.com/')
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(200, _search_response())) as mock_post:
        search('q')
    assert mock_post.call_args.args[0] == 'https://retrieval.example.com/api/v1/search'


# --- response mapping ------------------------------------------------------------

def test_search_maps_response_into_retrieved_chunks_including_scores():
    payload = _search_response(_search_result())
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(200, payload)):
        chunks = search('q')

    assert chunks == [
        RetrievedChunk(
            chunk_id=1,
            document_id='doc-1',
            text='some retrieved text',
            source='confluence',
            original_filename='handbook.pdf',
            page_start=3,
            page_end=4,
            document_version=2,
            heading_path=['Section', 'Subsection'],
            scores=RetrievedChunkScores(vector=0.8, fulltext=0.5, rrf=0.6, rerank=0.9),
            collection='vertraege',
        )
    ]


def test_search_maps_multiple_results_preserving_order():
    payload = _search_response(
        _search_result(chunk_id=1, text='first'),
        _search_result(chunk_id=2, text='second'),
    )
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(200, payload)):
        chunks = search('q')
    assert [chunk.chunk_id for chunk in chunks] == [1, 2]
    assert [chunk.text for chunk in chunks] == ['first', 'second']


def test_search_defaults_missing_optional_fields():
    result = _search_result()
    del result['source']
    del result['original_filename']
    del result['page_start']
    del result['page_end']
    del result['heading_path']
    del result['scores']
    del result['collection']
    payload = _search_response(result)

    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(200, payload)):
        chunks = search('q')

    chunk = chunks[0]
    assert chunk.source is None
    assert chunk.original_filename is None
    assert chunk.page_start is None
    assert chunk.page_end is None
    assert chunk.heading_path == []
    assert chunk.scores == RetrievedChunkScores(vector=None, fulltext=None, rrf=None, rerank=None)
    assert chunk.collection is None


def test_search_no_results_returns_empty_list():
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(200, _search_response())):
        assert search('q') == []


# --- error classification --------------------------------------------------------

def test_search_503_raises_retrieval_unavailable():
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(503, text='down')) as mock_post:
        with pytest.raises(RetrievalUnavailable) as exc_info:
            search('q')
    assert mock_post.call_count == 1
    assert exc_info.value.status_code == 503


def test_search_422_raises_retrieval_error_with_detail():
    payload = {'detail': "final_k (10) must not exceed top_k (5)"}
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(422, payload)) as mock_post:
        with pytest.raises(RetrievalError) as exc_info:
            search('q', top_k=5, final_k=10)
    assert mock_post.call_count == 1
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == 'final_k (10) must not exceed top_k (5)'


def test_search_422_stringifies_non_string_detail():
    payload = {'detail': [{'loc': ['body', 'query'], 'msg': 'field required', 'type': 'missing'}]}
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(422, payload)):
        with pytest.raises(RetrievalError) as exc_info:
            search('q')
    assert 'field required' in exc_info.value.detail


def test_search_401_raises_retrieval_error():
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(401, text='invalid service token')):
        with pytest.raises(RetrievalError) as exc_info:
            search('q')
    assert exc_info.value.status_code == 401


def test_search_timeout_raises_retrieval_unavailable_without_retry():
    with patch('app.services.retrieval_client.httpx.post', side_effect=httpx.TimeoutException('timed out')) as mock_post:
        with pytest.raises(RetrievalUnavailable):
            search('q')
    assert mock_post.call_count == 1


def test_search_connection_error_raises_retrieval_unavailable():
    with patch('app.services.retrieval_client.httpx.post', side_effect=httpx.ConnectError('refused')):
        with pytest.raises(RetrievalUnavailable):
            search('q')


def test_search_malformed_200_body_raises_retrieval_unavailable():
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(200, {'no_results_key': True})):
        with pytest.raises(RetrievalUnavailable):
            search('q')


def test_search_non_json_200_body_raises_retrieval_unavailable():
    with patch('app.services.retrieval_client.httpx.post', return_value=_FakeResponse(200, None, text='not json')):
        with pytest.raises(RetrievalUnavailable):
            search('q')


# --- list_collections --------------------------------------------------------


def _collection_out(**overrides) -> dict:
    collection = {'slug': 'vertraege', 'name': 'Vertraege', 'description': 'Vertragsdokumente', 'public': False}
    collection.update(overrides)
    return collection


def test_list_collections_sends_the_team_query_param():
    with patch('app.services.retrieval_client.httpx.get', return_value=_FakeResponse(200, [])) as mock_get:
        list_collections('legal')

    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == 'https://retrieval.example.com/api/v1/collections'
    assert mock_get.call_args.kwargs['headers']['Authorization'] == 'Bearer retrieval-token'
    assert mock_get.call_args.kwargs['params'] == {'team': 'legal'}
    assert mock_get.call_args.kwargs['timeout'] == 5.0


def test_list_collections_omits_the_team_param_entirely_when_team_is_none():
    # Not the same as `?team=` -- see list_collections' own docstring for
    # why "no team context at all" and "an empty team string" must not
    # collapse into the same request.
    with patch('app.services.retrieval_client.httpx.get', return_value=_FakeResponse(200, [])) as mock_get:
        list_collections(None)

    assert mock_get.call_args.kwargs['params'] is None


def test_list_collections_strips_trailing_slash_from_base_url(monkeypatch):
    monkeypatch.setattr(settings, 'retrieval_base_url', 'https://retrieval.example.com/')
    with patch('app.services.retrieval_client.httpx.get', return_value=_FakeResponse(200, [])) as mock_get:
        list_collections('legal')
    assert mock_get.call_args.args[0] == 'https://retrieval.example.com/api/v1/collections'


def test_list_collections_maps_response_into_collections():
    payload = [
        _collection_out(slug='vertraege', name='Vertraege', description='Vertragsdokumente', public=False),
        _collection_out(slug='handbuch', name='Mitarbeiterhandbuch', description=None, public=True),
    ]
    with patch('app.services.retrieval_client.httpx.get', return_value=_FakeResponse(200, payload)):
        collections = list_collections('legal')

    assert collections == [
        Collection(slug='vertraege', name='Vertraege', description='Vertragsdokumente', public=False),
        Collection(slug='handbuch', name='Mitarbeiterhandbuch', description=None, public=True),
    ]


def test_list_collections_no_results_returns_empty_list():
    with patch('app.services.retrieval_client.httpx.get', return_value=_FakeResponse(200, [])):
        assert list_collections('legal') == []


def test_list_collections_503_raises_retrieval_unavailable():
    with patch('app.services.retrieval_client.httpx.get', return_value=_FakeResponse(503, text='down')):
        with pytest.raises(RetrievalUnavailable) as exc_info:
            list_collections('legal')
    assert exc_info.value.status_code == 503


def test_list_collections_401_raises_retrieval_error():
    with patch('app.services.retrieval_client.httpx.get', return_value=_FakeResponse(401, text='invalid service token')):
        with pytest.raises(RetrievalError) as exc_info:
            list_collections('legal')
    assert exc_info.value.status_code == 401


def test_list_collections_connection_error_raises_retrieval_unavailable():
    with patch('app.services.retrieval_client.httpx.get', side_effect=httpx.ConnectError('refused')):
        with pytest.raises(RetrievalUnavailable):
            list_collections('legal')


def test_list_collections_non_list_200_body_raises_retrieval_unavailable():
    with patch('app.services.retrieval_client.httpx.get', return_value=_FakeResponse(200, {'not': 'a list'})):
        with pytest.raises(RetrievalUnavailable):
            list_collections('legal')


def test_list_collections_malformed_item_raises_retrieval_unavailable():
    with patch('app.services.retrieval_client.httpx.get', return_value=_FakeResponse(200, [{'slug': 'vertraege'}])):
        with pytest.raises(RetrievalUnavailable):
            list_collections('legal')
