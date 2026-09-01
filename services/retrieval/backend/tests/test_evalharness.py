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
    assert len(golden) == 5
    for item in golden:
        assert isinstance(item, GoldenQuery)
        assert item.query
        assert item.relevant
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
