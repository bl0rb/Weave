"""Tests for app.services.reranker: NoopReranker, FakeReranker,
HttpReranker (with httpx.post mocked -- no real HTTP calls), and
get_reranker().
"""

from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.services.reranker import (
    FakeReranker,
    HttpReranker,
    NoopReranker,
    RerankCandidate,
    RerankError,
    get_reranker,
)


def _candidates(*texts: str) -> list[RerankCandidate]:
    return [RerankCandidate(chunk_id=i, text=text) for i, text in enumerate(texts)]


# --- NoopReranker -------------------------------------------------------------

def test_noop_reranker_preserves_input_order():
    candidates = _candidates('alpha', 'beta', 'gamma')
    result = NoopReranker().rerank('query', candidates)
    assert [pair[0] for pair in result] == candidates


def test_noop_reranker_scores_are_none():
    candidates = _candidates('alpha', 'beta')
    result = NoopReranker().rerank('query', candidates)
    assert [pair[1] for pair in result] == [None, None]


def test_noop_reranker_empty_candidates():
    assert NoopReranker().rerank('query', []) == []


# --- FakeReranker ---------------------------------------------------------------

def test_fake_reranker_is_deterministic():
    candidates = _candidates('the cat sat on the mat', 'a completely unrelated sentence')
    first = FakeReranker().rerank('cat mat', candidates)
    second = FakeReranker().rerank('cat mat', candidates)
    assert [c.text for c, _ in first] == [c.text for c, _ in second]
    assert [score for _, score in first] == [score for _, score in second]


def test_fake_reranker_orders_by_token_overlap_descending():
    candidates = _candidates(
        'no overlap here whatsoever',            # 0 overlapping tokens with the query
        'kuendigungsfrist betraegt zwei wochen',  # 2 overlapping tokens
        'kuendigungsfrist',                       # 1 overlapping token
    )
    result = FakeReranker().rerank('kuendigungsfrist wochen', candidates)
    assert [c.text for c, _ in result] == [
        'kuendigungsfrist betraegt zwei wochen',
        'kuendigungsfrist',
        'no overlap here whatsoever',
    ]
    assert [score for _, score in result] == [2.0, 1.0, 0.0]


def test_fake_reranker_is_case_insensitive():
    candidates = _candidates('KUENDIGUNGSFRIST')
    result = FakeReranker().rerank('kuendigungsfrist', candidates)
    assert result[0][1] == 1.0


def test_fake_reranker_ties_keep_input_order():
    # Both candidates share exactly one token with the query ("wochen") --
    # equal scores must not swap their relative (input) order.
    candidates = _candidates('wochen eins', 'wochen zwei')
    result = FakeReranker().rerank('wochen', candidates)
    assert [c.text for c, _ in result] == ['wochen eins', 'wochen zwei']
    assert [score for _, score in result] == [1.0, 1.0]


def test_fake_reranker_empty_candidates():
    assert FakeReranker().rerank('query', []) == []


# --- HttpReranker ---------------------------------------------------------------

class _FakeResponse:
    """Just enough of an httpx.Response to exercise the reranker's own
    parsing -- status_code, .json(), and .text (mirrors
    tests/test_embeddings.py's _FakeResponse in Weave-Knowledge)."""

    def __init__(self, status_code: int, json_data: dict | None = None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self) -> dict:
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


def _rerank_payload(scores_by_index: dict[int, float]) -> dict:
    return {'results': [{'index': index, 'relevance_score': score} for index, score in scores_by_index.items()]}


def _reranker(**overrides) -> HttpReranker:
    defaults = dict(base_url='https://rerank.example.com', api_key='sk-test', model='test-rerank-model')
    defaults.update(overrides)
    return HttpReranker(**defaults)


def test_http_reranker_reorders_by_index_field():
    candidates = _candidates('a', 'b', 'c')
    payload = _rerank_payload({2: 0.9, 0: 0.1, 1: 0.5})

    with patch('app.services.reranker.httpx.post', return_value=_FakeResponse(200, payload)) as mock_post:
        result = _reranker().rerank('query', candidates)

    assert [c.text for c, _ in result] == ['c', 'b', 'a']
    assert [score for _, score in result] == [0.9, 0.5, 0.1]
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == 'https://rerank.example.com/rerank'
    assert mock_post.call_args.kwargs['headers']['Authorization'] == 'Bearer sk-test'
    sent_json = mock_post.call_args.kwargs['json']
    assert sent_json['model'] == 'test-rerank-model'
    assert sent_json['query'] == 'query'
    assert sent_json['documents'] == ['a', 'b', 'c']
    assert sent_json['top_n'] == 3


def test_http_reranker_empty_candidates_makes_no_request():
    with patch('app.services.reranker.httpx.post') as mock_post:
        result = _reranker().rerank('query', [])
    assert result == []
    mock_post.assert_not_called()


