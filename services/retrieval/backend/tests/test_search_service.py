"""Tests for app.services.search: apply_filters/meta_matches, vector_search,
fulltext_search, rrf_fuse, and the top-level search() pipeline.

Runs entirely against the pytest suite's own sqlite test.db (see
tests/conftest.py) -- every test seeds its own small corpus of
Document/Chunk rows via make_document()/make_chunk() and cleans up again via
the `db`/`corpus` fixtures below, exactly like tests/test_models.py's own
try/finally pattern, so tests never see each other's rows.

Chunk embeddings are computed with the REAL FakeEmbeddingProvider (the same
class app/services/embeddings.py's get_provider() returns for the
process-wide default settings.embedding_provider == 'fake'), never a
hand-rolled stand-in vector -- that is what makes "query the exact same text
back" a meaningful assertion (cosine similarity ~1.0) rather than a tautology
against some other, unrelated fake vector.
"""

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus
from app.schemas.search import SearchFilters, SearchRequest
from app.services import search as search_service
from app.services.embeddings import FakeEmbeddingProvider
from tests.conftest import TestingSessionLocal, make_chunk, make_document

_PROVIDER = FakeEmbeddingProvider()


def _embed(text: str) -> list[float]:
    return _PROVIDER.embed_query(text)


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.query(Chunk).delete()
        session.query(Document).delete()
        session.commit()
        session.close()


def _seed_doc(db, **overrides) -> Document:
    doc = make_document(**overrides)
    db.add(doc)
    db.flush()
    return doc


def _seed_chunk(db, doc: Document, text: str, *, chunk_index: int = 0, embedding_model: str | None = None, meta: dict | None = None) -> Chunk:
    chunk = make_chunk(
        doc,
        chunk_index=chunk_index,
        text=text,
        embedding=_embed(text),
        embedding_model=embedding_model if embedding_model is not None else settings.embedding_model,
        meta=meta if meta is not None else {'team': doc.team, 'department': doc.department},
    )
    db.add(chunk)
    return chunk


# Shared across the corpus fixture and several assertions below: the "hit"
# text is the one every in-scope, correctly-embedded chunk is seeded with
# (a chunk found via BOTH the vector and fulltext leg for the same query),
# "partial" only overlaps on one of its two tokens, "unrelated" overlaps on
# neither.
_HIT_TEXT = 'Bitte geben Sie Ihre Kundennummer 4711 an, wenn Sie anrufen.'
_PARTIAL_TEXT = 'Die Kundennummer wird oben im Formular angezeigt.'
_UNRELATED_TEXT = 'Der Kaffee in der Kueche ist heute leider alle.'


