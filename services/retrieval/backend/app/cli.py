"""Weave-Retrieval maintenance CLI.

Run from `backend/` as:

    python -m app.cli eval --golden GOLDEN.yaml [--k 5 --k 10] [--min-recall 0.5]
    python -m app.cli seed-demo

Two subcommands:

- `eval`: run a golden-set retrieval evaluation (see
  app/services/evalharness.py) against the live search pipeline
  (app/services/search.py, via app.services.evalharness.get_search_fn()) and
  print a Recall@k / MRR / Hit@1 table. Exits non-zero when the golden set
  fails to load or when the aggregate Recall@5 falls below --min-recall --
  safe to wire into a CI gate that should fail a build on a retrieval-
  quality regression.
- `seed-demo`: populate whatever DATABASE_URL points at with a small, fixed
  demo corpus matching backend/eval/golden.example.yaml's own judgments, so
  `eval --golden backend/eval/golden.example.yaml` has something real to
  query without needing a live Weave-Knowledge Postgres database -- see
  run_seed_demo()'s own docstring and README's "Evaluation" for the full
  workflow and why this is sqlite-only.
"""

import argparse
import logging
import sys

from app.services.evalharness import EvaluationResult, GoldenQuery, evaluate, get_search_fn, load_golden

logger = logging.getLogger(__name__)

# seed-demo's fixed corpus: one entry per backend/eval/golden.example.yaml
# `source_job_id`, each mapped to the chunk text(s) that document gets
# seeded with. Every text either equals or contains that golden entry's own
# `contains` text-marker verbatim (see that file's comments for the exact
# judgments) and, beyond the marker itself, deliberately echoes several of
# the matching query's own words too -- this run_seed_demo() docstring's
# "why real words, not lorem ipsum" note explains why that part matters as
# much as the marker substring does.
_DEMO_CORPUS: dict[str, list[str]] = {
    'b1a7e2c4-1111-4a3b-9c2d-000000000001': [
        'Die Kündigungsfrist beträgt während der Probezeit zwei Wochen ab Zugang der Kündigung.',
    ],
    'b1a7e2c4-2222-4a3b-9c2d-000000000002': [
        'Um Remote-Arbeit aus dem europäischen Ausland zu beantragen, stellen Sie einen '
        'Antrag im HR-Portal mindestens vier Wochen im Voraus.',
    ],
    'b1a7e2c4-3333-4a3b-9c2d-000000000003': [
        'Bei einer Dienstreise können Sie Fahrtkosten, Übernachtung und '
        'Verpflegungsmehraufwand als Spesen abrechnen.',
    ],
    'b1a7e2c4-4444-4a3b-9c2d-000000000004': [
        'Spesen für Dienstreisen werden über das Reisekostenformular abgerechnet und '
        'müssen innerhalb von 14 Tagen eingereicht werden.',
    ],
    'b1a7e2c4-5555-4a3b-9c2d-000000000005': [
        'Sie können Ihr VPN-Passwort im Self-Service-Portal unter Einstellungen '
        'zurücksetzen, sofern Sie Ihre Mitarbeiter-ID kennen.',
    ],
    'b1a7e2c4-6666-4a3b-9c2d-000000000006': [
        'Jede Mitarbeiterin und jeder Mitarbeiter hat Anspruch auf 30 Urlaubstage pro Kalenderjahr.',
        'Wie viele Urlaubstage Sie pro Jahr beantragen können, erklärt dieser Abschnitt. Der '
        'Urlaubsantrag im HR-Portal mindestens zwei Wochen im Voraus gestellt werden, damit '
        'die Genehmigung rechtzeitig erfolgt.',
    ],
}


