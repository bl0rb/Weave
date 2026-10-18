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
    def __init__(self, collections, results) -> None:
        self._collections = collections
        self._results = results
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
        return _FakeResponse(200, {'results': self._results})


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
    def _install(collections, results=()):
        fake = _FakeRetrieval(collections, list(results))
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
                'text': 'Urlaubsantrag bis zum 15.',
                'source': {
                    'document': 'HR-Handbuch.pdf',
                    'document_id': 'doc-1',
                    'chunk_id': 7,
                    'page': '2-3',
                    'collection': 'hr',
                    'source_url': 'https://wiki.example/hr',
                },
            }
        ],
    }


def test_search_narrows_to_one_readable_collection(retrieval, caller):
    fake = retrieval([{'slug': 'hr'}, {'slug': 'it'}])
    client.post('/v1/search', json={'query': 'x', 'collection': 'it'}, headers=caller)
    assert fake.posts[0]['allowed_collections'] == ['it']


def test_search_in_an_unreadable_collection_is_empty_and_never_reaches_retrieval(retrieval, caller):
    fake = retrieval([{'slug': 'hr'}])
    response = client.post('/v1/search', json={'query': 'x', 'collection': 'secret'}, headers=caller)
    assert response.status_code == 200
    assert response.json() == {'query': 'x', 'results': []}
    assert fake.posts == []


def test_search_with_no_readable_collections_is_empty(retrieval, caller):
    fake = retrieval([])
    response = client.post('/v1/search', json={'query': 'x'}, headers=caller)
    assert response.json()['results'] == []
    assert fake.posts == []


def test_search_requires_authentication(retrieval):
    retrieval([{'slug': 'hr'}])
    assert client.post('/v1/search', json={'query': 'x'}).status_code == 401