@pytest.fixture
def corpus(db):
    """A deliberately adversarial corpus covering every filter/status/model
    edge case the search pipeline must handle -- see the inline comments on
    each seeded document for which invariant it exercises.
    """
    doc_support = _seed_doc(db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED)
    hit_chunk = _seed_chunk(db, doc_support, _HIT_TEXT, chunk_index=0)
    unrelated_chunk = _seed_chunk(db, doc_support, _UNRELATED_TEXT, chunk_index=1)

    doc_support_2 = _seed_doc(db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED)
    partial_chunk = _seed_chunk(db, doc_support_2, _PARTIAL_TEXT, chunk_index=0)

    # Different team -- in scope for status/embedding purposes, but must
    # never surface once allowed_teams=['Kundenservice'] is enforced, even
    # though its content is a byte-identical match.
    doc_other_team = _seed_doc(db, team='Engineering', department='Platform', status=DocumentStatus.INDEXED)
    other_team_chunk = _seed_chunk(db, doc_other_team, _HIT_TEXT, chunk_index=0)

    # Same team, indexed, identical content -- but embedded under a model
    # that doesn't match settings.embedding_model. Only the vector leg must
    # skip it; fulltext has no notion of embedding_model at all.
    doc_wrong_model = _seed_doc(db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED)
    wrong_model_chunk = _seed_chunk(db, doc_wrong_model, _HIT_TEXT, chunk_index=0, embedding_model='other-model')

    # Same team, same identical content, one row per non-indexed status --
    # none of these may ever appear in a result, no matter how good a match.
    doc_blocked = _seed_doc(db, team='Kundenservice', department='Support', status=DocumentStatus.BLOCKED)
    blocked_chunk = _seed_chunk(db, doc_blocked, _HIT_TEXT, chunk_index=0)

    doc_pending = _seed_doc(db, team='Kundenservice', department='Support', status=DocumentStatus.PENDING)
    pending_chunk = _seed_chunk(db, doc_pending, _HIT_TEXT, chunk_index=0)

    doc_superseded = _seed_doc(db, team='Kundenservice', department='Support', status=DocumentStatus.SUPERSEDED)
    superseded_chunk = _seed_chunk(db, doc_superseded, _HIT_TEXT, chunk_index=0)

    db.commit()
    for chunk in (
        hit_chunk, unrelated_chunk, partial_chunk, other_team_chunk, wrong_model_chunk,
        blocked_chunk, pending_chunk, superseded_chunk,
    ):
        db.refresh(chunk)

    return SimpleNamespace(
        db=db,
        hit_chunk=hit_chunk,
        unrelated_chunk=unrelated_chunk,
        partial_chunk=partial_chunk,
        other_team_chunk=other_team_chunk,
        wrong_model_chunk=wrong_model_chunk,
        blocked_chunk=blocked_chunk,
        pending_chunk=pending_chunk,
        superseded_chunk=superseded_chunk,
    )


def _ids(pairs) -> list[int]:
    return [chunk.id for chunk, _score in pairs]


# --- vector_search --------------------------------------------------------------


def test_vector_search_scores_identical_text_near_one(corpus):
    query_vec = _embed(_HIT_TEXT)
    results = search_service.vector_search(corpus.db, query_vec, None, None, None, limit=10)

    result_ids = _ids(results)
    assert corpus.hit_chunk.id in result_ids
    score_by_id = dict(results)
    hit_score = next(score for chunk, score in results if chunk.id == corpus.hit_chunk.id)
    assert hit_score == pytest.approx(1.0, abs=1e-9)


def test_vector_search_skips_non_indexed_statuses(corpus):
    query_vec = _embed(_HIT_TEXT)
    results = search_service.vector_search(corpus.db, query_vec, None, None, None, limit=50)
    result_ids = set(_ids(results))

    assert corpus.blocked_chunk.id not in result_ids
    assert corpus.pending_chunk.id not in result_ids
    assert corpus.superseded_chunk.id not in result_ids


def test_vector_search_skips_embedding_model_mismatch(corpus):
    query_vec = _embed(_HIT_TEXT)
    results = search_service.vector_search(corpus.db, query_vec, None, None, None, limit=50)
    assert corpus.wrong_model_chunk.id not in set(_ids(results))


def test_vector_search_allowed_teams_is_a_hard_boundary(corpus):
    query_vec = _embed(_HIT_TEXT)
    results = search_service.vector_search(corpus.db, query_vec, None, ['Kundenservice'], None, limit=50)
    result_ids = set(_ids(results))

    assert corpus.hit_chunk.id in result_ids
    # Byte-identical content and vector -- excluded ONLY because of the team
    # boundary, never because of any relevance judgment.
    assert corpus.other_team_chunk.id not in result_ids


def test_vector_search_team_filter_excludes_other_teams(corpus):
    query_vec = _embed(_HIT_TEXT)
    results = search_service.vector_search(
        corpus.db, query_vec, SearchFilters(team='Engineering'), None, None, limit=50
    )
    result_ids = set(_ids(results))
    assert corpus.other_team_chunk.id in result_ids
    assert corpus.hit_chunk.id not in result_ids


# --- fulltext_search --------------------------------------------------------------


