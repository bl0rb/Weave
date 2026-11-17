"""GET /v1/sources/passage (app/api/sources.py) against a mocked
Weave-Retrieval, same `_client` seam as tests/test_search_api.py."""

import uuid

import httpx
import pytest

from app.core import ratelimit
from app.core.ratelimit import FixedWindowRateLimiter
from tests.conftest import auth_headers, client, make_user_with_token

DOCUMENT_ID = str(uuid.uuid4())

PASSAGE = {
    'document_id': DOCUMENT_ID,
    'source_job_id': 'job-1',
    'title': 'Reiserichtlinie',
    'original_filename': 'Reiserichtlinie.pdf',
    'document_version': 3,
    'source_kind': 'upload',
    'source_url': 'https://wiki.example/reisen',
    'collection': 'hr',
    'target_chunk_id': 7,
    'chunks': [
        {'chunk_id': 6, 'chunk_index': 3, 'heading_path': ['Reisekosten'], 'page_start': 2, 'page_end': 2,
         'text': 'Vorher.', 'is_target': False},
        {'chunk_id': 7, 'chunk_index': 4, 'heading_path': ['Reisekosten', 'Tagegeld'], 'page_start': 2,
         'page_end': 3, 'text': 'Das Tagegeld betraegt 28 Euro.', 'is_target': True},
        {'chunk_id': 8, 'chunk_index': 5, 'heading_path': None, 'page_start': None, 'page_end': None,
         'text': 'Nachher.', 'is_target': False},
    ],
}


class _Response:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeRetrieval:
    def __init__(self, collections, passage_status=200, passage_payload=None, passage_error=None) -> None:
        self.collections = collections
        self.passage_status = passage_status
        self.passage_payload = PASSAGE if passage_payload is None else passage_payload
        self.passage_error = passage_error
        self.gets: list[tuple[str, dict | None]] = []
        self.posts: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def get(self, path, params=None):
        self.gets.append((path, params))
        return _Response(200, self.collections)

    def post(self, path, json=None):
        self.posts.append((path, json))
        if self.passage_error is not None:
            raise self.passage_error
        return _Response(self.passage_status, self.passage_payload)


@pytest.fixture
def retrieval(monkeypatch):
    def _install(collections=({'slug': 'hr'}, {'slug': 'it'}), **kwargs):
        fake = _FakeRetrieval(list(collections), **kwargs)
        monkeypatch.setattr('app.services.retrieval_client._client', lambda: fake)
        return fake

    return _install


def _get(token: str, **params):
    query = {'document_id': DOCUMENT_ID, 'chunk_id': '7', **params}
    return client.get('/v1/sources/passage', params=query, headers=auth_headers(token))


def test_passage_requires_authentication():
    response = client.get('/v1/sources/passage', params={'document_id': DOCUMENT_ID, 'chunk_id': 7})
    assert response.status_code == 401


def test_passage_proxies_with_the_callers_scope_and_passes_the_response_through(db_session, retrieval):
    _, token = make_user_with_token(db_session, username='passage-ok', team='Personal')
    fake = retrieval()

    response = _get(token, context='2')

    assert response.status_code == 200
    body = response.json()
    assert body['document_id'] == DOCUMENT_ID
    assert body['source_job_id'] == 'job-1'
    assert body['title'] == 'Reiserichtlinie'
    assert body['target_chunk_id'] == 7
    assert [chunk['chunk_id'] for chunk in body['chunks']] == [6, 7, 8]
    assert [chunk['is_target'] for chunk in body['chunks']] == [False, True, False]
    assert body['chunks'][2]['heading_path'] == []
    assert body['source_url'] == 'https://wiki.example/reisen'

    assert fake.gets == [('/api/v1/collections', {'teams': ['Personal']})]
    assert fake.posts == [
        (
            '/api/v1/passage',
            {
                'document_id': DOCUMENT_ID,
                'chunk_id': 7,
                'context': 2,
                'allowed_collections': ['hr', 'it', '__none__'],
                'allowed_teams': ['Personal'],
            },
        )
    ]