def test_http_reranker_retries_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr('app.services.reranker.time.sleep', lambda seconds: sleeps.append(seconds))

    candidates = _candidates('only')
    responses = [_FakeResponse(429, text='rate limited'), _FakeResponse(200, _rerank_payload({0: 0.7}))]

    with patch('app.services.reranker.httpx.post', side_effect=responses) as mock_post:
        result = _reranker().rerank('query', candidates)

    assert [score for _, score in result] == [0.7]
    assert mock_post.call_count == 2
    assert len(sleeps) == 1


def test_http_reranker_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr('app.services.reranker.time.sleep', lambda seconds: None)
    candidates = _candidates('only')
    responses = [_FakeResponse(503, text='unavailable'), _FakeResponse(200, _rerank_payload({0: 0.4}))]

    with patch('app.services.reranker.httpx.post', side_effect=responses) as mock_post:
        result = _reranker().rerank('query', candidates)

    assert [score for _, score in result] == [0.4]
    assert mock_post.call_count == 2


def test_http_reranker_retries_transport_error_then_succeeds(monkeypatch):
    monkeypatch.setattr('app.services.reranker.time.sleep', lambda seconds: None)
    candidates = _candidates('only')
    queue = [httpx.ConnectError('connection refused'), _FakeResponse(200, _rerank_payload({0: 0.2}))]

    def _side_effect(*args, **kwargs):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch('app.services.reranker.httpx.post', side_effect=_side_effect) as mock_post:
        result = _reranker().rerank('query', candidates)

    assert [score for _, score in result] == [0.2]
    assert mock_post.call_count == 2


def test_http_reranker_400_raises_without_retry():
    candidates = _candidates('only')
    with patch('app.services.reranker.httpx.post', return_value=_FakeResponse(400, text='bad request')) as mock_post:
        with pytest.raises(RerankError):
            _reranker().rerank('query', candidates)
    assert mock_post.call_count == 1


def test_http_reranker_401_raises_without_retry():
    candidates = _candidates('only')
    with patch('app.services.reranker.httpx.post', return_value=_FakeResponse(401, text='bad api key')) as mock_post:
        with pytest.raises(RerankError):
            _reranker().rerank('query', candidates)
    assert mock_post.call_count == 1


def test_http_reranker_exhausts_retries_and_raises(monkeypatch):
    monkeypatch.setattr('app.services.reranker.time.sleep', lambda seconds: None)
    candidates = _candidates('only')
    with patch(
        'app.services.reranker.httpx.post', return_value=_FakeResponse(429, text='still limited')
    ) as mock_post:
        with pytest.raises(RerankError):
            _reranker(max_attempts=3).rerank('query', candidates)
    assert mock_post.call_count == 3


def test_http_reranker_out_of_range_index_raises():
    candidates = _candidates('a', 'b')
    payload = {'results': [{'index': 0, 'relevance_score': 0.5}, {'index': 5, 'relevance_score': 0.1}]}
    with patch('app.services.reranker.httpx.post', return_value=_FakeResponse(200, payload)):
        with pytest.raises(RerankError):
            _reranker().rerank('query', candidates)


def test_http_reranker_duplicate_index_raises():
    candidates = _candidates('a', 'b')
    payload = {'results': [{'index': 0, 'relevance_score': 0.5}, {'index': 0, 'relevance_score': 0.1}]}
    with patch('app.services.reranker.httpx.post', return_value=_FakeResponse(200, payload)):
        with pytest.raises(RerankError):
            _reranker().rerank('query', candidates)


def test_http_reranker_partial_results_raises():
    candidates = _candidates('a', 'b', 'c')
    payload = _rerank_payload({0: 0.5, 1: 0.4})  # missing index 2
    with patch('app.services.reranker.httpx.post', return_value=_FakeResponse(200, payload)):
        with pytest.raises(RerankError):
            _reranker().rerank('query', candidates)


def test_http_reranker_non_json_body_raises():
    candidates = _candidates('only')
    with patch('app.services.reranker.httpx.post', return_value=_FakeResponse(200, None)):
        with pytest.raises(RerankError):
            _reranker().rerank('query', candidates)


# --- get_reranker() -------------------------------------------------------------

def test_get_reranker_none(monkeypatch):
    monkeypatch.setattr(settings, 'rerank_provider', 'none')
    assert isinstance(get_reranker(), NoopReranker)


def test_get_reranker_fake(monkeypatch):
    monkeypatch.setattr(settings, 'rerank_provider', 'fake')
    assert isinstance(get_reranker(), FakeReranker)


def test_get_reranker_api(monkeypatch):
    monkeypatch.setattr(settings, 'rerank_provider', 'api')
    assert isinstance(get_reranker(), HttpReranker)


def test_get_reranker_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(settings, 'rerank_provider', 'API')
    assert isinstance(get_reranker(), HttpReranker)


def test_get_reranker_unknown_raises(monkeypatch):
    monkeypatch.setattr(settings, 'rerank_provider', 'bogus')
    with pytest.raises(ValueError):
        get_reranker()