def test_fulltext_search_finds_exact_terms(corpus):
    results = search_service.fulltext_search(corpus.db, 'Kundennummer 4711', None, None, None, limit=50)
    score_by_id = {chunk.id: score for chunk, score in results}

    assert corpus.hit_chunk.id in score_by_id
    assert corpus.partial_chunk.id in score_by_id
    assert score_by_id[corpus.hit_chunk.id] == pytest.approx(1.0)
    assert score_by_id[corpus.partial_chunk.id] == pytest.approx(0.5)
    assert score_by_id[corpus.hit_chunk.id] > score_by_id[corpus.partial_chunk.id]
    assert corpus.unrelated_chunk.id not in score_by_id


def test_fulltext_search_ignores_embedding_model(corpus):
    """Unlike vector_search, embedding_model is not even a concept fulltext
    matching knows about -- a chunk embedded under the "wrong" model still
    surfaces here as long as its team/status are in scope."""
    results = search_service.fulltext_search(corpus.db, 'Kundennummer 4711', None, None, None, limit=50)
    assert corpus.wrong_model_chunk.id in set(_ids(results))


def test_fulltext_search_skips_non_indexed_statuses(corpus):
    results = search_service.fulltext_search(corpus.db, 'Kundennummer 4711', None, None, None, limit=50)
    result_ids = set(_ids(results))
    assert corpus.blocked_chunk.id not in result_ids
    assert corpus.pending_chunk.id not in result_ids
    assert corpus.superseded_chunk.id not in result_ids


def test_fulltext_search_allowed_teams_is_a_hard_boundary(corpus):
    results = search_service.fulltext_search(
        corpus.db, 'Kundennummer 4711', None, ['Kundenservice'], None, limit=50
    )
    assert corpus.other_team_chunk.id not in set(_ids(results))


# --- metadata filters against chunks.meta (source/tags) ---------------------------


def test_meta_filters_source_and_tags(db):
    doc = _seed_doc(db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED)
    text = 'Ein Dokument ueber Rechnungen und Mahnungen.'
    matching = _seed_chunk(
        db, doc, text, chunk_index=0, meta={'team': doc.team, 'source': 'invoice.pdf', 'tags': ['finance', 'urgent']}
    )
    other_source = _seed_chunk(
        db, doc, text, chunk_index=1, meta={'team': doc.team, 'source': 'other.pdf', 'tags': ['hr']}
    )
    db.commit()

    query_vec = _embed(text)

    by_source = search_service.vector_search(
        db, query_vec, SearchFilters(source='invoice.pdf'), None, None, limit=10
    )
    source_ids = set(_ids(by_source))
    assert matching.id in source_ids
    assert other_source.id not in source_ids

    by_tag = search_service.vector_search(db, query_vec, SearchFilters(tags=['urgent']), None, None, limit=10)
    tag_ids = set(_ids(by_tag))
    assert matching.id in tag_ids
    assert other_source.id not in tag_ids


# --- allowed_collections (Collections contract point 5) ---------------------------


def _seed_collection_corpus(db):
    """A small, dedicated corpus for the `allowed_collections` boundary and
    `filters.collection` refinement: one document per collection slug, plus
    one legacy (unscoped) document with `collection_slug=None`. All in the
    same team/status so only collection scoping ever explains a difference
    in what a query returns.
    """
    text = 'Bitte pruefen Sie den Status Ihrer Bestellung im Kundenportal.'

    doc_support = _seed_doc(
        db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED, collection_slug='support-docs'
    )
    support_chunk = _seed_chunk(db, doc_support, text, chunk_index=0)

    doc_eng = _seed_doc(
        db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED, collection_slug='eng-docs'
    )
    eng_chunk = _seed_chunk(db, doc_eng, text, chunk_index=0)

    doc_legacy = _seed_doc(
        db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED, collection_slug=None
    )
    legacy_chunk = _seed_chunk(db, doc_legacy, text, chunk_index=0)

    db.commit()
    for chunk in (support_chunk, eng_chunk, legacy_chunk):
        db.refresh(chunk)

    return SimpleNamespace(
        db=db, text=text, support_chunk=support_chunk, eng_chunk=eng_chunk, legacy_chunk=legacy_chunk
    )


