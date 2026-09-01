"""HTTP-level tests for POST /api/v1/search: auth gating (require_service_token,
app/core/auth.py -- unchanged behaviour now that the route does real work
instead of a 501 placeholder), request validation, and end-to-end round trips
proving app/services/search.py is actually wired in behind the endpoint, not
just importable.

The deeper hybrid-search pipeline behaviour (RRF fusion internals, per-leg
scoring, filter edge cases) is tests/test_search_service.py's job -- this
file only proves the HTTP shell (auth, validation, response envelope) around
it. The reranking tests near the end of this file are the one exception:
they specifically need a real HTTP round trip (not a direct search()
call) to prove settings.rerank_provider is read fresh per-request from the
same process-wide `settings` object every other test in this file mutates
via monkeypatch, and that a reranker outage degrades a live request to 200 +
RRF order rather than propagating as a 500.
"""

from unittest.mock import patch

import pytest

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus
from app.services.embeddings import FakeEmbeddingProvider
from tests.conftest import AUTH_HEADERS, TestingSessionLocal, client, make_chunk, make_document

_BODY = {'query': 'hello'}


def _cleanup() -> None:
    db = TestingSessionLocal()
    try:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
    finally:
        db.close()


# --- auth gating --------------------------------------------------------------


def test_search_without_token_is_401():
    resp = client.post('/api/v1/search', json=_BODY)
    assert resp.status_code == 401


def test_search_with_malformed_authorization_header_is_401():
    resp = client.post('/api/v1/search', json=_BODY, headers={'Authorization': 'not-bearer-at-all'})
    assert resp.status_code == 401


def test_search_with_wrong_token_is_401():
    resp = client.post('/api/v1/search', json=_BODY, headers={'Authorization': 'Bearer wrong-token'})
    assert resp.status_code == 401