def test_passage_defaults_to_one_chunk_of_context(db_session, retrieval):
    _, token = make_user_with_token(db_session, username='passage-default')
    fake = retrieval()

    assert _get(token).status_code == 200
    assert fake.posts[0][1]['context'] == 1


def test_passage_for_a_caller_without_readable_collections_still_asks_with_the_sentinel(db_session, retrieval):
    """Legacy documents without a collection stay reachable, team-gated."""
    _, token = make_user_with_token(db_session, username='passage-none')
    fake = retrieval(collections=[])

    assert _get(token).status_code == 200
    assert fake.posts[0][1]['allowed_collections'] == ['__none__']
    assert fake.posts[0][1]['allowed_teams'] == []


def test_passage_canonicalises_the_document_id(db_session, retrieval):
    _, token = make_user_with_token(db_session, username='passage-canonical')
    fake = retrieval()

    assert _get(token, document_id=DOCUMENT_ID.upper()).status_code == 200
    assert fake.posts[0][1]['document_id'] == DOCUMENT_ID


@pytest.mark.parametrize(
    'params',
    [
        {'document_id': 'not-a-uuid'},
        {'document_id': "'; DROP TABLE documents; --"},
        {'chunk_id': 'abc'},
        {'chunk_id': '-1'},
        {'chunk_id': str(2**63)},
    ],
)
def test_passage_with_malformed_ids_is_404_without_asking_retrieval(db_session, retrieval, params):
    _, token = make_user_with_token(db_session, username='passage-malformed')
    fake = retrieval()

    response = _get(token, **params)

    assert response.status_code == 404
    assert response.json()['detail'] == 'Passage not found'
    assert fake.posts == []


@pytest.mark.parametrize('context', ['-1', '4', 'many'])
def test_passage_rejects_an_out_of_range_context(db_session, retrieval, context):
    _, token = make_user_with_token(db_session, username='passage-context')
    retrieval()

    assert _get(token, context=context).status_code == 422


def test_passage_not_visible_or_not_found_upstream_is_404(db_session, retrieval):
    _, token = make_user_with_token(db_session, username='passage-404')
    retrieval(passage_status=404, passage_payload={'detail': 'Passage not found'})

    response = _get(token)

    assert response.status_code == 404
    assert response.json()['detail'] == 'Passage not found'


@pytest.mark.parametrize('status_code', [401, 500, 503])
def test_passage_upstream_failure_is_502(db_session, retrieval, status_code):
    _, token = make_user_with_token(db_session, username='passage-5xx')
    retrieval(passage_status=status_code, passage_payload={'detail': 'boom'})

    assert _get(token).status_code == 502


def test_passage_retrieval_unreachable_is_502_without_leaking_the_host(db_session, retrieval):
    _, token = make_user_with_token(db_session, username='passage-down')
    retrieval(passage_error=httpx.ConnectError('retrieval:8002 refused'))

    response = _get(token)

    assert response.status_code == 502
    assert 'retrieval:8002' not in response.text


@pytest.mark.parametrize('payload', [ValueError('not json'), ['a list'], {'document_id': DOCUMENT_ID}])
def test_passage_invalid_upstream_body_is_502(db_session, retrieval, payload):
    _, token = make_user_with_token(db_session, username='passage-invalid')
    retrieval(passage_payload=payload)

    assert _get(token).status_code == 502


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///etc/passwd', 'ftp://example.org/x'])
def test_passage_drops_non_http_source_urls(db_session, retrieval, url):
    _, token = make_user_with_token(db_session, username='passage-url')
    retrieval(passage_payload={**PASSAGE, 'source_url': url})

    response = _get(token)

    assert response.status_code == 200
    assert response.json()['source_url'] is None


def test_passage_is_rate_limited(db_session, retrieval, monkeypatch):
    _, token = make_user_with_token(db_session, username='passage-ratelimit')
    retrieval()
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=1))

    assert _get(token).status_code == 200
    assert _get(token).status_code == 429
