"""Tests for app.services.evalharness (evaluate(), load_golden(),
get_search_fn()) and app.cli's `eval` subcommand.

evaluate()'s Recall@k / MRR / Hit@1 test cases below are hand-computed in
each test's docstring/comments -- not just "assert it runs" -- per three
scenarios: a search_fn that returns exactly the relevant chunks
("perfekt"), one that finds some but not all judgments and not always at
rank 1 ("teilweise"), and one that returns nothing at all ("leer").
"""

import logging
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.cli import run_eval, run_seed_demo
from app.core.config import settings
from app.services.evalharness import (
    GoldenQuery,
    GoldenRelevant,
    RetrievedChunk,
    evaluate,
    get_search_fn,
    load_golden,
)

# Resolved relative to this test file, not the process cwd -- the documented
# test invocation (`cd Weave-Retrieval && .venv/bin/python -m pytest
# backend/tests -q`, see README) runs with the repo root as cwd, not
# `backend/`, so a bare 'eval/golden.example.yaml' would not resolve.
GOLDEN_EXAMPLE_PATH = Path(__file__).resolve().parent.parent / 'eval' / 'golden.example.yaml'


# --- evaluate(): perfect / partial / empty search_fn ---------------------------

def test_evaluate_perfect_search_fn():
    """search_fn returns exactly the relevant chunk(s), first, for every
    query -> Recall@k=1.0 at every k, MRR=1.0, Hit@1=1.0, for every query
    and in aggregate. q2 has two relevant judgments and its top-2 results
    satisfy both, so even Recall@1 (which only sees one of the two) is not
    exercised here -- see test_evaluate_partial_search_fn for a case where a
    single top-ranked result can only ever satisfy one of several
    judgments."""
    golden = [
        GoldenQuery(query='q1', relevant=[GoldenRelevant(source_job_id='doc-1')]),
        GoldenQuery(query='q2', relevant=[GoldenRelevant(source_job_id='doc-2', contains='alpha')]),
    ]

    def search_fn(query: str) -> list[RetrievedChunk]:
        if query == 'q1':
            return [RetrievedChunk(source_job_id='doc-1', text='the only chunk')]
        return [RetrievedChunk(source_job_id='doc-2', text='contains alpha here')]

    result = evaluate(search_fn, golden, ks=[1, 3])

    for query_metrics in result.per_query:
        assert query_metrics.recall_at_k == {1: pytest.approx(1.0), 3: pytest.approx(1.0)}
        assert query_metrics.mrr == pytest.approx(1.0)
        assert query_metrics.hit_at_1 == pytest.approx(1.0)

    assert result.recall_at_k == {1: pytest.approx(1.0), 3: pytest.approx(1.0)}
    assert result.mrr == pytest.approx(1.0)
    assert result.hit_at_1 == pytest.approx(1.0)


