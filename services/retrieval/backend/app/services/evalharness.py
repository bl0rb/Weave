"""Retrieval-quality evaluation harness: Recall@k / MRR / Hit@1 against a
hand-curated "golden set" of (query, expected-relevant-chunks) pairs.

`evaluate()` below takes the thing under test as a plain injected callable:

    search_fn: Callable[[str], list[RetrievedChunk]]

i.e. "run this query, give me back the ranked hits" with no other
dependency -- every test in tests/test_evalharness.py exercises evaluate()
with its own hand-written search_fn and never touches a database at all.
get_search_fn() below is the one place in this module that DOES import
app/services/search.py: it is what app/cli.py's `eval` subcommand calls to
obtain a search_fn backed by the real hybrid-search pipeline (against
whatever DB app/core/db.py's module-level SessionLocal is bound to --
settings.database_url, i.e. `DATABASE_URL`) rather than a hand-written stub.

Why RetrievedChunk carries `text`, not just an id
--------------------------------------------------
A golden-set author writes down which chunks answer a query *before* those
chunks exist as database rows with autoincrement `Chunk.id` values -- so a
golden entry can only meaningfully refer to a chunk by (a) which document it
came from (`Document.source_job_id`, stable and human-assignable) and,
optionally, (b) a short substring that must appear in the chunk's own text
(a "text marker" -- the task's "source+chunk-Textmarken" format). Matching
(b) against a live search result needs that result's actual text, not just
an opaque id -- hence `RetrievedChunk(source_job_id, text)` rather than a
bare id. A future search_fn only needs to expose these two fields per hit,
regardless of how many other columns (chunk_id, scores, ...) a real
SearchResult (app/schemas/search.py) also carries.

Golden-set YAML format (see backend/eval/golden.example.yaml)
---------------------------------------------------------------
    - query: "<the query text>"
      k: 10                      # optional; see GoldenQuery.k below
      relevant:
        - source_job_id: "<Document.source_job_id>"                 # whole document counts as relevant
        - source_job_id: "<Document.source_job_id>"
          contains: "<substring that must appear in the matching chunk's text>"
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    """One ranked hit as `search_fn` returns it -- see this module's
    docstring for why this carries `text` rather than a bare chunk id.
    `source_job_id` is `Document.source_job_id` (app/models/models.py) of
    the chunk's owning document, `text` is the chunk's own `Chunk.text`.
    """

    source_job_id: str
    text: str


SearchFn = Callable[[str], list[RetrievedChunk]]


@dataclass(frozen=True)
class GoldenRelevant:
    """One relevance judgment inside a GoldenQuery.relevant list.

    `source_job_id` alone marks the WHOLE document relevant: any retrieved
    chunk from that document satisfies this judgment (the "document
    source_job_id" half of the task's format). `contains`, when given,
    narrows that to only a chunk whose text contains this substring (case-
    insensitive) -- the "source+chunk-Textmarker" half.
    """

    source_job_id: str
    contains: str | None = None


@dataclass(frozen=True)
class GoldenQuery:
    """One golden-set entry: a query plus the chunks that should come back
    for it.

    `k`, when set, caps how many of search_fn(query)'s ranked hits are even
    considered for THIS query -- e.g. a golden entry only manually verified
    against the top 3 results should set `k: 3` so a coincidental hit at
    rank 7 can't inflate its recall. `None` (the default) means "consider up
    to max(ks)", i.e. no judgment-depth limit narrower than what `evaluate()`
    itself is asked to score. This is independent of `evaluate()`'s own `ks`
    parameter, which is the set of cutoffs Recall@k is reported at across
    every query in the set.
    """

    query: str
    relevant: list[GoldenRelevant]
    k: int | None = None


@dataclass(frozen=True)
class QueryMetrics:
    """Metrics for one GoldenQuery. `recall_at_k` has one entry per cutoff
    requested of evaluate()'s own `ks`. `mrr` and `hit_at_1` are computed
    against the query's full (GoldenQuery.k-capped) ranked result list, not
    against any single `ks` cutoff -- rank 1 is rank 1 regardless of which
    Recall@k values were requested."""

    query: str
    num_relevant: int
    recall_at_k: dict[int, float]
    mrr: float
    hit_at_1: float


@dataclass(frozen=True)
class EvaluationResult:
    """evaluate()'s return value: every query's own metrics plus the
    macro-average (mean over queries, each query weighted equally
    regardless of how many relevant chunks it has) aggregate."""

    per_query: list[QueryMetrics]
    recall_at_k: dict[int, float]
    mrr: float
    hit_at_1: float


def _matches(item: RetrievedChunk, relevant: GoldenRelevant) -> bool:
    if item.source_job_id != relevant.source_job_id:
        return False
    if relevant.contains is None:
        return True
    return relevant.contains.lower() in item.text.lower()


def _first_relevant_rank(retrieved: list[RetrievedChunk], relevant: list[GoldenRelevant]) -> int | None:
    for rank, item in enumerate(retrieved, start=1):
        if any(_matches(item, rel) for rel in relevant):
            return rank
    return None


def evaluate(search_fn: SearchFn, golden: list[GoldenQuery], ks: list[int] = [5, 10]) -> EvaluationResult:
    """Run every GoldenQuery's query through `search_fn` and score the
    results against its `relevant` judgments.

    Recall@k (for each `k` in `ks`) = (# distinct relevant judgments matched
    by at least one of the top-k results) / (# relevant judgments) for that
    query, macro-averaged across queries in the aggregate. MRR = 1 / rank of
    the first matching result (0.0 if none matched, within the query's own
    GoldenQuery.k-capped depth). Hit@1 = 1.0 if the single top-ranked result
    matches any judgment, else 0.0.

    Raises ValueError if `golden` is empty (nothing to evaluate) or if any
    GoldenQuery has an empty `relevant` list (a judgment-less query can't
    produce a meaningful recall denominator -- that's a golden-set authoring
    bug, not a 0.0 score).
    """
    if not golden:
        raise ValueError('golden set is empty -- nothing to evaluate')

    distinct_ks = sorted(set(ks))
    if not distinct_ks:
        raise ValueError('ks must contain at least one cutoff')
    default_depth = max(distinct_ks)

    per_query: list[QueryMetrics] = []
    for item in golden:
        if not item.relevant:
            raise ValueError(f'golden query {item.query!r} has an empty relevant list')

        depth = item.k if item.k is not None else default_depth
        retrieved = list(search_fn(item.query))[:depth]

        recall_at_k: dict[int, float] = {}
        for k in distinct_ks:
            top = retrieved[:k]
            hit_count = sum(1 for rel in item.relevant if any(_matches(r, rel) for r in top))
            recall_at_k[k] = hit_count / len(item.relevant)

        rank = _first_relevant_rank(retrieved, item.relevant)
        mrr = 1.0 / rank if rank is not None else 0.0
        hit_at_1 = 1.0 if retrieved and any(_matches(retrieved[0], rel) for rel in item.relevant) else 0.0

        per_query.append(
            QueryMetrics(
                query=item.query,
                num_relevant=len(item.relevant),
                recall_at_k=recall_at_k,
                mrr=mrr,
                hit_at_1=hit_at_1,
            )
        )

    n = len(per_query)
    aggregate_recall = {k: sum(q.recall_at_k[k] for q in per_query) / n for k in distinct_ks}
    aggregate_mrr = sum(q.mrr for q in per_query) / n
    aggregate_hit_at_1 = sum(q.hit_at_1 for q in per_query) / n

    return EvaluationResult(
        per_query=per_query,
        recall_at_k=aggregate_recall,
        mrr=aggregate_mrr,
        hit_at_1=aggregate_hit_at_1,
    )


# --- Golden-set YAML loading ------------------------------------------------

def _parse_relevant(raw: Any, *, query: str) -> GoldenRelevant:
    if not isinstance(raw, dict):
        raise ValueError(f'golden query {query!r} has a relevant entry that is not a mapping: {raw!r}')
    source_job_id = raw.get('source_job_id')
    if not isinstance(source_job_id, str) or not source_job_id:
        raise ValueError(f'golden query {query!r} has a relevant entry with a missing/invalid source_job_id: {raw!r}')
    contains = raw.get('contains')
    if contains is not None and not isinstance(contains, str):
        raise ValueError(f'golden query {query!r} has a relevant entry with a non-string contains: {raw!r}')
    return GoldenRelevant(source_job_id=source_job_id, contains=contains)


def _parse_query(raw: Any) -> GoldenQuery:
    if not isinstance(raw, dict):
        raise ValueError(f'golden set entry is not a mapping: {raw!r}')
    query = raw.get('query')
    if not isinstance(query, str) or not query:
        raise ValueError(f'golden set entry has a missing/invalid query: {raw!r}')
    relevant_raw = raw.get('relevant')
    if not isinstance(relevant_raw, list) or not relevant_raw:
        raise ValueError(f'golden query {query!r} has a missing/empty relevant list')
    relevant = [_parse_relevant(entry, query=query) for entry in relevant_raw]
    k = raw.get('k')
    if k is not None and (not isinstance(k, int) or isinstance(k, bool) or k < 1):
        raise ValueError(f'golden query {query!r} has an invalid k: {k!r} (expected a positive integer or omitted)')
    return GoldenQuery(query=query, relevant=relevant, k=k)


def load_golden(path: str | Path) -> list[GoldenQuery]:
    """Parse a golden-set YAML file (see this module's docstring for the
    format) into a list of GoldenQuery. Raises OSError if `path` can't be
    read and ValueError for any structurally invalid entry -- both are
    meant to be caught by a caller (app/cli.py's `eval` subcommand) that
    turns them into a clear, non-traceback error message.
    """
    text = Path(path).read_text(encoding='utf-8')
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f'{path}: not valid YAML: {exc}') from exc

    if not isinstance(data, list):
        raise ValueError(f'{path}: expected a YAML list of golden-set entries, got {type(data).__name__}')

    return [_parse_query(entry) for entry in data]


def get_search_fn() -> SearchFn:
    """Build the search_fn app/cli.py's `eval` subcommand runs the golden
    set against, backed by the REAL hybrid-search pipeline
    (app/services/search.py:search()) instead of a hand-written stub.

    Every import of app/services/search.py (and everything it in turn pulls
    in: the DB engine, the embedding provider, the reranker) happens INSIDE
    this function, not at module level -- see the module docstring for why
    the rest of this file (evaluate(), load_golden(), and every test in
    tests/test_evalharness.py) stays free of that dependency; it is only
    paid by a caller that actually calls get_search_fn() (app/cli.py's
    `eval` subcommand, or a test that calls this function directly).

    Runs each query against whatever database app/core/db.py's module-level
    `SessionLocal` is bound to at call time -- i.e. `settings.database_url` /
    the `DATABASE_URL` env var the process was started with. This service
    owns no schema and never migrates that database itself (see this
    module's own architecture-decision docs and README's "Architektur-
    Entscheidung"); populating it with a queryable corpus before running
    `eval` against a real database is the caller's job -- see README's
    "Evaluation" section for `app.cli seed-demo`, a small sqlite-only
    fixture command that exists for exactly this.

    Each call opens and closes its own Session (evaluate() calls search_fn
    once per golden query, not once for the whole run, so there is no
    single request/session lifetime to hook this into the way a FastAPI
    dependency would).

    Both `top_k` and `final_k` are set to settings.search_top_k (NOT left to
    their own defaults, where `final_k` would be the much smaller
    settings.search_final_k) -- this evaluates the pipeline's full
    `top_k`-deep candidate pool (still reranked, if a reranker is
    configured), not just the handful of results a production caller would
    actually see, so Recall@k for a `k` up to `search_top_k` is meaningful
    rather than silently capped at `search_final_k`.

    A search hit's `document_id` (a `Document.id` UUID, see
    app/schemas/search.py's SearchResult) is resolved back to that
    document's `source_job_id` via a fresh `db.get(Document, ...)` lookup --
    RetrievedChunk matches golden-set judgments by `source_job_id`
    (Weave-Ingest's stable, human-assignable job id), not by the database's
    own surrogate UUID primary key (see this module's own docstring for why).
    """

    def search_fn(query: str) -> list[RetrievedChunk]:
        import uuid

        from app.core.config import settings
        from app.core.db import SessionLocal
        from app.models.models import Document
        from app.schemas.search import SearchRequest
        from app.services.search import search as run_search

        db = SessionLocal()
        try:
            depth = settings.search_top_k
            request = SearchRequest(query=query, top_k=depth, final_k=depth)
            results, _trace = run_search(db, request)

            retrieved: list[RetrievedChunk] = []
            for result in results:
                document = db.get(Document, uuid.UUID(result.document_id))
                # A result's owning Document row was just read inside
                # run_search() itself (see search()'s own docstring on why
                # this re-lookup is an identity-map hit, not a fresh
                # SELECT) -- it cannot have vanished by the time we get here.
                assert document is not None
                retrieved.append(RetrievedChunk(source_job_id=document.source_job_id, text=result.text))
            return retrieved
        finally:
            db.close()

    return search_fn
