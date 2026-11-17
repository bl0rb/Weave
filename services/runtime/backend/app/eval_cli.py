"""Weave-Runtime evaluation CLI.

Run from `backend/` as:

    python -m app.eval_cli answers --golden GOLDEN.yaml --bot BOT_ID \\
        [--endpoint NAME ...] [--judge-endpoint NAME] [--team T ...] [--user SUBJECT] \\
        [--json OUT.json] [--markdown OUT.md] [--min-correctness X]

`answers` runs the golden set through the real chat pipeline in-process
(app/services/answer_eval.py, `handle_chat`) once per `--endpoint` (a
central LLM endpoint of Weave-Ingest's chat-provider configuration; the
bot's default model when none is given) and prints a comparison table --
rows grouped Suche / Fachliche Richtigkeit / Quellenbezug / Abstention /
Betrieb, one column per model. It talks to the same services a live turn
does (Weave-Retrieval, the chat-provider control plane, the LLM), configured
by the usual environment variables (RETRIEVAL_BASE_URL, CHAT_CONFIG_BASE_URL,
...). `--team`/`--user` set the caller's teams and Weave-Ingest subject, i.e.
which knowledge spaces the evaluation may read.

`--judge-endpoint` adds the LLM judge (`judge_correctness`, judge-based
`faithfulness`), built like `/internal/conversation-title`:
`fetch_chat_provider(NAME)` + `OpenAICompatibleLLM`.

Exit codes: 0 ok; 1 when the first model's mean `key_fact_recall` is below
`--min-correctness`; 2 for load/config errors (unreadable or invalid golden
set, unknown bot, missing permission, endpoint not offered by the bot or
without effect -- standalone mode without CHAT_CONFIG_BASE_URL, n8n bot,
disabled endpoint --, chat-provider control plane unreachable before the
first turn, unavailable judge, `--min-correctness` without any
`must_include` facts). A turn that fails during the run is listed as an
error and scored as a wrong answer, so it lowers the `--min-correctness`
mean instead of dropping out of it.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from app.schemas.chat import ChatUser
from app.services import answer_eval
from app.services import llm as llm_service
from app.services.chat_config_client import ChatConfigUnavailable, fetch_chat_provider

logger = logging.getLogger(__name__)


def build_judge(endpoint: str) -> answer_eval.LLMJudge:
    """The judge for `--judge-endpoint`; raises ChatConfigUnavailable when
    the control plane is not configured or the endpoint is disabled."""
    snapshot = fetch_chat_provider(endpoint)
    if snapshot is None:
        raise ChatConfigUnavailable('Zentrale Chat-Konfiguration ist nicht eingerichtet (CHAT_CONFIG_BASE_URL).')
    if not snapshot.enabled:
        raise ChatConfigUnavailable(f'LLM-Endpunkt {endpoint!r} ist deaktiviert.')
    provider = llm_service.OpenAICompatibleLLM(
        base_url=snapshot.base_url, api_key=snapshot.api_key, model=snapshot.model,
        timeout=snapshot.timeout_seconds,
    )
    return answer_eval.LLMJudge(provider, model=snapshot.model, label=endpoint)


def _caller(teams: list[str] | None, subject: str | None) -> ChatUser:
    teams = [team for team in (teams or []) if team]
    return ChatUser(team=teams[0] if teams else None, teams=teams or None, subject=subject or None)


def run_answers(args: argparse.Namespace) -> int:
    try:
        items = answer_eval.load_golden(args.golden)
    except (OSError, ValueError) as exc:
        print(f'error: could not load golden set: {exc}', file=sys.stderr)
        return 2

    if args.min_correctness is not None:
        if not 0.0 <= args.min_correctness <= 1.0:
            print('error: --min-correctness must be between 0 and 1', file=sys.stderr)
            return 2
        if not any(item.answer is not None and item.answer.must_include for item in items):
            print('error: --min-correctness needs golden entries with answer.must_include', file=sys.stderr)
            return 2

    judge = None
    if args.judge_endpoint:
        try:
            judge = build_judge(args.judge_endpoint)
        except ChatConfigUnavailable as exc:
            print(f'error: judge unavailable: {exc}', file=sys.stderr)
            return 2

    try:
        evaluation = answer_eval.evaluate_answers(
            items, bot_id=args.bot, endpoints=args.endpoint, user=_caller(args.team, args.user), judge=judge,
        )
    except answer_eval.FATAL_ERRORS as exc:
        print(f'error: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2

    print(answer_eval.format_text_report(evaluation))
    try:
        if args.json:
            Path(args.json).write_text(
                json.dumps(answer_eval.to_json(evaluation), ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
            )
        if args.markdown:
            Path(args.markdown).write_text(answer_eval.format_markdown_report(evaluation), encoding='utf-8')
    except OSError as exc:
        print(f'error: could not write report: {exc}', file=sys.stderr)
        return 2

    if args.min_correctness is not None:
        first = evaluation.summaries[0]
        recall = first.metrics['key_fact_recall']
        if recall is None or recall < args.min_correctness:
            print(
                f'FAIL: mean key_fact_recall of {first.model!r} is {answer_eval.format_value(recall)}, '
                f'below --min-correctness {args.min_correctness:.3f}',
                file=sys.stderr,
            )
            return 1
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m app.eval_cli', description='Weave-Runtime evaluation CLI')
    subparsers = parser.add_subparsers(dest='command', required=True)
    answers = subparsers.add_parser('answers', help='evaluate answer quality against a golden set')
    answers.add_argument('--golden', required=True, help='golden-set YAML (same format as Weave-Retrieval eval)')
    answers.add_argument('--bot', required=True, help='bot id to evaluate')
    answers.add_argument(
        '--endpoint', action='append', default=None, metavar='NAME',
        help='central LLM endpoint to compare (repeatable); default: the bot\'s own model',
    )
    answers.add_argument('--judge-endpoint', default=None, metavar='NAME', help='central LLM endpoint for the judge')
    answers.add_argument('--team', action='append', default=None, metavar='T', help='caller team (repeatable)')
    answers.add_argument('--user', default=None, metavar='SUBJECT', help='caller Weave-Ingest user id')
    answers.add_argument('--json', default=None, metavar='OUT', help='write the full result as JSON')
    answers.add_argument('--markdown', default=None, metavar='OUT', help='write the comparison as Markdown')
    answers.add_argument(
        '--min-correctness', type=float, default=None, metavar='X',
        help='exit 1 when the first model\'s mean key_fact_recall is below X (0..1)',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')
    args = _build_arg_parser().parse_args(argv)
    if args.command == 'answers':
        return run_answers(args)
    return 2  # pragma: no cover -- argparse rejects unknown commands


if __name__ == '__main__':
    sys.exit(main())