def test_search_with_empty_configured_token_is_503(monkeypatch):
    # A misconfigured deployment (RETRIEVAL_API_TOKEN unset) must refuse
    # every request with 503, even one carrying what *was* a valid token a
    # moment ago -- never fall back to "auth disabled".
    monkeypatch.setattr(settings, 'retrieval_api_token', '')
    resp = client.post('/api/v1/search', json=_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 503


# --- request validation --------------------------------------------------------


def test_search_rejects_empty_query_with_valid_token():
    resp = client.post('/api/v1/search', json={'query': ''}, headers=AUTH_HEADERS)
    assert resp.status_code == 422


def test_search_rejects_final_k_greater_than_top_k():
    resp = client.post(
        '/api/v1/search', json={'query': 'hello', 'top_k': 3, 'final_k': 5}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 400


def test_search_accepts_final_k_equal_to_top_k():
    _cleanup()
    resp = client.post(
        '/api/v1/search', json={'query': 'hello', 'top_k': 5, 'final_k': 5}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['query'] == 'hello'
    assert body['results'] == []
    assert body['trace']['vector_candidates'] == 0
    assert body['trace']['fulltext_candidates'] == 0
    assert body['trace']['reranked'] == 0


def test_search_uses_configured_defaults_when_top_k_and_final_k_are_omitted():
    # settings.search_final_k (5) <= settings.search_top_k (20) by default --
    # this must pass the same final_k > top_k guard the explicit-value tests
    # above exercise, without either field ever being sent.
    _cleanup()
    resp = client.post('/api/v1/search', json={'query': 'hello'}, headers=AUTH_HEADERS)
    assert resp.status_code == 200


# --- end-to-end round trip ------------------------------------------------------


def test_search_end_to_end_returns_seeded_result_and_enforces_allowed_teams():
    db = TestingSessionLocal()
    try:
        provider = FakeEmbeddingProvider()
        text = 'Kundennummer 4711 steht oben rechts auf der Rechnung.'

        doc = make_document(team='Kundenservice', department='Support', status=DocumentStatus.INDEXED)
        db.add(doc)
        db.flush()
        chunk = make_chunk(
            doc, chunk_index=0, text=text, embedding=provider.embed_query(text),
            embedding_model=settings.embedding_model, meta={'team': doc.team, 'department': doc.department},
        )

        other_team_doc = make_document(team='OtherTeam', department='Other', status=DocumentStatus.INDEXED)
        db.add(other_team_doc)
        db.flush()
        other_chunk = make_chunk(
            other_team_doc, chunk_index=0, text=text, embedding=provider.embed_query(text),
            embedding_model=settings.embedding_model, meta={'team': other_team_doc.team},
        )

        db.add_all([chunk, other_chunk])
        db.commit()
        db.refresh(chunk)
        db.refresh(other_chunk)

        resp = client.post(
            '/api/v1/search',
            json={'query': text, 'allowed_teams': ['Kundenservice']},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()

        chunk_ids = [r['chunk_id'] for r in body['results']]
        assert chunk.id in chunk_ids
        # Byte-identical content from a different team -- the allowed_teams
        # boundary must hold end-to-end through the real HTTP endpoint, not
        # just when the service function is called directly.
        assert other_chunk.id not in chunk_ids

        hit = next(r for r in body['results'] if r['chunk_id'] == chunk.id)
        assert hit['scores']['vector'] == pytest.approx(1.0, abs=1e-6)
        assert hit['scores']['rerank'] is None
        assert hit['team'] == 'Kundenservice'
        # settings.rerank_provider defaults to 'none' (NoopReranker): the one
        # in-scope candidate still passes through the reranking stage
        # (reranked == the RRF top_k pool size, see SearchTrace's own
        # docstring) with score=None, and no error occurred.
        assert body['trace']['reranked'] == 1
        assert body['trace']['rerank_error'] is False
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


# --- reranking: settings.rerank_provider == 'fake' end-to-end -------------------


def test_search_with_fake_reranker_reorders_and_trims_to_final_k(monkeypatch):
    """Three chunks, deliberately constructed so RRF fusion and FakeReranker
    (settings.rerank_provider == 'fake', app/services/reranker.py) disagree
    on their order:

    - `chunk_vector`: its embedding is set to the query's OWN embedding
      (cosine similarity 1.0, guaranteed vector-leg rank 1), but its text
      shares only 2 of the query's 3 words -- a middling fulltext score.
    - `chunk_fulltext`: its embedding is the query embedding's exact
      NEGATION (cosine similarity -1.0, the worst possible -- guaranteed
      last place in the vector leg), but its text shares all 3 of the
      query's words -- the best possible fulltext score.
    - `chunk_noise`: an unrelated embedding (whatever FakeEmbeddingProvider
      produces for unrelated text -- provably neither exactly 1.0 nor
      exactly -1.0, so it always lands strictly between the other two in
      the vector leg) and zero textual overlap with the query at all, so
      fulltext_search() excludes it outright.

    Both `chunk_vector` and `chunk_fulltext` are therefore present in BOTH
    legs (`chunk_noise` only in the vector leg), which makes RRF fusion rank
    `chunk_vector` first (vector rank 1 + fulltext rank 2 beats vector rank
    3 + fulltext rank 1 -- see rrf_fuse()'s own module -- because being
    ahead in one leg by two ranks outweighs being ahead in the other by one).
    FakeReranker, in contrast, only ever looks at each candidate's raw
    query/text token overlap -- 3 shared words beats 2 -- and ranks
    `chunk_fulltext` first. This is what proves the API's final response
    order actually reflects the reranker's own scores, not just the RRF
    order search() computed before reranking ran.

    `final_k=2` additionally drops `chunk_noise` (never fulltext-matched,
    so it has no way to compete with either of the other two once a real
    reranker -- as opposed to the RRF-order-preserving Noop default --
    is scoring every candidate) from the final response, proving
    `final_k` trims by REranked order, not by the pre-rerank RRF order.
    """
    monkeypatch.setattr(settings, 'rerank_provider', 'fake')

    db = TestingSessionLocal()
    try:
        query = 'produkt information sofort'
        provider = FakeEmbeddingProvider()
        query_vec = provider.embed_query(query)

        doc = make_document(status=DocumentStatus.INDEXED)
        db.add(doc)
        db.flush()

        chunk_vector = make_chunk(
            doc, chunk_index=0, text='Das Produkt Information ist bereits erhältlich.',
            embedding=query_vec, embedding_model=settings.embedding_model, meta={'team': doc.team},
        )
        chunk_fulltext = make_chunk(
            doc, chunk_index=1, text='Produkt Information sofort lieferbar.',
            embedding=[-x for x in query_vec], embedding_model=settings.embedding_model, meta={'team': doc.team},
        )
        chunk_noise = make_chunk(
            doc, chunk_index=2, text='Der Kaffee in der Küche ist heute leider alle.',
            embedding=provider.embed_query('ein völlig anderes Thema ohne jeden Bezug'),
            embedding_model=settings.embedding_model, meta={'team': doc.team},
        )
        db.add_all([chunk_vector, chunk_fulltext, chunk_noise])
        db.commit()
        for chunk in (chunk_vector, chunk_fulltext, chunk_noise):
            db.refresh(chunk)

        resp = client.post(
            '/api/v1/search',
            json={'query': query, 'top_k': 3, 'final_k': 2},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()

        # FakeReranker order (by raw query/text token overlap: 3 > 2),
        # trimmed to final_k=2 -- chunk_noise (0 overlap) never makes it in,
        # even though RRF fusion would have ranked chunk_vector first.
        assert [r['chunk_id'] for r in body['results']] == [chunk_fulltext.id, chunk_vector.id]
        assert [r['scores']['rerank'] for r in body['results']] == [3.0, 2.0]
        assert body['trace']['reranked'] == 3
        assert body['trace']['rerank_error'] is False
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


# --- reranking: a provider failure falls back to RRF order, never a 5xx ---------


def test_search_falls_back_to_rrf_order_when_reranker_fails(monkeypatch):
    """settings.rerank_provider == 'api' (HttpReranker), with httpx.post
    mocked to return a persistent 500 -- HttpReranker exhausts its retries
    and raises RerankError (see tests/test_reranker.py for that behaviour in
    isolation). The search request must still succeed (200), returning the
    untouched RRF order with every `scores.rerank` at `None` -- exactly the
    same shape a NoopReranker would have produced -- plus
    `trace.rerank_error = True` so a caller/operator can tell the difference
    from "no reranker configured" (see SearchTrace's own docstring). A
    reranker outage must never fail the whole search request.
    """
    monkeypatch.setattr(settings, 'rerank_provider', 'api')
    monkeypatch.setattr(settings, 'rerank_base_url', 'https://rerank.example.com')
    monkeypatch.setattr(settings, 'rerank_api_key', 'sk-test')
    monkeypatch.setattr('app.services.reranker.time.sleep', lambda seconds: None)

    class _AlwaysFailsResponse:
        status_code = 500
        text = 'internal error'

    db = TestingSessionLocal()
    try:
        provider = FakeEmbeddingProvider()
        text = 'Der Drucker im dritten Stock hat keine Tinte mehr.'

        doc = make_document(status=DocumentStatus.INDEXED)
        db.add(doc)
        db.flush()
        chunk = make_chunk(
            doc, chunk_index=0, text=text, embedding=provider.embed_query(text),
            embedding_model=settings.embedding_model, meta={'team': doc.team},
        )
        db.add(chunk)
        db.commit()
        db.refresh(chunk)

        with patch('app.services.reranker.httpx.post', return_value=_AlwaysFailsResponse()):
            resp = client.post('/api/v1/search', json={'query': text}, headers=AUTH_HEADERS)

        assert resp.status_code == 200
        body = resp.json()

        chunk_ids = [r['chunk_id'] for r in body['results']]
        assert chunk.id in chunk_ids
        assert all(r['scores']['rerank'] is None for r in body['results'])
        assert body['trace']['rerank_error'] is True
        assert body['trace']['reranked'] == len(body['results'])
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


def test_non_positive_top_k_and_final_k_are_rejected():
    # Regression: negative limits used to slip past the final_k > top_k
    # guard and turn into silently wrong slices on the SQLite fallback.
    for body in (
        {'query': 'x', 'top_k': -1, 'final_k': -1},
        {'query': 'x', 'top_k': 0},
        {'query': 'x', 'final_k': 0},
    ):
        resp = client.post('/api/v1/search', json=body, headers=AUTH_HEADERS)
        assert resp.status_code == 422, body
