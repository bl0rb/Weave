"""Tests for app.services.embeddings: FakeEmbeddingProvider,
OpenAICompatibleProvider (with httpx.post mocked -- no real HTTP calls), and
get_provider(). Mirrors Weave-Knowledge's own backend/tests/test_embeddings.py
(the sibling module this one was copied from), scaled down to the one-item
`embed_query()` shape this service actually uses -- see
app/services/embeddings.py's module docstring.
"""

from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.services import embeddings as embeddings_module
from app.services.embeddings import (
    EmbeddingProviderError,
    FakeEmbeddingProvider,
    OpenAICompatibleProvider,
    embed_query,
    get_provider,
)


# --- FakeEmbeddingProvider ----------------------------------------------------

def test_fake_provider_is_deterministic():
    provider = FakeEmbeddingProvider()
    assert provider.embed_query('hello world') == provider.embed_query('hello world')
    assert FakeEmbeddingProvider().embed_query('hello world') == provider.embed_query('hello world')


def test_fake_provider_vectors_are_normalized():
    provider = FakeEmbeddingProvider()
    vector = provider.embed_query('some query')
    norm = sum(component * component for component in vector) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_fake_provider_dimension_and_model_name_default_from_settings():
    provider = FakeEmbeddingProvider()
    assert provider.dimension == settings.embedding_dimension
    assert provider.model_name == settings.embedding_model
    assert len(provider.embed_query('x')) == settings.embedding_dimension


# --- OpenAICompatibleProvider --------------------------------------------------

class _FakeResponse:
    """Just enough of an httpx.Response to exercise the provider's own
    parsing -- status_code, .json(), and .text."""

    def __init__(self, status_code: int, json_data: dict | None = None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self) -> dict:
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


def _embedding_payload(vector: list[float]) -> dict:
    return {'data': [{'object': 'embedding', 'index': 0, 'embedding': vector}]}


def _provider(**overrides) -> OpenAICompatibleProvider:
    defaults = dict(base_url='https://embed.example.com', api_key='sk-test', model='test-model')
    defaults.update(overrides)
    return OpenAICompatibleProvider(**defaults)


def test_openai_provider_sends_single_item_input_and_model():
    provider = _provider()
    with patch(
        'app.services.embeddings.httpx.post', return_value=_FakeResponse(200, _embedding_payload([1.0, 2.0]))
    ) as mock_post:
        vector = provider.embed_query('what is the return policy?')

    assert vector == [1.0, 2.0]
    assert mock_post.call_args.args[0] == 'https://embed.example.com/v1/embeddings'
    assert mock_post.call_args.kwargs['headers']['Authorization'] == 'Bearer sk-test'
    assert mock_post.call_args.kwargs['json']['model'] == 'test-model'
    assert mock_post.call_args.kwargs['json']['input'] == ['what is the return policy?']


def test_openai_provider_sends_query_input_type():
    """This provider only ever embeds the incoming search string, never
    chunk text meant for the index -- see embed_query's own docstring for
    why that always means input_type="query", and why input_type (not a
    second model name) is what carries the distinction."""
    provider = _provider()
    with patch(
        'app.services.embeddings.httpx.post', return_value=_FakeResponse(200, _embedding_payload([1.0]))
    ) as mock_post:
        provider.embed_query('only')
    assert mock_post.call_args.kwargs['json']['input_type'] == 'query'


def test_openai_provider_model_name_identical_in_request_and_property():
    """The model name must never be renamed for the query/passage
    distinction -- input_type carries that instead. Same name goes out on
    the wire as comes back from provider.model_name, which is what
    app/services/search.py's vector_search() then filters
    Chunk.embedding_model against (see this module's own docstring)."""
    provider = _provider(model='intfloat/multilingual-e5-small')
    with patch(
        'app.services.embeddings.httpx.post', return_value=_FakeResponse(200, _embedding_payload([1.0]))
    ) as mock_post:
        provider.embed_query('only')
    sent_model = mock_post.call_args.kwargs['json']['model']
    assert sent_model == 'intfloat/multilingual-e5-small'
    assert sent_model == provider.model_name


def test_openai_provider_retries_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr('app.services.embeddings.time.sleep', lambda seconds: sleeps.append(seconds))

    provider = _provider()
    responses = [_FakeResponse(429, text='rate limited'), _FakeResponse(200, _embedding_payload([9.0]))]

    with patch('app.services.embeddings.httpx.post', side_effect=responses) as mock_post:
        vector = provider.embed_query('only')

    assert vector == [9.0]
    assert mock_post.call_count == 2
    assert len(sleeps) == 1


def test_openai_provider_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr('app.services.embeddings.time.sleep', lambda seconds: None)
    provider = _provider()
    responses = [_FakeResponse(503, text='unavailable'), _FakeResponse(200, _embedding_payload([1.0]))]

    with patch('app.services.embeddings.httpx.post', side_effect=responses) as mock_post:
        vector = provider.embed_query('only')

    assert vector == [1.0]
    assert mock_post.call_count == 2