def test_vector_search_allowed_collections_is_a_hard_boundary(db):
    seeded = _seed_collection_corpus(db)
    query_vec = _embed(seeded.text)

    results = search_service.vector_search(db, query_vec, None, None, ['support-docs'], limit=50)
    result_ids = set(_ids(results))

    # Byte-identical content and vector, in-scope team/status -- excluded
    # ONLY because 'eng-docs' isn't in allowed_collections, never because of
    # relevance.
    assert seeded.support_chunk.id in result_ids
    assert seeded.eng_chunk.id not in result_ids
    # No sentinel -> unscoped legacy content stays invisible too (see the
    # dedicated legacy-document tests below for the sentinel's own effect).
    assert seeded.legacy_chunk.id not in result_ids


def test_fulltext_search_allowed_collections_is_a_hard_boundary(db):
    seeded = _seed_collection_corpus(db)

    results = search_service.fulltext_search(db, seeded.text, None, None, ['support-docs'], limit=50)
    result_ids = set(_ids(results))

    assert seeded.support_chunk.id in result_ids
    assert seeded.eng_chunk.id not in result_ids
    assert seeded.legacy_chunk.id not in result_ids


def test_allowed_collections_excludes_a_perfectly_matching_foreign_collection(db):
    """Regression for the exact wording of the contract: a document whose
    collection matches every OTHER filter perfectly must still be excluded
    purely because its collection isn't in allowed_collections."""
    seeded = _seed_collection_corpus(db)
    query_vec = _embed(seeded.text)

    vector_ids = set(_ids(search_service.vector_search(db, query_vec, None, None, ['eng-docs'], limit=50)))
    assert seeded.eng_chunk.id in vector_ids
    assert seeded.support_chunk.id not in vector_ids

    fulltext_ids = set(_ids(search_service.fulltext_search(db, seeded.text, None, None, ['eng-docs'], limit=50)))
    assert seeded.eng_chunk.id in fulltext_ids
    assert seeded.support_chunk.id not in fulltext_ids


def test_allowed_collections_empty_list_yields_zero_results(db):
    """`allowed_collections=[]` is "no collection authorized" -- same
    semantics as `allowed_teams=[]` -- not "no restriction"."""
    seeded = _seed_collection_corpus(db)
    query_vec = _embed(seeded.text)

    assert search_service.vector_search(db, query_vec, None, None, [], limit=50) == []
    assert search_service.fulltext_search(db, seeded.text, None, None, [], limit=50) == []


def test_allowed_collections_none_leaves_legacy_documents_visible(db):
    """`allowed_collections=None` is "no collection restriction at all" --
    every in-scope chunk surfaces, scoped or not."""
    seeded = _seed_collection_corpus(db)
    query_vec = _embed(seeded.text)

    result_ids = set(_ids(search_service.vector_search(db, query_vec, None, None, None, limit=50)))
    assert seeded.support_chunk.id in result_ids
    assert seeded.eng_chunk.id in result_ids
    assert seeded.legacy_chunk.id in result_ids