def test_evaluate_partial_search_fn():
    """search_fn for q1 buries its one relevant chunk at rank 2 behind an
    irrelevant hit; for q2 it finds one of two relevant judgments (at rank
    1) and never finds the second. Hand-computed with ks=[1, 3]:

    q1: retrieved = [doc-9 (irrelevant), doc-1 (relevant)]
        recall@1 = 0/1 = 0.0   (top-1 is doc-9, no match)
        recall@3 = 1/1 = 1.0   (doc-1 is within the first 3 -- only 2 exist)
        mrr      = 1/2 = 0.5   (first match at rank 2)
        hit@1    = 0.0

    q2: retrieved = [doc-2/"alpha" (matches R1), doc-9 (irrelevant), doc-2/"neither" (matches nothing)]
        R1=(doc-2, contains "alpha"), R2=(doc-2, contains "beta")
        recall@1 = 1/2 = 0.5   (top-1 matches R1 only)
        recall@3 = 1/2 = 0.5   (R2 never matched by any of the 3 retrieved chunks)
        mrr      = 1/1 = 1.0   (first match at rank 1)
        hit@1    = 1.0

    aggregate (macro-average over 2 queries):
        recall@1 = (0.0 + 0.5) / 2 = 0.25
        recall@3 = (1.0 + 0.5) / 2 = 0.75
        mrr      = (0.5 + 1.0) / 2 = 0.75
        hit@1    = (0.0 + 1.0) / 2 = 0.5
    """
    golden = [
        GoldenQuery(query='q1', relevant=[GoldenRelevant(source_job_id='doc-1')]),
        GoldenQuery(
            query='q2',
            relevant=[
                GoldenRelevant(source_job_id='doc-2', contains='alpha'),
                GoldenRelevant(source_job_id='doc-2', contains='beta'),
            ],
        ),
    ]

    def search_fn(query: str) -> list[RetrievedChunk]:
        if query == 'q1':
            return [
                RetrievedChunk(source_job_id='doc-9', text='irrelevant'),
                RetrievedChunk(source_job_id='doc-1', text='the actual chunk'),
            ]
        return [
            RetrievedChunk(source_job_id='doc-2', text='contains alpha only'),
            RetrievedChunk(source_job_id='doc-9', text='irrelevant'),
            RetrievedChunk(source_job_id='doc-2', text='unrelated text, neither keyword'),
        ]

    result = evaluate(search_fn, golden, ks=[1, 3])

    q1, q2 = result.per_query
    assert q1.query == 'q1'
    assert q1.recall_at_k == {1: pytest.approx(0.0), 3: pytest.approx(1.0)}
    assert q1.mrr == pytest.approx(0.5)
    assert q1.hit_at_1 == pytest.approx(0.0)

    assert q2.query == 'q2'
    assert q2.recall_at_k == {1: pytest.approx(0.5), 3: pytest.approx(0.5)}
    assert q2.mrr == pytest.approx(1.0)
    assert q2.hit_at_1 == pytest.approx(1.0)

    assert result.recall_at_k == {1: pytest.approx(0.25), 3: pytest.approx(0.75)}
    assert result.mrr == pytest.approx(0.75)
    assert result.hit_at_1 == pytest.approx(0.5)


def test_evaluate_empty_search_fn():
    """search_fn returns nothing for any query -> every metric is 0.0,
    for every query and in aggregate; also exercises evaluate()'s own
    default `ks=[5, 10]`."""
    golden = [
        GoldenQuery(query='q1', relevant=[GoldenRelevant(source_job_id='doc-1')]),
        GoldenQuery(query='q2', relevant=[GoldenRelevant(source_job_id='doc-2', contains='anything')]),
    ]

    def search_fn(query: str) -> list[RetrievedChunk]:
        return []

    result = evaluate(search_fn, golden)

    for query_metrics in result.per_query:
        assert query_metrics.recall_at_k == {5: 0.0, 10: 0.0}
        assert query_metrics.mrr == 0.0
        assert query_metrics.hit_at_1 == 0.0

    assert result.recall_at_k == {5: 0.0, 10: 0.0}
    assert result.mrr == 0.0
    assert result.hit_at_1 == 0.0


# --- evaluate(): validation and GoldenQuery.k depth-capping ---------------------

def test_evaluate_empty_golden_raises():
    with pytest.raises(ValueError):
        evaluate(lambda query: [], [])


def test_evaluate_empty_relevant_raises():
    golden = [GoldenQuery(query='q1', relevant=[])]
    with pytest.raises(ValueError):
        evaluate(lambda query: [], golden)


def test_evaluate_empty_ks_raises():
    golden = [GoldenQuery(query='q1', relevant=[GoldenRelevant(source_job_id='doc-1')])]
    with pytest.raises(ValueError):
        evaluate(lambda query: [], golden, ks=[])


def test_evaluate_per_query_k_caps_considered_depth():
    """GoldenQuery.k=1 means only the top-1 result is ever considered for
    THIS query, even though a relevant chunk sits at rank 2 and ks asks for
    Recall@3 -- so recall@3 for this query must equal recall@1 (both 0.0),
    not 1.0."""
    golden = [GoldenQuery(query='q1', relevant=[GoldenRelevant(source_job_id='doc-1')], k=1)]

    def search_fn(query: str) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(source_job_id='doc-9', text='irrelevant'),
            RetrievedChunk(source_job_id='doc-1', text='relevant but beyond k=1'),
        ]

    result = evaluate(search_fn, golden, ks=[1, 3])
    assert result.per_query[0].recall_at_k == {1: 0.0, 3: 0.0}
    assert result.per_query[0].mrr == 0.0


# --- load_golden(): the shipped example, plus malformed inputs -----------------