def test_openai_provider_retries_transport_error_then_succeeds(monkeypatch):
    monkeypatch.setattr('app.services.embeddings.time.sleep', lambda seconds: None)
    provider = _provider()
    queue = [httpx.ConnectError('connection refused'), _FakeResponse(200, _embedding_payload([5.0]))]

    def _side_effect(*args, **kwargs):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch('app.services.embeddings.httpx.post', side_effect=_side_effect) as mock_post:
        vector = provider.embed_query('only')

    assert vector == [5.0]
    assert mock_post.call_count == 2


def test_openai_provider_400_raises_without_retry():
    provider = _provider()
    with patch('app.services.embeddings.httpx.post', return_value=_FakeResponse(400, text='bad request')) as mock_post:
        with pytest.raises(EmbeddingProviderError):
            provider.embed_query('only')
    assert mock_post.call_count == 1


def test_openai_provider_exhausts_retries_and_raises(monkeypatch):
    monkeypatch.setattr('app.services.embeddings.time.sleep', lambda seconds: None)
    provider = _provider(max_attempts=3)
    with patch('app.services.embeddings.httpx.post', return_value=_FakeResponse(429, text='still limited')) as mock_post:
        with pytest.raises(EmbeddingProviderError):
            provider.embed_query('only')
    assert mock_post.call_count == 3


def test_openai_provider_dimension_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_dimension', 42)
    assert _provider().dimension == 42


# --- get_provider() -------------------------------------------------------------

def test_get_provider_fake(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_provider', 'fake')
    assert isinstance(get_provider(), FakeEmbeddingProvider)


def test_get_provider_openai(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_provider', 'openai')
    assert isinstance(get_provider(), OpenAICompatibleProvider)


def test_get_provider_unknown_raises(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_provider', 'bogus')
    with pytest.raises(ValueError):
        get_provider()


# --- embed_query() cache ----------------------------------------------------------

class _CountingProvider:
    def __init__(self, fail_first: bool = False):
        self.calls = []
        self._fail_first = fail_first

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._fail_first and len(self.calls) == 1:
            raise EmbeddingProviderError('down')
        return [float(len(self.calls)), 0.5]


@pytest.fixture
def counting(monkeypatch):
    provider = _CountingProvider()
    monkeypatch.setattr(embeddings_module, 'get_provider', lambda: provider)
    return provider


def test_embed_query_cache_hit_returns_a_copy(counting):
    first = embed_query('Urlaub beantragen')
    first.append(99.0)  # a caller mutating its vector must not touch the cache
    second = embed_query('Urlaub beantragen')
    assert second == [1.0, 0.5]
    assert counting.calls == ['Urlaub beantragen']
    embed_query('Urlaub  beantragen')  # different text, different vector: a miss
    assert len(counting.calls) == 2


def test_embed_query_cache_expires_after_ttl(counting, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(embeddings_module, '_now', lambda: clock[0])
    monkeypatch.setattr(settings, 'embedding_query_cache_ttl_seconds', 600)
    embed_query('q')
    clock[0] += 599
    embed_query('q')
    assert len(counting.calls) == 1
    clock[0] += 2
    assert embed_query('q') == [2.0, 0.5]
    assert len(counting.calls) == 2


@pytest.mark.parametrize('name, value', [
    ('embedding_model', 'other-model'),
    ('embedding_provider', 'openai'),
    ('embedding_base_url', 'http://other'),
    ('embedding_dimension', 3),
])
def test_embed_query_cache_misses_after_a_provider_change(counting, monkeypatch, name, value):
    embed_query('q')
    monkeypatch.setattr(settings, name, value)
    assert embed_query('q') == [2.0, 0.5]
    assert len(counting.calls) == 2


def test_embed_query_cache_size_zero_disables_and_lru_evicts(counting, monkeypatch):
    monkeypatch.setattr(settings, 'embedding_query_cache_size', 0)
    embed_query('a')
    embed_query('a')
    assert len(counting.calls) == 2

    monkeypatch.setattr(settings, 'embedding_query_cache_size', 2)
    embed_query('a')  # 3
    embed_query('b')  # 4
    embed_query('a')  # hit, 'a' becomes most recent
    embed_query('c')  # 5, evicts 'b'
    embed_query('a')  # hit
    embed_query('b')  # 6
    assert counting.calls == ['a', 'a', 'a', 'b', 'c', 'b']


def test_embed_query_failures_are_not_cached(monkeypatch):
    provider = _CountingProvider(fail_first=True)
    monkeypatch.setattr(embeddings_module, 'get_provider', lambda: provider)
    with pytest.raises(EmbeddingProviderError):
        embed_query('q')
    assert embed_query('q') == [2.0, 0.5]
    assert embed_query('q') == [2.0, 0.5]
    assert len(provider.calls) == 2