def test_allowed_collections_sentinel_reveals_legacy_documents_only(db):
    """The `NO_COLLECTION_SENTINEL` ("__none__") specifically re-admits
    `collection_slug IS NULL` documents ON TOP OF whichever real slugs are
    listed alongside it -- Collections contract point 5's Altbestand rule,
    verified against both legs and both directions (present vs. absent)."""
    seeded = _seed_collection_corpus(db)
    query_vec = _embed(seeded.text)

    with_sentinel = set(
        _ids(
            search_service.vector_search(
                db, query_vec, None, None, ['support-docs', search_service.NO_COLLECTION_SENTINEL], limit=50
            )
        )
    )
    assert seeded.support_chunk.id in with_sentinel
    assert seeded.legacy_chunk.id in with_sentinel
    assert seeded.eng_chunk.id not in with_sentinel

    without_sentinel = set(
        _ids(search_service.vector_search(db, query_vec, None, None, ['support-docs'], limit=50))
    )
    assert seeded.legacy_chunk.id not in without_sentinel

    # The sentinel alone (no real slug at all) admits ONLY legacy documents.
    sentinel_only = set(
        _ids(
            search_service.fulltext_search(
                db, seeded.text, None, None, [search_service.NO_COLLECTION_SENTINEL], limit=50
            )
        )
    )
    assert sentinel_only == {seeded.legacy_chunk.id}


def test_filters_collection_refines_within_allowed_collections(db):
    """`filters.collection` narrows further within the boundary -- it can
    select any ONE of the allowed slugs, never one outside it."""
    seeded = _seed_collection_corpus(db)
    query_vec = _embed(seeded.text)
    allowed = ['support-docs', 'eng-docs']

    narrowed = search_service.vector_search(
        db, query_vec, SearchFilters(collection='support-docs'), None, allowed, limit=50
    )
    assert set(_ids(narrowed)) == {seeded.support_chunk.id}


def test_filters_collection_outside_allowed_collections_yields_empty_not_error(db):
    """Asking for a `filters.collection` the caller wasn't granted must
    intersect to an empty result -- it must never raise, and it must never
    fall back to "no collection filter" and leak the boundary."""
    seeded = _seed_collection_corpus(db)
    query_vec = _embed(seeded.text)

    vector_results = search_service.vector_search(
        db, query_vec, SearchFilters(collection='eng-docs'), None, ['support-docs'], limit=50
    )
    assert vector_results == []

    fulltext_results = search_service.fulltext_search(
        db, seeded.text, SearchFilters(collection='eng-docs'), None, ['support-docs'], limit=50
    )
    assert fulltext_results == []


def test_allowed_teams_and_allowed_collections_both_enforced_together(db):
    """The two hard boundaries are independent AND-ed constraints -- a chunk
    must satisfy BOTH to surface, satisfying only one is not enough."""
    text = 'Ihr Vertrag wird automatisch verlaengert, sofern Sie nicht widersprechen.'
    doc_both_ok = _seed_doc(
        db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED, collection_slug='support-docs'
    )
    chunk_both_ok = _seed_chunk(db, doc_both_ok, text, chunk_index=0)

    # Right team, wrong collection.
    doc_wrong_collection = _seed_doc(
        db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED, collection_slug='eng-docs'
    )
    chunk_wrong_collection = _seed_chunk(db, doc_wrong_collection, text, chunk_index=0)

    # Right collection, wrong team.
    doc_wrong_team = _seed_doc(
        db, team='Engineering', department='Platform', status=DocumentStatus.INDEXED, collection_slug='support-docs'
    )
    chunk_wrong_team = _seed_chunk(db, doc_wrong_team, text, chunk_index=0)

    db.commit()
    for chunk in (chunk_both_ok, chunk_wrong_collection, chunk_wrong_team):
        db.refresh(chunk)

    query_vec = _embed(text)
    result_ids = set(
        _ids(
            search_service.vector_search(
                db, query_vec, None, ['Kundenservice'], ['support-docs'], limit=50
            )
        )
    )
    assert result_ids == {chunk_both_ok.id}


# --- rrf_fuse (pure function -- no DB needed) --------------------------------------


