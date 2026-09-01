"""Tests for app.services.embeddings: FakeEmbeddingProvider,
OpenAICompatibleProvider (with httpx.post mocked -- no real HTTP calls),
get_provider(), and embed_chunks().
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.models.models import Chunk, Document
from app.services import embeddings as embeddings_module
from app.services.embeddings import (
    EmbeddingProviderError,
    FakeEmbeddingProvider,
    OpenAICompatibleProvider,
    embed_chunks,
    get_provider,
)
from tests.conftest import TestingSessionLocal


# --- FakeEmbeddingProvider ----------------------------------------------------

def test_fake_provider_is_deterministic():
    provider = FakeEmbeddingProvider()
    first = provider.embed_batch(['hello world'])[0]
    second = provider.embed_batch(['hello world'])[0]
    assert first == second
    # A fresh instance must agree too -- determinism comes from the text's
    # own sha256, not from any per-instance state.
    assert FakeEmbeddingProvider().embed_batch(['hello world'])[0] == first


def test_fake_provider_differs_by_text():
    provider = FakeEmbeddingProvider()
    vector_a, vector_b = provider.embed_batch(['text one', 'text two'])
    assert vector_a != vector_b


def test_fake_provider_vectors_are_normalized():
    provider = FakeEmbeddingProvider()
    for vector in provider.embed_batch(['a', 'bb', 'ccc']):
        norm = sum(component * component for component in vector) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-9)


def test_fake_provider_dimension_defaults_from_settings():
    provider = FakeEmbeddingProvider()
    assert provider.dimension == settings.embedding_dimension
    assert len(provider.embed_batch(['x'])[0]) == settings.embedding_dimension


def test_fake_provider_dimension_override():
    provider = FakeEmbeddingProvider(dimension=8)
    assert provider.dimension == 8
    assert len(provider.embed_batch(['x'])[0]) == 8


def test_fake_provider_model_name_defaults_to_settings():
    assert FakeEmbeddingProvider().model_name == settings.embedding_model


def test_fake_provider_model_name_override():
    assert FakeEmbeddingProvider(model_name='custom-fake').model_name == 'custom-fake'


def test_fake_provider_empty_batch():
    assert FakeEmbeddingProvider().embed_batch([]) == []


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


def _embeddings_payload(vectors_by_index: dict[int, list[float]]) -> dict:
    return {'data': [{'index': index, 'embedding': vector} for index, vector in vectors_by_index.items()]}


def _provider(**overrides) -> OpenAICompatibleProvider:
    defaults = dict(base_url='https://embed.example.com', api_key='sk-test', model='test-model')
    defaults.update(overrides)
    return OpenAICompatibleProvider(**defaults)


def test_openai_provider_batches_at_batch_size():
    provider = _provider(batch_size=2)
    responses = [
        _FakeResponse(200, _embeddings_payload({0: [1.0, 0.0], 1: [0.0, 1.0]})),
        _FakeResponse(200, _embeddings_payload({0: [1.0, 1.0], 1: [2.0, 2.0]})),
        _FakeResponse(200, _embeddings_payload({0: [3.0, 3.0]})),
    ]

    with patch('app.services.embeddings.httpx.post', side_effect=responses) as mock_post:
        vectors = provider.embed_batch(['a', 'b', 'c', 'd', 'e'])

    assert vectors == [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 2.0], [3.0, 3.0]]
    assert mock_post.call_count == 3
    sent_inputs = [call.kwargs['json']['input'] for call in mock_post.call_args_list]
    assert sent_inputs == [['a', 'b'], ['c', 'd'], ['e']]
    assert mock_post.call_args_list[0].args[0] == 'https://embed.example.com/v1/embeddings'
    assert mock_post.call_args_list[0].kwargs['headers']['Authorization'] == 'Bearer sk-test'
    assert mock_post.call_args_list[0].kwargs['json']['model'] == 'test-model'


def test_openai_provider_reorders_by_index_field():
    """The response's `data` array arrives out of order -- the provider must
    place each embedding at its `index`, not at its position in the array.
    """
    provider = _provider(batch_size=10)
    payload = {
        'data': [
            {'index': 2, 'embedding': [3.0]},
            {'index': 0, 'embedding': [1.0]},
            {'index': 1, 'embedding': [2.0]},
        ]
    }
    with patch('app.services.embeddings.httpx.post', return_value=_FakeResponse(200, payload)):
        vectors = provider.embed_batch(['a', 'b', 'c'])
    assert vectors == [[1.0], [2.0], [3.0]]


def test_openai_provider_retries_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr('app.services.embeddings.time.sleep', lambda seconds: sleeps.append(seconds))

    provider = _provider()
    responses = [_FakeResponse(429, text='rate limited'), _FakeResponse(200, _embeddings_payload({0: [9.0]}))]

    with patch('app.services.embeddings.httpx.post', side_effect=responses) as mock_post:
        vectors = provider.embed_batch(['only'])

    assert vectors == [[9.0]]
    assert mock_post.call_count == 2
    assert len(sleeps) == 1


def test_openai_provider_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr('app.services.embeddings.time.sleep', lambda seconds: None)
    provider = _provider()
    responses = [_FakeResponse(503, text='unavailable'), _FakeResponse(200, _embeddings_payload({0: [1.0]}))]

    with patch('app.services.embeddings.httpx.post', side_effect=responses) as mock_post:
        vectors = provider.embed_batch(['only'])

    assert vectors == [[1.0]]
    assert mock_post.call_count == 2


def test_openai_provider_retries_transport_error_then_succeeds(monkeypatch):
    monkeypatch.setattr('app.services.embeddings.time.sleep', lambda seconds: None)
    provider = _provider()
    queue = [httpx.ConnectError('connection refused'), _FakeResponse(200, _embeddings_payload({0: [5.0]}))]

    def _side_effect(*args, **kwargs):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch('app.services.embeddings.httpx.post', side_effect=_side_effect) as mock_post:
        vectors = provider.embed_batch(['only'])

    assert vectors == [[5.0]]
    assert mock_post.call_count == 2


def test_openai_provider_400_raises_without_retry():
    provider = _provider()
    with patch('app.services.embeddings.httpx.post', return_value=_FakeResponse(400, text='bad request')) as mock_post:
        with pytest.raises(EmbeddingProviderError):
            provider.embed_batch(['only'])
    assert mock_post.call_count == 1


def test_openai_provider_401_raises_without_retry():
    provider = _provider()
    with patch('app.services.embeddings.httpx.post', return_value=_FakeResponse(401, text='bad api key')) as mock_post:
        with pytest.raises(EmbeddingProviderError):
            provider.embed_batch(['only'])
    assert mock_post.call_count == 1


def test_openai_provider_exhausts_retries_and_raises(monkeypatch):
    monkeypatch.setattr('app.services.embeddings.time.sleep', lambda seconds: None)
    provider = _provider(max_attempts=3)
    with patch('app.services.embeddings.httpx.post', return_value=_FakeResponse(429, text='still limited')) as mock_post:
        with pytest.raises(EmbeddingProviderError):
            provider.embed_batch(['only'])
    assert mock_post.call_count == 3


def test_openai_provider_dimension_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_dimension', 42)
    assert _provider().dimension == 42


def test_openai_provider_sends_passage_input_type():
    """Indexing always embeds chunk text, never a search query -- see the
    module docstring above _embed_one_batch for why this rides along as its
    own field (input_type) rather than a second model name."""
    provider = _provider()
    with patch(
        'app.services.embeddings.httpx.post', return_value=_FakeResponse(200, _embeddings_payload({0: [1.0]}))
    ) as mock_post:
        provider.embed_batch(['only'])
    assert mock_post.call_args.kwargs['json']['input_type'] == 'passage'


def test_openai_provider_model_name_identical_in_request_and_response_path():
    """The model name must never be renamed for the query/passage distinction
    -- input_type carries that instead (see _embed_one_batch's docstring).
    Same name goes out on the wire and comes back as provider.model_name,
    which is what embed_chunks() below persists onto the chunk/document."""
    provider = _provider(model='intfloat/multilingual-e5-small')
    with patch(
        'app.services.embeddings.httpx.post', return_value=_FakeResponse(200, _embeddings_payload({0: [1.0]}))
    ) as mock_post:
        provider.embed_batch(['only'])
    sent_model = mock_post.call_args.kwargs['json']['model']
    assert sent_model == 'intfloat/multilingual-e5-small'
    assert sent_model == provider.model_name


# --- get_provider() -------------------------------------------------------------

def test_get_provider_fake(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_provider', 'fake')
    assert isinstance(get_provider(), FakeEmbeddingProvider)


def test_get_provider_openai(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_provider', 'openai')
    assert isinstance(get_provider(), OpenAICompatibleProvider)


def test_get_provider_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_provider', 'OpenAI')
    assert isinstance(get_provider(), OpenAICompatibleProvider)


def test_get_provider_unknown_raises(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_provider', 'bogus')
    with pytest.raises(ValueError):
        get_provider()


# --- embed_chunks() -------------------------------------------------------------

def _make_document(db, **overrides) -> Document:
    defaults = dict(
        source_job_id=str(uuid.uuid4()),
        content_sha256='f' * 64,
        engine='paddleocr',
        frontmatter={},
        tags=[],
        processed_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    document = Document(**defaults)
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def test_embed_chunks_writes_vectors_and_model_name():
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
        chunk1 = Chunk(document_id=document.id, chunk_index=0, text='alpha', heading_path=[], char_count=5, meta={})
        chunk2 = Chunk(
            document_id=document.id, chunk_index=1, text='beta', heading_path=['Intro'], char_count=4, meta={}
        )
        db.add_all([chunk1, chunk2])
        db.commit()

        provider = FakeEmbeddingProvider()
        embed_chunks(db, document, [chunk1, chunk2], provider)
        db.commit()

        db.expire_all()
        refreshed_document = db.get(Document, document.id)
        assert refreshed_document.embedding_model == provider.model_name

        for chunk in (chunk1, chunk2):
            db.refresh(chunk)
            assert chunk.embedding is not None
            assert len(chunk.embedding) == provider.dimension
            assert chunk.embedding_model == provider.model_name
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


def test_embed_chunks_heading_path_changes_the_embedded_text():
    """Exercises the local _embedding_input fallback (app.services.enrichment
    doesn't exist in this checkout yet -- see embeddings.py's module
    docstring): two chunks with identical text but different heading_path
    must embed to different vectors, proving the heading breadcrumb is
    actually part of what gets embedded."""
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
        with_heading = Chunk(
            document_id=document.id, chunk_index=0, text='same text',
            heading_path=['Section'], char_count=9, meta={},
        )
        without_heading = Chunk(
            document_id=document.id, chunk_index=1, text='same text',
            heading_path=[], char_count=9, meta={},
        )
        db.add_all([with_heading, without_heading])
        db.commit()

        embed_chunks(db, document, [with_heading, without_heading], FakeEmbeddingProvider())
        db.commit()

        assert with_heading.embedding != without_heading.embedding
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


def test_embed_chunks_empty_list_is_a_noop():
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
        embed_chunks(db, document, [], FakeEmbeddingProvider())
        assert document.embedding_model is None
    finally:
        db.query(Document).delete()
        db.commit()
        db.close()


def test_embed_chunks_raises_on_provider_count_mismatch():
    class _BadProvider(FakeEmbeddingProvider):
        def embed_batch(self, texts):
            return super().embed_batch(texts)[:-1] if texts else []

    db = TestingSessionLocal()
    try:
        document = _make_document(db)
        chunk = Chunk(document_id=document.id, chunk_index=0, text='only', heading_path=[], char_count=4, meta={})
        db.add(chunk)
        db.commit()

        with pytest.raises(EmbeddingProviderError):
            embed_chunks(db, document, [chunk], _BadProvider())
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


def test_embed_chunks_model_name_matches_request_sent_over_http():
    """End-to-end through embed_chunks(): the model name stored on the chunk
    and document must be exactly what was sent in the HTTP request's
    `model` field -- input_type is the only thing that varies with
    query-vs-passage, never the model name itself (see
    OpenAICompatibleProvider._embed_one_batch's docstring)."""
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
        chunk = Chunk(document_id=document.id, chunk_index=0, text='only', heading_path=[], char_count=4, meta={})
        db.add(chunk)
        db.commit()

        provider = _provider(model='intfloat/multilingual-e5-small', batch_size=10)
        with patch(
            'app.services.embeddings.httpx.post',
            return_value=_FakeResponse(200, _embeddings_payload({0: [1.0]})),
        ) as mock_post:
            embed_chunks(db, document, [chunk], provider)
        db.commit()

        sent_model = mock_post.call_args.kwargs['json']['model']
        assert sent_model == 'intfloat/multilingual-e5-small'
        assert chunk.embedding_model == sent_model
        assert document.embedding_model == sent_model
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


def test_embedding_input_uses_heading_path_as_breadcrumb():
    """embed_chunks() calls _embedding_input(chunk, document.frontmatter) --
    this is app.services.enrichment.embedding_input in this checkout (that
    module now exists), which prefers a Confluence `confluence_path` over
    the chunk's own ATX heading_path, falling back to heading_path when
    there is none."""
    chunk = Chunk(document_id=uuid.uuid4(), chunk_index=0, text='body text', heading_path=['A', 'B'], char_count=9, meta={})
    assert embeddings_module._embedding_input(chunk, {}) == 'A > B\n\nbody text'

    chunk_no_heading = Chunk(
        document_id=uuid.uuid4(), chunk_index=0, text='body text', heading_path=[], char_count=9, meta={}
    )
    assert embeddings_module._embedding_input(chunk_no_heading, {}) == 'body text'

    chunk_with_confluence_path = Chunk(
        document_id=uuid.uuid4(), chunk_index=0, text='body text', heading_path=['Ignored'], char_count=9, meta={}
    )
    frontmatter = {'confluence_path': ['Space Root', 'Parent Page']}
    assert (
        embeddings_module._embedding_input(chunk_with_confluence_path, frontmatter)
        == 'Space Root > Parent Page\n\nbody text'
    )