def test_load_golden_example_parses():
    golden = load_golden(GOLDEN_EXAMPLE_PATH)
    assert len(golden) == 6
    # The example ships exactly one unanswerable entry (answer.answerable:
    # false, relevant: []) -- every other entry carries judgments.
    unanswerable = [item for item in golden if not item.answerable]
    assert len(unanswerable) == 1
    assert unanswerable[0].relevant == []
    assert any(item.answer is not None and item.answerable for item in golden)
    for item in golden:
        assert isinstance(item, GoldenQuery)
        assert item.query
        assert item.relevant or not item.answerable
        for rel in item.relevant:
            assert isinstance(rel, GoldenRelevant)
            assert rel.source_job_id
            assert rel.contains is None or isinstance(rel.contains, str)
        assert item.k is None or (isinstance(item.k, int) and item.k > 0)

    # Spot-check the format actually exercises both relevance shapes: at
    # least one document-level judgment (contains=None) and at least one
    # source+text-marker judgment (contains set).
    all_relevant = [rel for item in golden for rel in item.relevant]
    assert any(rel.contains is None for rel in all_relevant)
    assert any(rel.contains is not None for rel in all_relevant)


def test_load_golden_missing_file_raises_oserror():
    with pytest.raises(OSError):
        load_golden('does/not/exist.yaml')


def test_load_golden_not_a_list_raises(tmp_path):
    path = tmp_path / 'golden.yaml'
    path.write_text(yaml.safe_dump({'query': 'not a list'}), encoding='utf-8')
    with pytest.raises(ValueError):
        load_golden(path)


def test_load_golden_invalid_yaml_raises(tmp_path):
    path = tmp_path / 'golden.yaml'
    path.write_text('query: [unterminated', encoding='utf-8')
    with pytest.raises(ValueError):
        load_golden(path)


def test_load_golden_missing_query_raises(tmp_path):
    path = tmp_path / 'golden.yaml'
    path.write_text(yaml.safe_dump([{'relevant': [{'source_job_id': 'x'}]}]), encoding='utf-8')
    with pytest.raises(ValueError):
        load_golden(path)


def test_load_golden_empty_relevant_raises(tmp_path):
    path = tmp_path / 'golden.yaml'
    path.write_text(yaml.safe_dump([{'query': 'q', 'relevant': []}]), encoding='utf-8')
    with pytest.raises(ValueError):
        load_golden(path)


def test_load_golden_relevant_missing_source_job_id_raises(tmp_path):
    path = tmp_path / 'golden.yaml'
    path.write_text(yaml.safe_dump([{'query': 'q', 'relevant': [{'contains': 'x'}]}]), encoding='utf-8')
    with pytest.raises(ValueError):
        load_golden(path)


def test_load_golden_invalid_k_raises(tmp_path):
    path = tmp_path / 'golden.yaml'
    path.write_text(
        yaml.safe_dump([{'query': 'q', 'relevant': [{'source_job_id': 'x'}], 'k': -1}]), encoding='utf-8'
    )
    with pytest.raises(ValueError):
        load_golden(path)


def test_load_golden_k_omitted_defaults_to_none(tmp_path):
    path = tmp_path / 'golden.yaml'
    path.write_text(yaml.safe_dump([{'query': 'q', 'relevant': [{'source_job_id': 'x'}]}]), encoding='utf-8')
    golden = load_golden(path)
    assert golden[0].k is None


# --- get_search_fn() -------------------------------------------------------------
#
# Both tests below monkeypatch app.core.db's module-level `engine`/
# `SessionLocal` (and settings.database_url, for consistency and for
# run_seed_demo()'s own sqlite-only guard) to a throwaway sqlite file under
# `tmp_path`, rather than pointing DATABASE_URL at the shared
# tests/conftest.py `test.db` -- app.core.db.engine/SessionLocal are built
# once, at that module's own import time, from whatever settings.database_url
# was at that moment (already the process-wide default 'sqlite:///./weave_
# retrieval.db' by the time this test file runs), so mutating the setting
# alone here would have no effect; get_search_fn()'s search_fn and
# run_seed_demo() both re-read app.core.db's attributes via a LOCAL import
# at call time (see their own docstrings for why), which is exactly what
# makes patching those attributes -- not just the setting -- effective.


def _use_tmp_sqlite_db(monkeypatch, tmp_path) -> None:
    import app.core.db as db_module

    db_url = f'sqlite:///{tmp_path / "eval_demo.db"}'
    engine = create_engine(db_url, future=True)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(settings, 'database_url', db_url)
    monkeypatch.setattr(db_module, 'engine', engine)
    monkeypatch.setattr(db_module, 'SessionLocal', session_local)