def test_rrf_fuse_prefers_a_chunk_found_by_both_legs():
    chunk_vector_only = SimpleNamespace(id=2)
    chunk_fulltext_only = SimpleNamespace(id=3)
    chunk_both = SimpleNamespace(id=1)

    vector_results = [(chunk_vector_only, 0.99), (chunk_both, 0.9)]
    fulltext_results = [(chunk_fulltext_only, 1.0), (chunk_both, 0.8)]

    fused = search_service.rrf_fuse(vector_results, fulltext_results, k=60)

    assert fused[0].chunk.id == chunk_both.id
    assert fused[0].vector_score == 0.9
    assert fused[0].fulltext_score == 0.8
    assert fused[0].rrf_score == pytest.approx(1 / 62 + 1 / 62)
    # Beats EITHER single-list top hit even though it wasn't rank 1 in
    # either individual list -- exactly what RRF is for.
    assert fused[0].rrf_score > fused[1].rrf_score


def test_rrf_fuse_orders_ties_by_chunk_id():
    chunk_high_id = SimpleNamespace(id=5)
    chunk_low_id = SimpleNamespace(id=2)
    # Each appears only once, both at rank 1 of their own (disjoint) list --
    # a genuine rrf_score tie (1/(k+1) each), broken only by chunk.id.
    fused = search_service.rrf_fuse([(chunk_high_id, 0.9)], [(chunk_low_id, 1.0)], k=60)
    assert [entry.chunk.id for entry in fused] == [2, 5]
    assert fused[0].rrf_score == pytest.approx(fused[1].rrf_score)


def test_rrf_fuse_reads_settings_rrf_k_by_default(monkeypatch):
    monkeypatch.setattr(settings, 'rrf_k', 10)
    chunk = SimpleNamespace(id=1)
    fused = search_service.rrf_fuse([(chunk, 1.0)], [], k=None)
    assert fused[0].rrf_score == pytest.approx(1 / 11)


# --- resolve_top_k / resolve_final_k -----------------------------------------------


def test_resolve_top_k_and_final_k_default_from_settings():
    request = SearchRequest(query='x')
    assert search_service.resolve_top_k(request) == settings.search_top_k
    assert search_service.resolve_final_k(request) == settings.search_final_k


def test_resolve_top_k_and_final_k_use_request_overrides():
    request = SearchRequest(query='x', top_k=3, final_k=2)
    assert search_service.resolve_top_k(request) == 3
    assert search_service.resolve_final_k(request) == 2


# --- search() end-to-end -----------------------------------------------------------


def test_search_pipeline_end_to_end(corpus):
    request = SearchRequest(query=_HIT_TEXT, allowed_teams=['Kundenservice'], top_k=10)
    results, trace = search_service.search(corpus.db, request)
    result_ids = [r.chunk_id for r in results]

    assert corpus.hit_chunk.id in result_ids
    # allowed_teams boundary holds even through the full pipeline, not just
    # the individual leg functions.
    assert corpus.other_team_chunk.id not in result_ids
    assert corpus.blocked_chunk.id not in result_ids
    assert corpus.pending_chunk.id not in result_ids
    assert corpus.superseded_chunk.id not in result_ids

    hit_result = next(r for r in results if r.chunk_id == corpus.hit_chunk.id)
    assert hit_result.scores.vector == pytest.approx(1.0, abs=1e-6)
    assert hit_result.scores.fulltext == pytest.approx(1.0, abs=1e-6)
    assert hit_result.scores.rrf > 0
    assert hit_result.scores.rerank is None
    assert hit_result.document_version == 1
    assert hit_result.team == 'Kundenservice'
    # corpus's own documents never set collection_slug -- a pre-Collections
    # legacy document, so the reported collection is None, not a made-up
    # slug (see test_search_pipeline_reports_source_collection below for the
    # non-legacy case).
    assert hit_result.collection is None
    assert hit_result.embedding_model == settings.embedding_model

    # In-scope, indexed, exact textual match -- but vector-skipped due to its
    # own model mismatch, so only the fulltext leg contributed its score.
    wrong_model_result = next(r for r in results if r.chunk_id == corpus.wrong_model_chunk.id)
    assert wrong_model_result.scores.vector is None
    assert wrong_model_result.scores.fulltext == pytest.approx(1.0, abs=1e-6)

    assert trace.vector_candidates >= 1
    assert trace.fulltext_candidates >= 1
    assert trace.fused >= 1
    # settings.rerank_provider defaults to 'none' (NoopReranker) -- every
    # fused candidate still passes through the reranking stage (reranked ==
    # the RRF top_k pool size, see SearchTrace's own docstring), it's just
    # that stage's SCORE that stays None, not the candidate count. Nothing
    # here exceeds settings.search_final_k's default (5), so final_k
    # trimming doesn't drop anything below `reranked` either.
    assert trace.reranked == len(results)
    assert not trace.rerank_error
    assert set(trace.timings_ms.keys()) == {'embed', 'vector_search', 'fulltext_search', 'fuse', 'rerank'}
    assert all(value >= 0 for value in trace.timings_ms.values())