def run_seed_demo() -> int:
    """Recreate the schema on whatever settings.database_url currently
    points at and populate it with `_DEMO_CORPUS` -- a small, fixed
    Document/Chunk corpus whose `source_job_id`s and chunk texts line up
    exactly with backend/eval/golden.example.yaml's own relevance
    judgments, so that golden set's Recall@5 comes out at 1.0 against a
    freshly seeded database (see tests/test_evalharness.py for the
    end-to-end proof).

    SQLite only, on purpose, and unconditionally: this drops and recreates
    EVERY table (`Base.metadata.drop_all` + `create_all`) before seeding,
    which Weave-Retrieval must NEVER do against the real, shared
    weave_knowledge Postgres database it otherwise only ever reads from
    read-only (see README's "Architektur-Entscheidung" and this service's
    complete absence of its own Alembic). Refuses outright (logs an error,
    returns 2) when settings.database_url isn't a sqlite URL, rather than
    trusting whoever's running this not to point a real deployment's
    DATABASE_URL at it by accident. This is a local dev/CI fixture command,
    not a migration tool -- it exists purely so `eval` has a database to
    query without a live Weave-Knowledge instance around.

    Why the seeded text echoes the matching query's own words, not just the
    bare "contains" marker: a golden query is matched against the ACTUAL
    ranked output of the real hybrid-search pipeline here, not looked up by
    id -- so a chunk purely composed of its required text-marker and
    nothing else can still rank behind unrelated chunks whenever the
    fake-embedding vector leg's essentially-random cosine similarity
    happens to favor one of those instead (there is no real semantic
    embedding in this offline demo). Giving every seeded chunk visible
    lexical overlap with the query that is supposed to find it keeps the
    fulltext leg's contribution to Reciprocal Rank Fusion decisive on top
    of that noise, which is what actually makes the resulting Recall@5
    deterministic rather than a coin flip.

    Every document is seeded already `status=DocumentStatus.INDEXED` (the
    one status apply_filters() ever lets through, see
    app/services/search.py) and embedded with app.services.embeddings.
    FakeEmbeddingProvider -- the same deterministic, dependency-free
    provider settings.embedding_provider defaults to, so a plain `eval`
    run right after `seed-demo` needs no real embedding/rerank credentials
    at all.
    """
    from datetime import datetime, timezone

    from app.core.config import settings
    from app.core.db import Base, SessionLocal, engine
    from app.models.models import Chunk, Document, DocumentStatus
    from app.services.embeddings import FakeEmbeddingProvider

    if not settings.database_url.startswith('sqlite'):
        logger.error(
            'seed-demo: refusing to run against a non-sqlite DATABASE_URL (%r) -- this '
            'command drops and recreates the entire schema, and Weave-Retrieval must '
            'never do that against the real, shared weave_knowledge database '
            "(see README's \"Architektur-Entscheidung\")",
            settings.database_url,
        )
        return 2

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    provider = FakeEmbeddingProvider()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        documents = {
            source_job_id: Document(
                source_job_id=source_job_id,
                content_sha256='0' * 64,
                engine='paddleocr',
                frontmatter={},
                tags=[],
                team='Demo',
                department='Demo',
                processed_at=now,
                status=DocumentStatus.INDEXED,
            )
            for source_job_id in _DEMO_CORPUS
        }
        db.add_all(documents.values())
        db.flush()  # populate Document.id (a Python-side default) for the Chunk FK below

        chunks = [
            Chunk(
                document_id=documents[source_job_id].id,
                chunk_index=chunk_index,
                text=text,
                heading_path=[],
                char_count=len(text),
                meta={'team': 'Demo', 'department': 'Demo'},
                embedding=provider.embed_query(text),
                embedding_model=provider.model_name,
            )
            for source_job_id, texts in _DEMO_CORPUS.items()
            for chunk_index, text in enumerate(texts)
        ]
        db.add_all(chunks)
        db.commit()
        document_count, chunk_count = len(documents), len(chunks)
    finally:
        db.close()

    logger.info(
        'seed-demo: seeded %d document(s) / %d chunk(s) into %r',
        document_count, chunk_count, settings.database_url,
    )
    return 0

# Recall@5 is always reported and always the metric --min-recall gates on,
# regardless of which --k values were requested -- so it's folded into the
# cutoff set unconditionally rather than requiring every caller to remember
# to pass `--k 5` themselves.
_GATE_K = 5
_DEFAULT_KS = [5, 10]