def test_get_search_fn_queries_the_configured_database(monkeypatch, tmp_path):
    """get_search_fn() returns a search_fn backed by the REAL hybrid-search
    pipeline (app/services/search.py), not a stub -- seed one document/chunk
    directly (bypassing run_seed_demo()'s fixed corpus) and confirm the
    returned search_fn actually finds it, with `source_job_id` resolved from
    the owning Document row as documented."""
    _use_tmp_sqlite_db(monkeypatch, tmp_path)

    import uuid
    from datetime import datetime, timezone

    import app.core.db as db_module
    from app.models.models import Chunk, Document, DocumentStatus
    from app.services.embeddings import FakeEmbeddingProvider

    db_module.Base.metadata.create_all(bind=db_module.engine)  # schema only -- no seeded corpus

    text = 'Das WLAN-Passwort steht auf der Rückseite des Routers.'
    db = db_module.SessionLocal()
    try:
        doc = Document(
            source_job_id=str(uuid.uuid4()),
            content_sha256='f' * 64,
            engine='paddleocr',
            frontmatter={},
            tags=[],
            processed_at=datetime.now(timezone.utc),
            status=DocumentStatus.INDEXED,
        )
        db.add(doc)
        db.flush()
        provider = FakeEmbeddingProvider()
        db.add(Chunk(
            document_id=doc.id, chunk_index=0, text=text, heading_path=[], char_count=len(text), meta={},
            embedding=provider.embed_query(text), embedding_model=provider.model_name,
        ))
        db.commit()
        expected_source_job_id = doc.source_job_id
    finally:
        db.close()

    search_fn = get_search_fn()
    retrieved = search_fn(text)

    assert len(retrieved) == 1
    assert retrieved[0].source_job_id == expected_source_job_id
    assert retrieved[0].text == text


# --- app.cli's `seed-demo` + `eval`, end-to-end against a real database ----------


def test_cli_eval_end_to_end_against_seeded_demo_db(monkeypatch, tmp_path, capsys):
    """The full advertised workflow (README's "Evaluation"): `seed-demo`
    populates a fresh database with a corpus matching backend/eval/
    golden.example.yaml's own judgments, then `eval` against that exact
    golden set is run through app.cli.run_eval() (not evaluate() directly)
    to prove the CLI subcommand itself -- argument handling, get_search_fn()
    wiring, table printing, the --min-recall gate -- works end-to-end, not
    just evaluate() as a pure function.
    """
    _use_tmp_sqlite_db(monkeypatch, tmp_path)

    assert run_seed_demo() == 0

    exit_code = run_eval(golden_path=str(GOLDEN_EXAMPLE_PATH), min_recall=1.0)
    printed = capsys.readouterr().out

    assert exit_code == 0, printed
    assert 'AGGREGATE' in printed
    assert 'R@5' in printed