def test_search_pipeline_keeps_fused_top_k_not_final_k(corpus):
    """search() keeps the fused top `top_k`, not `final_k` -- trimming to
    final_k is a reranker's job (Stage Integration), not this pipeline's."""
    request = SearchRequest(query=_HIT_TEXT, allowed_teams=['Kundenservice'], top_k=2, final_k=5)
    results, trace = search_service.search(corpus.db, request)

    assert len(results) == 2
    # More unique candidates existed across both legs (hit_chunk +
    # wrong_model_chunk tied at the top of the fulltext leg, plus whichever
    # of partial_chunk/unrelated_chunk the vector leg's own top_k=2 picked
    # up as its second candidate) than the top_k=2 trim keeps.
    assert trace.fused > len(results)


def test_search_pipeline_enforces_allowed_collections_alongside_allowed_teams(db):
    """End-to-end proof that `SearchRequest.allowed_collections` is actually
    wired into search(), not just into the lower-level leg functions --
    and that it composes with `allowed_teams` as two independent AND-ed
    boundaries, exactly like test_allowed_teams_and_allowed_collections_
    both_enforced_together above proves at the vector_search() level."""
    text = 'Bitte aktualisieren Sie Ihre Rechnungsadresse im Kundenportal.'

    doc_both_ok = _seed_doc(
        db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED, collection_slug='support-docs'
    )
    chunk_both_ok = _seed_chunk(db, doc_both_ok, text, chunk_index=0)

    doc_wrong_collection = _seed_doc(
        db, team='Kundenservice', department='Support', status=DocumentStatus.INDEXED, collection_slug='eng-docs'
    )
    chunk_wrong_collection = _seed_chunk(db, doc_wrong_collection, text, chunk_index=0)

    db.commit()
    for chunk in (chunk_both_ok, chunk_wrong_collection):
        db.refresh(chunk)

    request = SearchRequest(
        query=text, allowed_teams=['Kundenservice'], allowed_collections=['support-docs'], top_k=10
    )
    results, _trace = search_service.search(db, request)
    result_ids = [r.chunk_id for r in results]

    assert chunk_both_ok.id in result_ids
    assert chunk_wrong_collection.id not in result_ids

    both_ok_result = next(r for r in results if r.chunk_id == chunk_both_ok.id)
    assert both_ok_result.collection == 'support-docs'


def test_search_pipeline_reports_source_collection_for_scope_verification(db):
    """SearchResult.collection is the basis for a downstream caller (e.g. a
    Weave-Runtime security check) to verify a reported source against its
    OWN authorized scope -- a hit from a real collection must carry that
    collection's slug, and a legacy hit with no collection at all must
    carry `None`, never a guessed or omitted value."""
    seeded = _seed_collection_corpus(db)

    request = SearchRequest(query=seeded.text, allowed_teams=['Kundenservice'], top_k=10)
    results, _trace = search_service.search(db, request)
    collection_by_chunk_id = {r.chunk_id: r.collection for r in results}

    assert collection_by_chunk_id[seeded.support_chunk.id] == 'support-docs'
    assert collection_by_chunk_id[seeded.eng_chunk.id] == 'eng-docs'
    assert collection_by_chunk_id[seeded.legacy_chunk.id] is None