def _format_table(result: EvaluationResult, *, ks: list[int]) -> str:
    """Plain-text report: one row per golden query plus an AGGREGATE row, one
    column per requested Recall@k cutoff plus MRR and Hit@1. No external
    table-formatting dependency -- this CLI has exactly one subcommand and a
    handful of rows, so fixed-width str.format() columns are simpler than
    adding one.
    """
    recall_headers = [f'R@{k}' for k in ks]
    numeric_headers = [*recall_headers, 'MRR', 'Hit@1']
    # Every score is formatted as "%.3f" in [0.000, 1.000] -- always exactly
    # 5 characters -- so 5 is the floor a numeric column ever needs; only a
    # wider header (e.g. "R@100") pushes it past that.
    numeric_col_width = max(5, *(len(h) for h in numeric_headers))
    query_col_width = min(60, max(len('query'), len('AGGREGATE'), *(len(q.query) for q in result.per_query)))

    def _truncate(text: str) -> str:
        return text if len(text) <= query_col_width else text[: query_col_width - 1] + '…'

    def _row(label: str, recalls: dict[int, float], mrr: float, hit_at_1: float) -> str:
        cells = [_truncate(label).ljust(query_col_width)]
        cells += [f'{recalls[k]:.3f}'.rjust(numeric_col_width) for k in ks]
        cells += [f'{mrr:.3f}'.rjust(numeric_col_width), f'{hit_at_1:.3f}'.rjust(numeric_col_width)]
        return '  '.join(cells)

    header_cells = ['query'.ljust(query_col_width)] + [h.rjust(numeric_col_width) for h in numeric_headers]
    lines = ['  '.join(header_cells)]
    for query_metrics in result.per_query:
        lines.append(_row(query_metrics.query, query_metrics.recall_at_k, query_metrics.mrr, query_metrics.hit_at_1))
    lines.append('-' * len(lines[0]))
    lines.append(_row('AGGREGATE', result.recall_at_k, result.mrr, result.hit_at_1))
    return '\n'.join(lines)


def run_eval(*, golden_path: str, ks: list[int] | None = None, min_recall: float = 0.0) -> int:
    """Load `golden_path`, evaluate it against get_search_fn()'s search_fn
    (the real hybrid-search pipeline, run against whatever database
    settings.database_url / `DATABASE_URL` currently names -- see that
    function's own docstring, and `seed-demo` above for populating one),
    print the resulting table, and return an exit code: 0 on success, 1 if
    the aggregate Recall@5 is below `min_recall`, 2 if the golden set
    couldn't be loaded.
    """
    requested_ks = list(ks) if ks else list(_DEFAULT_KS)
    eval_ks = sorted(set(requested_ks) | {_GATE_K})

    try:
        golden: list[GoldenQuery] = load_golden(golden_path)
    except (OSError, ValueError) as exc:
        logger.error('eval: failed to load golden set %r: %s', golden_path, exc)
        return 2

    search_fn = get_search_fn()

    try:
        result = evaluate(search_fn, golden, ks=eval_ks)
    except ValueError as exc:
        logger.error('eval: %s', exc)
        return 2

    print(_format_table(result, ks=eval_ks))

    recall_at_gate = result.recall_at_k[_GATE_K]
    if recall_at_gate < min_recall:
        logger.error(
            'eval: aggregate Recall@%d %.4f is below --min-recall %.4f', _GATE_K, recall_at_gate, min_recall
        )
        return 1

    logger.info('eval: aggregate Recall@%d %.4f meets --min-recall %.4f', _GATE_K, recall_at_gate, min_recall)
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='app.cli', description='Weave-Retrieval maintenance CLI')
    subparsers = parser.add_subparsers(dest='command', required=True)

    eval_parser = subparsers.add_parser(
        'eval',
        help='Run a golden-set retrieval evaluation (Recall@k / MRR / Hit@1) against the search pipeline',
    )
    eval_parser.add_argument(
        '--golden', required=True,
        help='Path to a golden-set YAML file (see backend/eval/golden.example.yaml)',
    )
    eval_parser.add_argument(
        '--k', type=int, action='append', default=None,
        help=f'Recall@k cutoff to report; repeatable (default: {_DEFAULT_KS}). Recall@{_GATE_K} is always included.',
    )
    eval_parser.add_argument(
        '--min-recall', type=float, default=0.0,
        help='Exit non-zero if the aggregate Recall@%d falls below this value (default: 0.0, i.e. never fail)' % _GATE_K,
    )

    subparsers.add_parser(
        'seed-demo',
        help=(
            'Recreate the schema on DATABASE_URL (sqlite only) and seed a small fixed demo '
            'corpus matching backend/eval/golden.example.yaml, so `eval` has something real '
            'to query -- see run_seed_demo() and README\'s "Evaluation"'
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == 'eval':
        return run_eval(golden_path=args.golden, ks=args.k, min_recall=args.min_recall)
    if args.command == 'seed-demo':
        return run_seed_demo()

    parser.error(f'unknown command {args.command!r}')  # argparse exits itself here
    return 2  # pragma: no cover -- unreachable, parser.error() calls sys.exit


if __name__ == '__main__':
    sys.exit(main())
