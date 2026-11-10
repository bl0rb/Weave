"""POST /v1/search (app/api/search.py) against a mocked Weave-Retrieval,
same `_client` seam as tests/test_collections_api.py."""

import pytest

from tests.conftest import auth_headers, client, make_user_with_token


class _FakeResponse:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeRetrieval:
    def __init__(self, collections, results, trace=None) -> None:
        self._collections = collections
        self._results = results
        self._trace = trace
        self.posts: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def get(self, path, params=None):
        return _FakeResponse(200, self._collections)

    def post(self, path, json=None):
        assert path == '/api/v1/search'
        self.posts.append(json)
        body = {'results': self._results}
        if self._trace is not None:
            body['trace'] = self._trace
        return _FakeResponse(200, body)


_HIT = {
    'chunk_id': 7,
    'document_id': 'doc-1',
    'text': 'Urlaubsantrag bis zum 15.',
    'page_start': 2,
    'page_end': 3,
    'original_filename': 'HR-Handbuch.pdf',
    'collection': 'hr',
    'source_url': 'https://wiki.example/hr',
}


@pytest.fixture
def retrieval(monkeypatch):
    def _install(collections, results=(), trace=None):
        fake = _FakeRetrieval(collections, list(results), trace)
        monkeypatch.setattr('app.services.retrieval_client._client', lambda: fake)
        return fake

    return _install


@pytest.fixture
def caller(db_session):
    _, raw_token = make_user_with_token(db_session, username='search-caller', team='HR')
    return auth_headers(raw_token)


def test_search_is_scoped_to_the_callers_readable_collections(retrieval, caller):
    fake = retrieval([{'slug': 'hr'}, {'slug': 'it'}], [_HIT])

    response = client.post('/v1/search', json={'query': 'Urlaub', 'top_k': 5}, headers=caller)

    assert response.status_code == 200
    assert fake.posts == [{'query': 'Urlaub', 'allowed_collections': ['hr', 'it'], 'allowed_teams': ['HR'], 'top_k': 5}]
    assert response.json() == {
        'query': 'Urlaub',
        'results': [
            {
                'rank': 1,
                'text': 'Urlaubsantrag bis zum 15.',
                'scores': {'vector': None, 'fulltext': None, 'rrf': None, 'rerank': None},
                'source': {
                    'document': 'HR-Handbuch.pdf',
                    'document_id': 'doc-1',
                    'chunk_id': 7,
                    'page': '2-3',
                    'collection': 'hr',
                    'source_url': 'https://wiki.example/hr',
                    'heading_path': [],
                    'document_version': None,
                    'page_start': 2,
                    'page_end': 3,
                    'chunk_index': None,
                    'title': None,
                    'language': None,
                    'document_type': None,
                    'tags': [],
                    'source_kind': None,
                    'uploaded_at': None,
                    'fetched_at': None,
                    'legal_status': None,
                },
            }
        ],
        'trace': None,
    }


def test_search_narrows_to_one_readable_collection(retrieval, caller):
    fake = retrieval([{'slug': 'hr'}, {'slug': 'it'}])
    client.post('/v1/search', json={'query': 'x', 'collection': 'it'}, headers=caller)
    assert fake.posts[0]['allowed_collections'] == ['it']


def test_search_in_an_unreadable_collection_is_empty_and_never_reaches_retrieval(retrieval, caller):
    fake = retrieval([{'slug': 'hr'}])
    response = client.post('/v1/search', json={'query': 'x', 'collection': 'secret'}, headers=caller)
    assert response.status_code == 200
    assert response.json() == {'query': 'x', 'results': [], 'trace': None}
    assert fake.posts == []


def test_search_with_no_readable_collections_is_empty(retrieval, caller):
    fake = retrieval([])
    response = client.post('/v1/search', json={'query': 'x'}, headers=caller)
    assert response.json()['results'] == []
    assert fake.posts == []


def test_search_requires_authentication(retrieval):
    retrieval([{'slug': 'hr'}])
    assert client.post('/v1/search', json={'query': 'x'}).status_code == 401


def test_search_final_k_above_top_k_is_422_and_never_reaches_retrieval(retrieval, caller):
    fake = retrieval([{'slug': 'hr'}])
    response = client.post('/v1/search', json={'query': 'x', 'top_k': 3, 'final_k': 5}, headers=caller)
    assert response.status_code == 422
    assert fake.posts == []


def test_search_forwards_final_k_and_maps_scores_metadata_and_trace(retrieval, caller):
    hit = {
        **_HIT,
        'rank': 1,
        'heading_path': ['HR', 'Urlaub'],
        'document_version': 4,
        'chunk_index': 12,
        'title': 'HR-Handbuch',
        'language': 'de',
        'document_type': 'policy',
        'tags': ['hr'],
        'source_kind': 'confluence',
        'fetched_at': '2026-01-01T00:00:00Z',
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
    fake = retrieval([{'slug': 'hr'}], [hit], trace)

    response = client.post('/v1/search', json={'query': 'Urlaub', 'top_k': 20, 'final_k': 5}, headers=caller)

    assert response.status_code == 200
    assert fake.posts[0]['final_k'] == 5
    result = response.json()['results'][0]
    assert result['rank'] == 1
    assert result['scores'] == {'vector': 0.71, 'fulltext': 0.12, 'rrf': 0.031, 'rerank': 0.92}
    source = result['source']
    assert source['heading_path'] == ['HR', 'Urlaub']
    assert (source['document_version'], source['chunk_index']) == (4, 12)
    assert (source['title'], source['language'], source['document_type']) == ('HR-Handbuch', 'de', 'policy')
    assert source['tags'] == ['hr']
    assert (source['source_kind'], source['legal_status']) == ('confluence', 'approved')
    assert response.json()['trace'] == {key: value for key, value in trace.items() if key != 'embedding_model'}


def test_search_tolerates_malformed_trace(retrieval, caller):
    retrieval([{'slug': 'hr'}], [_HIT], 'bogus')
    response = client.post('/v1/search', json={'query': 'x'}, headers=caller)
    assert response.status_code == 200
    assert response.json()['trace'] is None


def test_search_final_k_rejected_by_retrieval_is_422_not_502(retrieval, caller, monkeypatch):
    # Only final_k sent, larger than Weave-Retrieval's own default top_k:
    # its 400 is the caller's error, not a gateway failure.
    fake = retrieval([{'slug': 'hr'}])
    monkeypatch.setattr(
        fake, 'post', lambda path, json=None: _FakeResponse(400, {'detail': 'final_k (30) must not exceed top_k (20)'})
    )
    response = client.post('/v1/search', json={'query': 'x', 'final_k': 30}, headers=caller)
    assert response.status_code == 422
    assert response.json()['detail'] == 'final_k (30) must not exceed top_k (20)'