def test_cli_seed_demo_refuses_non_sqlite_database_url(monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(settings, 'database_url', 'postgresql://example/weave_knowledge')

    exit_code = run_seed_demo()

    assert exit_code == 2
    assert any('non-sqlite' in record.message for record in caplog.records)


# --- app.cli's `eval` subcommand -------------------------------------------------

def _perfect_search_fn_for(golden: list[GoldenQuery]):
    """A search_fn that returns, for each golden query, exactly the chunks
    needed to satisfy every one of its relevant judgments, in order -- see
    test_evaluate_perfect_search_fn for why that guarantees Recall@k=1.0 /
    MRR=1.0 / Hit@1=1.0 at every k."""
    by_query = {
        item.query: [
            RetrievedChunk(source_job_id=rel.source_job_id, text=rel.contains or 'irrelevant filler text')
            for rel in item.relevant
        ]
        for item in golden
    }

    def search_fn(query: str) -> list[RetrievedChunk]:
        return by_query.get(query, [])

    return search_fn


def test_cli_eval_perfect_search_fn_passes_gate(monkeypatch, capsys):
    golden = load_golden(GOLDEN_EXAMPLE_PATH)
    monkeypatch.setattr('app.cli.get_search_fn', lambda: _perfect_search_fn_for(golden))

    exit_code = run_eval(golden_path=GOLDEN_EXAMPLE_PATH, min_recall=1.0)

    assert exit_code == 0
    printed = capsys.readouterr().out
    assert 'AGGREGATE' in printed
    assert 'R@5' in printed
    assert 'MRR' in printed
    assert 'Hit@1' in printed


def test_cli_eval_below_min_recall_returns_1(monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    golden = load_golden(GOLDEN_EXAMPLE_PATH)
    monkeypatch.setattr('app.cli.get_search_fn', lambda: _perfect_search_fn_for(golden))

    # Perfect search_fn tops out at Recall@5 == 1.0 -- anything above that
    # must fail the gate.
    exit_code = run_eval(golden_path=GOLDEN_EXAMPLE_PATH, min_recall=1.5)

    assert exit_code == 1
    assert any('below --min-recall' in record.message for record in caplog.records)


def test_cli_eval_missing_golden_file_returns_2(caplog):
    caplog.set_level(logging.ERROR)
    exit_code = run_eval(golden_path='does/not/exist.yaml')
    assert exit_code == 2
    assert any('failed to load golden set' in record.message for record in caplog.records)


def test_cli_eval_custom_ks_always_include_gate_k(monkeypatch, capsys):
    """--k 20 alone must not drop Recall@5 from the report -- it's the fixed
    gate metric regardless of which cutoffs were requested."""
    golden = load_golden(GOLDEN_EXAMPLE_PATH)
    monkeypatch.setattr('app.cli.get_search_fn', lambda: _perfect_search_fn_for(golden))

    exit_code = run_eval(golden_path=GOLDEN_EXAMPLE_PATH, ks=[20])

    assert exit_code == 0
    printed = capsys.readouterr().out
    assert 'R@5' in printed
    assert 'R@20' in printed


def test_get_search_fn_reads_depth_after_control_plane_refresh(monkeypatch):
    """The central SEARCH_TOP_K (applied by refresh_control_plane()) must
    already govern the FIRST golden query's depth, not only later ones."""
    import app.services.search as search_module

    monkeypatch.setattr(settings, 'search_top_k', 20)
    monkeypatch.setattr(search_module, 'refresh_control_plane', lambda: setattr(settings, 'search_top_k', 50))
    seen: list[tuple[int | None, int | None, bool]] = []

    def fake_search(db, request, *, refresh=True):
        seen.append((request.top_k, request.final_k, refresh))
        return [], None

    monkeypatch.setattr(search_module, 'search', fake_search)

    get_search_fn()('erste Anfrage')

    assert seen == [(50, 50, False)]


# --- Precision@k / nDCG@k ----------------------------------------------------------

def _rc(source_job_id: str, text: str = 'filler') -> RetrievedChunk:
    return RetrievedChunk(source_job_id=source_job_id, text=text)


def test_precision_and_ndcg_hand_computed():
    """Two judgments (doc A, doc B); hits: [X, A, A, B, Y].

    Precision@k counts every rank whose hit matches ANY judgment: ranks 2,3,4
    -> P@2 = 1/2, P@5 = 3/5. nDCG counts each judgment once: A credits at
    rank 2, the second A chunk (rank 3) credits nothing, B credits at rank 4.
      DCG@5  = 1/log2(3) + 1/log2(5)
      IDCG@5 = 1/log2(2) + 1/log2(3)  (two judgments at ranks 1,2)
      DCG@2  = 1/log2(3); IDCG@2 = same as IDCG@5
    """
    import math

    golden = [GoldenQuery(query='q', relevant=[GoldenRelevant('A'), GoldenRelevant('B')])]
    hits = [_rc('X'), _rc('A'), _rc('A'), _rc('B'), _rc('Y')]
    result = evaluate(lambda q: hits, golden, ks=[2, 5])

    idcg = 1.0 + 1.0 / math.log2(3)
    q = result.per_query[0]
    assert q.precision_at_k == {2: pytest.approx(0.5), 5: pytest.approx(0.6)}
    assert q.ndcg_at_k[2] == pytest.approx((1.0 / math.log2(3)) / idcg)
    assert q.ndcg_at_k[5] == pytest.approx((1.0 / math.log2(3) + 1.0 / math.log2(5)) / idcg)
    assert result.precision_at_k == q.precision_at_k
    assert result.ndcg_at_k == q.ndcg_at_k
    assert result.ks == [2, 5]
    # existing metrics unchanged
    assert q.recall_at_k == {2: 0.5, 5: 1.0}
    assert q.mrr == pytest.approx(0.5)
    assert q.hit_at_1 == 0.0


def test_ndcg_perfect_ranking_is_one_and_ideal_capped_at_k():
    """Three judgments but k=2: the ideal DCG only counts min(3, 2) = 2
    hits, so two relevant hits at ranks 1-2 score a perfect nDCG@2."""
    golden = [GoldenQuery(query='q', relevant=[GoldenRelevant('A'), GoldenRelevant('B'), GoldenRelevant('C')])]
    result = evaluate(lambda q: [_rc('A'), _rc('B'), _rc('C')], golden, ks=[2, 3])
    assert result.ndcg_at_k[2] == pytest.approx(1.0)
    assert result.ndcg_at_k[3] == pytest.approx(1.0)
    assert result.precision_at_k[2] == pytest.approx(1.0)


def test_ndcg_hit_matching_two_judgments_credits_only_one():
    """One chunk matching both a document-level and a text-marker judgment
    of the same document credits only ONE of them -- each judgment needs
    its own hit."""
    import math

    golden = [
        GoldenQuery(query='q', relevant=[GoldenRelevant('A'), GoldenRelevant('A', contains='marker')])
    ]
    result = evaluate(lambda q: [_rc('A', 'has marker')], golden, ks=[2])
    idcg = 1.0 + 1.0 / math.log2(3)
    assert result.ndcg_at_k[2] == pytest.approx(1.0 / idcg)
    assert result.precision_at_k[2] == pytest.approx(0.5)


@pytest.mark.parametrize('marker_first', [False, True])
def test_ndcg_assignment_is_independent_of_judgment_order(marker_first):
    """A doc-level and a `contains` judgment of the same document, hits
    [A chunk with marker, plain A chunk]: the marker chunk must credit the
    `contains` judgment so the plain chunk can still credit the doc-level
    one -- a perfect ranking scores 1.0 whatever the YAML order."""
    relevant = [GoldenRelevant('A'), GoldenRelevant('A', contains='marker')]
    if marker_first:
        relevant.reverse()
    golden = [GoldenQuery(query='q', relevant=relevant)]
    result = evaluate(lambda q: [_rc('A', 'has marker'), _rc('A', 'plain')], golden, ks=[2])
    assert result.ndcg_at_k[2] == pytest.approx(1.0)
    assert result.recall_at_k[2] == pytest.approx(1.0)


def test_ndcg_matching_reassigns_earlier_hit_through_a_chain():
    """Hits [h1 matches J1,J2; h2 matches J2,J3; h3 matches J1 only]:
    greedy would credit J1 at rank 1, J2 at rank 2 and nothing at rank 3.
    The matching moves h1 -> J2 -> h2 -> J3 so h3 credits J1: all three
    ranks gain and nDCG@3 == 1.0."""
    relevant = [
        GoldenRelevant('A', contains='one'),
        GoldenRelevant('A', contains='two'),
        GoldenRelevant('A', contains='three'),
    ]
    hits = [_rc('A', 'one two'), _rc('A', 'two three'), _rc('A', 'one')]
    result = evaluate(lambda q: hits, [GoldenQuery(query='q', relevant=relevant)], ks=[3])
    assert result.ndcg_at_k[3] == pytest.approx(1.0)


def test_ndcg_matching_prefers_earlier_ranks():
    """Only one judgment, two matching hits: rank 1 is credited (not 2)."""
    golden = [GoldenQuery(query='q', relevant=[GoldenRelevant('A')])]
    result = evaluate(lambda q: [_rc('A'), _rc('A')], golden, ks=[2])
    assert result.ndcg_at_k[2] == pytest.approx(1.0)


@pytest.mark.parametrize('ks', [[0], [0, 5], [-1, 5]])
def test_evaluate_non_positive_k_raises(ks):
    golden = [GoldenQuery(query='q1', relevant=[GoldenRelevant(source_job_id='doc-1')])]
    with pytest.raises(ValueError, match='positive'):
        evaluate(lambda query: [_rc('doc-1')], golden, ks=ks)


def test_cli_eval_k_zero_is_usage_error_exit_2(monkeypatch, caplog):
    """--k 0 is a usage error (exit 2), not a gate failure (exit 1) or a
    ZeroDivisionError traceback."""
    caplog.set_level(logging.ERROR)
    golden = load_golden(GOLDEN_EXAMPLE_PATH)
    monkeypatch.setattr('app.cli.get_search_fn', lambda: _perfect_search_fn_for(golden))
    assert run_eval(golden_path=GOLDEN_EXAMPLE_PATH, ks=[0]) == 2
    assert any('positive' in record.message for record in caplog.records)


def test_precision_and_ndcg_empty_search_fn_are_zero():
    golden = [GoldenQuery(query='q', relevant=[GoldenRelevant('A')])]
    result = evaluate(lambda q: [], golden, ks=[5])
    assert result.precision_at_k == {5: 0.0}
    assert result.ndcg_at_k == {5: 0.0}


def test_precision_respects_per_query_depth_cap():
    """GoldenQuery.k=1 caps the considered hits; Precision@5 is still
    divided by 5."""
    golden = [GoldenQuery(query='q', relevant=[GoldenRelevant('A')], k=1)]
    result = evaluate(lambda q: [_rc('A'), _rc('A')], golden, ks=[5])
    assert result.precision_at_k[5] == pytest.approx(0.2)


# --- answer block / unanswerable entries -----------------------------------------

def test_evaluate_skips_unanswerable_entries():
    calls: list[str] = []

    def search_fn(query: str) -> list[RetrievedChunk]:
        calls.append(query)
        return [_rc('A')]

    golden = [
        GoldenQuery(query='answerable', relevant=[GoldenRelevant('A')]),
        GoldenQuery(query='unanswerable', relevant=[], answer={'answerable': False}),
        GoldenQuery(query='unanswerable with judgments', relevant=[GoldenRelevant('Z')], answer={'answerable': False}),
    ]
    result = evaluate(search_fn, golden, ks=[5])
    assert calls == ['answerable']
    assert [q.query for q in result.per_query] == ['answerable']
    assert result.skipped_unanswerable == 2
    assert result.recall_at_k == {5: 1.0}


def test_evaluate_only_unanswerable_raises():
    golden = [GoldenQuery(query='q', relevant=[], answer={'answerable': False})]
    with pytest.raises(ValueError):
        evaluate(lambda q: [], golden)


def test_answerable_true_with_empty_relevant_still_raises():
    golden = [GoldenQuery(query='q', relevant=[], answer={'answerable': True})]
    with pytest.raises(ValueError):
        evaluate(lambda q: [], golden)


def _write_golden(tmp_path, entries) -> Path:
    path = tmp_path / 'golden.yaml'
    path.write_text(yaml.safe_dump(entries, allow_unicode=True), encoding='utf-8')
    return path


def test_load_golden_answer_block_is_kept_verbatim(tmp_path):
    answer = {
        'answerable': True,
        'reference': 'Zwei Wochen.',
        'must_include': ['zwei Wochen', ['Formular A', 'Formblatt A']],
        'must_not_include': ['vier Wochen'],
        'must_cite': ['x'],
    }
    golden = load_golden(_write_golden(tmp_path, [{'query': 'q', 'relevant': [{'source_job_id': 'x'}], 'answer': answer}]))
    assert golden[0].answer == answer
    assert golden[0].answerable is True


def test_load_golden_without_answer_is_answerable(tmp_path):
    golden = load_golden(_write_golden(tmp_path, [{'query': 'q', 'relevant': [{'source_job_id': 'x'}]}]))
    assert golden[0].answer is None
    assert golden[0].answerable is True


@pytest.mark.parametrize('relevant_key', ['empty', 'omitted'])
def test_load_golden_unanswerable_may_have_no_relevant(tmp_path, relevant_key):
    entry = {'query': 'q', 'answer': {'answerable': False}}
    if relevant_key == 'empty':
        entry['relevant'] = []
    golden = load_golden(_write_golden(tmp_path, [entry]))
    assert golden[0].relevant == []
    assert golden[0].answerable is False


@pytest.mark.parametrize(
    'entry',
    [
        {'query': 'q', 'relevant': [{'source_job_id': 'x'}], 'answer': 'not a mapping'},
        {'query': 'q', 'relevant': [{'source_job_id': 'x'}], 'answer': ['list']},
        {'query': 'q', 'relevant': [{'source_job_id': 'x'}], 'answer': {'answerable': 'no'}},
        # answerable true (explicit or default) still needs judgments
        {'query': 'q', 'relevant': [], 'answer': {'answerable': True}},
        {'query': 'q', 'answer': {'reference': 'r'}},
        # unanswerable, but relevant is not a list / has an invalid judgment
        {'query': 'q', 'relevant': 'x', 'answer': {'answerable': False}},
        {'query': 'q', 'relevant': [{'contains': 'x'}], 'answer': {'answerable': False}},
    ],
)
def test_load_golden_invalid_answer_entries_raise(tmp_path, entry):
    with pytest.raises(ValueError):
        load_golden(_write_golden(tmp_path, [entry]))


def test_load_golden_null_answer_is_ignored(tmp_path):
    golden = load_golden(_write_golden(tmp_path, [{'query': 'q', 'relevant': [{'source_job_id': 'x'}], 'answer': None}]))
    assert golden[0].answer is None


# --- `eval` table + --json ---------------------------------------------------------

def test_cli_eval_table_has_precision_ndcg_and_skipped_line(monkeypatch, capsys):
    golden = load_golden(GOLDEN_EXAMPLE_PATH)
    monkeypatch.setattr('app.cli.get_search_fn', lambda: _perfect_search_fn_for(golden))

    exit_code = run_eval(golden_path=GOLDEN_EXAMPLE_PATH, ks=[3])

    assert exit_code == 0
    printed = capsys.readouterr().out
    for header in ('R@3', 'R@5', 'P@3', 'P@5', 'nDCG@3', 'nDCG@5', 'MRR', 'Hit@1'):
        assert header in printed
    assert 'skipped (unanswerable): 1' in printed


def test_cli_eval_writes_json_report(monkeypatch, tmp_path, capsys):
    import json

    golden = load_golden(GOLDEN_EXAMPLE_PATH)
    monkeypatch.setattr('app.cli.get_search_fn', lambda: _perfect_search_fn_for(golden))
    out = tmp_path / 'report.json'

    exit_code = run_eval(golden_path=GOLDEN_EXAMPLE_PATH, ks=[10], json_path=str(out))

    assert exit_code == 0
    report = json.loads(out.read_text(encoding='utf-8'))
    assert report['ks'] == [5, 10]
    aggregate = report['aggregate']
    assert aggregate['num_queries'] == 5
    assert aggregate['skipped_unanswerable'] == 1
    assert aggregate['recall_at_k'] == {'5': 1.0, '10': 1.0}
    assert aggregate['ndcg_at_k'] == {'5': pytest.approx(1.0), '10': pytest.approx(1.0)}
    assert set(aggregate['precision_at_k']) == {'5', '10'}
    assert aggregate['mrr'] == 1.0
    assert aggregate['hit_at_1'] == 1.0
    assert len(report['per_query']) == 5
    first = report['per_query'][0]
    assert set(first) == {'query', 'num_relevant', 'recall_at_k', 'precision_at_k', 'ndcg_at_k', 'mrr', 'hit_at_1'}
    assert first['query'] == golden[0].query


def test_cli_eval_json_written_even_when_gate_fails(monkeypatch, tmp_path):
    golden = load_golden(GOLDEN_EXAMPLE_PATH)
    monkeypatch.setattr('app.cli.get_search_fn', lambda: _perfect_search_fn_for(golden))
    out = tmp_path / 'report.json'

    exit_code = run_eval(golden_path=GOLDEN_EXAMPLE_PATH, min_recall=1.5, json_path=str(out))

    assert exit_code == 1
    assert out.exists()


def test_cli_eval_json_unwritable_returns_2(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.ERROR)
    golden = load_golden(GOLDEN_EXAMPLE_PATH)
    monkeypatch.setattr('app.cli.get_search_fn', lambda: _perfect_search_fn_for(golden))

    exit_code = run_eval(golden_path=GOLDEN_EXAMPLE_PATH, json_path=str(tmp_path / 'missing-dir' / 'r.json'))

    assert exit_code == 2
    assert any('failed to write JSON report' in record.message for record in caplog.records)


def test_cli_main_parses_json_flag(monkeypatch):
    from app import cli

    seen = {}

    def fake_run_eval(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(cli, 'run_eval', fake_run_eval)
    assert cli.main(['eval', '--golden', 'g.yaml', '--json', 'out.json']) == 0
    assert seen['json_path'] == 'out.json'
    assert cli.main(['eval', '--golden', 'g.yaml']) == 0
    assert seen['json_path'] is None
