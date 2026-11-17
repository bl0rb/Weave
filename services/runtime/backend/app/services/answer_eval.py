"""Answer-quality evaluation: runs a golden set through the REAL chat
pipeline (`app/services/chat.py`'s `handle_chat`, in-process) once per
model and scores every answer -- the answer-level counterpart of
Weave-Retrieval's retrieval harness (app/services/evalharness.py in that
service), driven by `python -m app.eval_cli answers` (app/eval_cli.py).

Golden set
----------
The SAME YAML file Weave-Retrieval's `python -m app.cli eval` reads
(services/retrieval/backend/eval/golden.example.yaml), loaded here by this
module's own, more tolerant `load_golden` (only `query`, `relevant` and
`answer` are read; unknown keys such as `k` or `relevant[].contains` are
ignored). The optional `answer:` block is the shared answer schema:

    answer:
      answerable: true            # default true; false = the bot should abstain
      reference: "..."            # optional reference answer
      must_include:               # optional key facts; item = string or list of alternatives
        - "30 Tage"
        - ["Formular A", "Formblatt A"]
      must_not_include: ["..."]   # optional
      must_cite: ["<source_job_id>"]  # optional; default = relevant[].source_job_id

An entry without `answer:` is scored for retrieval and grounding only.

What a run records
------------------
`evaluate_answers` calls `handle_chat(ChatRequest(..., llm_endpoint=...))`
for every (model, query) pair. While a turn runs, a recorder scoped by a
`contextvars.ContextVar` (`_ACTIVE`) captures every chunk
`retrieval_client.search` returned (direct RAG and agent-mode searches
alike) and the token usage of every `FakeLLM`/`OpenAICompatibleLLM`
`chat`/`chat_with_tools` call. The wrappers are installed only for the
duration of `evaluate_answers` (`_recorders_installed`) and pass straight
through whenever no recording is active -- outside an evaluation nothing
changes. Latency is wall-clock time around `handle_chat`.

Metrics (each `None` when not applicable; `None` is excluded from means)
-------------------------------------------------------------------------
"Expected documents" are `answer.must_cite`, else `relevant[].source_job_id`.

* Suche / retrieval
  - `context_recall`: share of expected documents among the retrieved hits.
  - `context_precision`: share of the answer's sources (`ChatResponse.
    sources`, chunk sources only) whose `source_job_id` is expected.
  - `mrr`: 1/rank of the first retrieved hit from an expected document
    (0 when none).
* Fachliche Richtigkeit / correctness
  - `key_fact_recall`: share of `must_include` items found in the answer
    (normalized, case-insensitive, whole words, any alternative).
  - `forbidden_rate`: share of `must_not_include` items found (lower is better).
  - `token_f1`: SQuAD-style token F1 against `reference`.
  - `judge_correctness`: LLM judge score 0..1 (only with a judge).
* Quellenbezug / grounding (only when the bot answered with sources)
  - `citation_coverage`: share of answer sentences carrying a `[N]`
    citation (ALCE citation-recall proxy).
  - `citation_precision`: share of cited numbers resolving to a source
    from an expected document. Citations of knowledge-memory sources
    (`memory_kind` set, no `source_job_id`) are left out, as in
    `context_precision`.
  - `faithfulness`: lexical -- share of answer sentences whose content
    tokens are >= 60 % covered by at least one chunk the LLM received; with
    a judge instead the RAGAS-style claim support ratio (judge failure ->
    None). The chunks are those `trace.retrieval.hits` marks `used` (direct
    RAG, after `min_score`), else every recorded hit (agent mode); chunks
    behind `ChatResponse.sources` come first, so the judge's
    `_JUDGE_CONTEXT_CHUNKS` cap never drops a cited chunk before an uncited one.
* Abstention
  - `abstention_accuracy`: for `answerable: false` 1 when the bot abstained
    (guard triggered or no sources), for `answerable: true` 1 when it did
    not. Only for entries with an `answer:` block.

A turn that raised (any per-turn exception, see `evaluate_answers`) counts
as a failed answer: `key_fact_recall`, `token_f1`, `judge_correctness` and
`abstention_accuracy` are 0.0 wherever the entry makes them applicable, so
errors pull the means (and the `--min-correctness` gate) down instead of
dropping out. Retrieval and grounding metrics stay None (nothing to score).

The aggregate per model is the macro mean over queries.
"""

import json
import logging
import math
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.schemas.chat import ChatRequest, ChatResponse, ChatUser, Source
from app.services import llm as llm_service
from app.services import retrieval_client
from app.services.botconfig import BotConfigError, BotNotFoundError
from app.services.chat import _CITATION_RE, BotPermissionDenied
from app.services.chat_config_client import ChatConfigUnavailable
from app.services.retrieval_client import RetrievedChunk

logger = logging.getLogger(__name__)

# (group key, report heading, metric names) -- the order every report uses.
METRIC_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ('retrieval', 'Suche (Retrieval)', ('context_recall', 'context_precision', 'mrr')),
    ('correctness', 'Fachliche Richtigkeit', ('key_fact_recall', 'forbidden_rate', 'token_f1', 'judge_correctness')),
    ('grounding', 'Quellenbezug (Grounding)', ('citation_coverage', 'citation_precision', 'faithfulness')),
    ('abstention', 'Abstention', ('abstention_accuracy',)),
)
METRIC_NAMES: tuple[str, ...] = tuple(name for _, _, names in METRIC_GROUPS for name in names)



class EvalConfigError(Exception):
    """A configuration problem `preflight` finds before the first turn: an
    `--endpoint` that would not take effect (standalone mode without the
    central chat-provider configuration, an n8n bot, a disabled endpoint)
    or a chat-provider control plane that cannot be reached at all."""


# Exceptions that fail every turn the same way (unknown or invalid bot,
# BOTS_DIR missing, no permission, endpoint not offered, `preflight`
# failure): a configuration error that aborts the run instead of being
# recorded per query. `ChatConfigUnavailable` is deliberately NOT here:
# `preflight` resolves every endpoint once up front (and turns a failure
# there into EvalConfigError); during the run it can be transient (a 503 of
# the control plane) and is recorded on that query like any other error.
FATAL_ERRORS = (BotNotFoundError, BotConfigError, FileNotFoundError, BotPermissionDenied, EvalConfigError)

FAITHFULNESS_COVERAGE = 0.6
DEFAULT_MODEL_LABEL = 'default'


# --- golden set ----------------------------------------------------------------


@dataclass(frozen=True)
class AnswerSpec:
    answerable: bool = True
    reference: str | None = None
    # Each key fact is a list of alternatives; one match is enough.
    must_include: list[list[str]] = field(default_factory=list)
    must_not_include: list[str] = field(default_factory=list)
    must_cite: list[str] | None = None


@dataclass(frozen=True)
class GoldenItem:
    query: str
    relevant: list[str] = field(default_factory=list)  # source_job_ids
    answer: AnswerSpec | None = None

    @property
    def expected_documents(self) -> list[str]:
        if self.answer is not None and self.answer.must_cite is not None:
            return self.answer.must_cite
        return self.relevant


def _string_list(value: Any, *, what: str, query: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f'golden query {query!r}: {what} must be a string or a list of strings')
    return [item for item in value if item.strip()]


def _parse_answer(raw: Any, *, query: str) -> AnswerSpec:
    if not isinstance(raw, dict):
        raise ValueError(f'golden query {query!r} has an answer block that is not a mapping: {raw!r}')
    answerable = raw.get('answerable', True)
    if not isinstance(answerable, bool):
        raise ValueError(f'golden query {query!r} has a non-boolean answer.answerable: {answerable!r}')
    reference = raw.get('reference')
    if reference is not None and not isinstance(reference, str):
        raise ValueError(f'golden query {query!r} has a non-string answer.reference')
    must_include_raw = raw.get('must_include') or []
    if not isinstance(must_include_raw, list):
        raise ValueError(f'golden query {query!r}: answer.must_include must be a list')
    must_include = [
        alternatives
        for item in must_include_raw
        if (alternatives := _string_list(item, what='answer.must_include item', query=query))
    ]
    must_cite = raw.get('must_cite')
    return AnswerSpec(
        answerable=answerable,
        reference=reference.strip() or None if isinstance(reference, str) else None,
        must_include=must_include,
        must_not_include=_string_list(raw.get('must_not_include'), what='answer.must_not_include', query=query),
        must_cite=None if must_cite is None else _string_list(must_cite, what='answer.must_cite', query=query),
    )


def _parse_item(raw: Any) -> GoldenItem:
    if not isinstance(raw, dict):
        raise ValueError(f'golden set entry is not a mapping: {raw!r}')
    query = raw.get('query')
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f'golden set entry has a missing/invalid query: {raw!r}')
    relevant: list[str] = []
    relevant_raw = raw.get('relevant')
    if isinstance(relevant_raw, list):
        for entry in relevant_raw:
            # Tolerant: a judgment without a usable source_job_id is skipped.
            job_id = entry.get('source_job_id') if isinstance(entry, dict) else None
            if isinstance(job_id, str) and job_id and job_id not in relevant:
                relevant.append(job_id)
    answer = _parse_answer(raw['answer'], query=query) if raw.get('answer') is not None else None
    return GoldenItem(query=query.strip(), relevant=relevant, answer=answer)


def load_golden(path: str | Path) -> list[GoldenItem]:
    """Parse the golden YAML (see this module's docstring). Raises OSError
    when `path` cannot be read and ValueError for invalid YAML, a
    non-list document, an empty set or a structurally broken entry --
    app/eval_cli.py turns both into exit code 2."""
    text = Path(path).read_text(encoding='utf-8')
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f'{path}: not valid YAML: {exc}') from exc
    if not isinstance(data, list):
        raise ValueError(f'{path}: expected a YAML list of golden-set entries, got {type(data).__name__}')
    items = [_parse_item(entry) for entry in data]
    if not items:
        raise ValueError(f'{path}: the golden set is empty')
    return items


# --- recorder --------------------------------------------------------------------


@dataclass
class TurnRecording:
    """What one turn did, captured by the wrappers in `_recorders_installed`."""

    hits: list[RetrievedChunk] = field(default_factory=list)
    searches: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_reported: bool = False


_ACTIVE: ContextVar[TurnRecording | None] = ContextVar('answer_eval_recording', default=None)
# Set while a wrapped LLM call runs, so a provider method calling another
# wrapped method (FakeLLM.chat_with_tools -> chat) is counted once.
_IN_LLM_CALL: ContextVar[bool] = ContextVar('answer_eval_in_llm_call', default=False)


def _recording_search(original: Callable[..., Any]) -> Callable[..., Any]:
    def search(*args, **kwargs):
        hits = original(*args, **kwargs)
        recording = _ACTIVE.get()
        if recording is not None:
            recording.searches += 1
            recording.hits.extend(hits)
        return hits

    return search


def _recording_llm_method(original: Callable[..., Any]) -> Callable[..., Any]:
    def method(self, *args, **kwargs):
        recording = _ACTIVE.get()
        if recording is None or _IN_LLM_CALL.get():
            return original(self, *args, **kwargs)
        token = _IN_LLM_CALL.set(True)
        try:
            result = original(self, *args, **kwargs)
        finally:
            _IN_LLM_CALL.reset(token)
        recording.llm_calls += 1
        usage = getattr(result, 'usage', None)
        if usage is not None:
            recording.usage_reported = True
            recording.prompt_tokens += usage.prompt_tokens
            recording.completion_tokens += usage.completion_tokens
        return result

    return method


@contextmanager
def _recorders_installed() -> Iterator[None]:
    """Install the recording wrappers for the duration of an evaluation and
    restore the exact previous attributes afterwards. The wrappers record
    only while `_ACTIVE` holds a recording; otherwise they pass through."""
    targets: list[tuple[Any, str]] = [(retrieval_client, 'search')]
    for cls in (llm_service.FakeLLM, llm_service.OpenAICompatibleLLM):
        targets.extend((cls, name) for name in ('chat', 'chat_with_tools') if name in vars(cls))
    saved = [(owner, name, vars(owner)[name]) for owner, name in targets]
    try:
        for owner, name, original in saved:
            wrap = _recording_search if owner is retrieval_client else _recording_llm_method
            setattr(owner, name, wrap(original))
        yield
    finally:
        for owner, name, original in saved:
            setattr(owner, name, original)


# --- text helpers ------------------------------------------------------------------

_CITATION_MARKER_RE = _CITATION_RE  # chat.py's own `[N]`/`[source N]`/`[1, 2]` syntax
# Citations written after the sentence's punctuation ("... gilt. [1]") are
# moved in front of it so they stay with their sentence when splitting.
_TRAILING_CITATIONS_RE = re.compile(r'([.!?])((?:\s*' + _CITATION_RE.pattern + r')+)', re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+|\n+')
_LIST_MARKER_RE = re.compile(r'^\s*(?:[-*•>]+|#+|\d+[.)])\s+')
_ARTICLES = frozenset(
    'a an the der die das den dem des ein eine einen einem einer eines'.split()
)
_STOPWORDS = _ARTICLES | frozenset(
    """
    and or but if of to in on at by for with from as is are was were be been it this that these those
    not no can may will would should which who what when where how than then there their its into
    und oder aber wenn von vom zu zum zur im in am an auf bei mit aus als ist sind war waren sein wird
    werden wurde kann können darf dürfen muss müssen soll sollen nicht kein keine es sie er ich wir ihr
    du man sich dass für über unter nach vor durch bis auch noch nur so wie was wer wo dies diese dieser
    dieses hat haben hatte sowie bzw ggf z b
    """.split()
)


def _fold(text: str) -> str:
    return unicodedata.normalize('NFKC', text).casefold()


def normalize_text(text: str) -> str:
    """Casefolded text without citation markers and punctuation, single
    spaces -- the form key facts and answers are compared in."""
    text = _CITATION_MARKER_RE.sub(' ', text or '')
    text = re.sub(r'[^\w\s]', ' ', _fold(text))
    return ' '.join(text.split())


def contains_fact(normalized_answer: str, fact: str) -> bool:
    needle = normalize_text(fact)
    return bool(needle) and f' {needle} ' in f' {normalized_answer} '


def _content_tokens(text: str) -> list[str]:
    return [
        token for token in normalize_text(text).split()
        if token not in _STOPWORDS and (len(token) > 1 or token.isdigit())
    ]


def token_f1(prediction: str, reference: str) -> float:
    """SQuAD-style token F1 (normalized, articles dropped; de+en articles)."""
    pred = [t for t in normalize_text(prediction).split() if t not in _ARTICLES]
    ref = [t for t in normalize_text(reference).split() if t not in _ARTICLES]
    if not pred or not ref:
        return float(pred == ref)
    common = sum((Counter(pred) & Counter(ref)).values())
    if common == 0:
        return 0.0
    precision, recall = common / len(pred), common / len(ref)
    return 2 * precision * recall / (precision + recall)


def split_sentences(answer: str) -> list[str]:
    """Answer sentences worth scoring: split on sentence punctuation and
    line breaks, list/heading markers stripped, fewer than three words dropped."""
    text = _TRAILING_CITATIONS_RE.sub(lambda m: f'{m.group(2)}{m.group(1)}', answer or '')
    sentences = []
    for part in _SENTENCE_SPLIT_RE.split(text):
        part = _LIST_MARKER_RE.sub('', part).strip()
        if len(normalize_text(part).split()) >= 3:
            sentences.append(part)
    return sentences


def cited_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    for group in _CITATION_MARKER_RE.findall(text or ''):
        for number in re.findall(r'\d+', group):
            if int(number) not in numbers:
                numbers.append(int(number))
    return numbers


def _source_for(number: int, sources: list[Source]) -> Source | None:
    if any(source.ref is not None for source in sources):
        return next((source for source in sources if source.ref == number), None)
    return sources[number - 1] if 1 <= number <= len(sources) else None


def _dedupe_hits(hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
    seen: set[tuple[str, int]] = set()
    unique = []
    for hit in hits:
        key = (hit.document_id, hit.chunk_id)
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return unique


# --- metrics -----------------------------------------------------------------------


def retrieval_metrics(item: GoldenItem, hits: list[RetrievedChunk], sources: list[Source]) -> dict[str, float | None]:
    expected = set(item.expected_documents)
    if not expected:
        return {'context_recall': None, 'context_precision': None, 'mrr': None}
    retrieved_ids = [getattr(hit, 'source_job_id', None) for hit in hits]
    recall = len(expected & set(retrieved_ids)) / len(expected)
    mrr = next((1.0 / rank for rank, job_id in enumerate(retrieved_ids, start=1) if job_id in expected), 0.0)
    chunk_sources = [source for source in sources if source.memory_kind is None]
    precision = (
        sum(1 for source in chunk_sources if source.source_job_id in expected) / len(chunk_sources)
        if chunk_sources else None
    )
    return {'context_recall': recall, 'context_precision': precision, 'mrr': mrr}


def correctness_metrics(item: GoldenItem, answer: str) -> dict[str, float | None]:
    spec = item.answer
    metrics: dict[str, float | None] = {'key_fact_recall': None, 'forbidden_rate': None, 'token_f1': None}
    if spec is None:
        return metrics
    normalized = normalize_text(answer)
    if spec.must_include:
        found = sum(1 for alternatives in spec.must_include if any(contains_fact(normalized, alt) for alt in alternatives))
        metrics['key_fact_recall'] = found / len(spec.must_include)
    if spec.must_not_include:
        hits = sum(1 for fact in spec.must_not_include if contains_fact(normalized, fact))
        metrics['forbidden_rate'] = hits / len(spec.must_not_include)
    if spec.reference:
        metrics['token_f1'] = token_f1(answer, spec.reference)
    return metrics


def citation_metrics(item: GoldenItem, answer: str, sources: list[Source]) -> dict[str, float | None]:
    sentences = split_sentences(answer)
    coverage = (
        sum(1 for sentence in sentences if cited_numbers(sentence)) / len(sentences) if sentences else None
    )
    expected = set(item.expected_documents)
    numbers = cited_numbers(answer)
    precision = None
    if numbers and expected:
        resolved = [_source_for(number, sources) for number in numbers]
        # A knowledge-memory source has no source_job_id: neither right nor wrong.
        scored = [source for source in resolved if source is None or source.memory_kind is None]
        if scored:
            precision = sum(1 for source in scored if source is not None and source.source_job_id in expected) / len(scored)
    return {'citation_coverage': coverage, 'citation_precision': precision}


def lexical_faithfulness(answer: str, contexts: Sequence[str]) -> float | None:
    """Share of answer sentences whose content tokens are covered to at
    least `FAITHFULNESS_COVERAGE` by a single retrieved chunk."""
    context_tokens = [set(_content_tokens(text)) for text in contexts if text]
    if not context_tokens:
        return None
    scored = 0
    supported = 0
    for sentence in split_sentences(answer):
        tokens = set(_content_tokens(sentence))
        if not tokens:
            continue
        scored += 1
        if any(len(tokens & chunk) / len(tokens) >= FAITHFULNESS_COVERAGE for chunk in context_tokens):
            supported += 1
    return supported / scored if scored else None


def _chunk_key(item: Any) -> tuple[str, int]:
    return (item.document_id, item.chunk_id)


def faithfulness_contexts(response: ChatResponse, hits: list[RetrievedChunk]) -> list[str]:
    """Texts of the chunks the LLM actually received (see this module's
    docstring, `faithfulness`), cited chunks first."""
    retrieval = response.trace.retrieval
    trace_hits = retrieval.hits if retrieval is not None else []
    if trace_hits:
        used = {_chunk_key(hit) for hit in trace_hits if hit.used}
        hits = [hit for hit in hits if _chunk_key(hit) in used]
    cited = {_chunk_key(source) for source in response.sources if source.memory_kind is None}
    ordered = [hit for hit in hits if _chunk_key(hit) in cited] + [hit for hit in hits if _chunk_key(hit) not in cited]
    return [hit.text for hit in ordered]


def is_abstention(response: ChatResponse) -> bool:
    guard = response.trace.guard
    return bool(guard is not None and guard.triggered) or not response.sources


# --- LLM judge -----------------------------------------------------------------------

_JUDGE_CORRECTNESS_SYSTEM = (
    'Du bewertest die fachliche Richtigkeit einer Antwort. Vergleiche sie mit der Referenzantwort und den '
    'Kernfakten. Fehlende Kernfakten und falsche Aussagen senken die Bewertung, Formulierung und Länge nicht. '
    'Antworte ausschließlich mit JSON: {"score": <Zahl von 0 bis 1>, "reason": "<ein Satz>"}.'
)
_JUDGE_FAITHFULNESS_SYSTEM = (
    'Du prüfst, ob eine Antwort durch den gegebenen Kontext belegt ist. Zerlege die Antwort in einzelne '
    'Tatsachenbehauptungen und entscheide für jede, ob der Kontext sie stützt. Höflichkeitsfloskeln und '
    'Rückfragen sind keine Behauptungen. Antworte ausschließlich mit JSON: '
    '{"claims": [{"claim": "<Behauptung>", "supported": true|false}]}.'
)
_JUDGE_CONTEXT_CHARS = 1500
_JUDGE_CONTEXT_CHUNKS = 8


def parse_judge_json(text: str) -> dict | None:
    """The JSON object in a judge reply (code fences tolerated), else None."""
    text = (text or '').strip()
    fenced = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class LLMJudge:
    """LLM-as-a-judge on any `LLMProvider` (app/eval_cli.py builds it like
    `/internal/conversation-title`: `fetch_chat_provider` +
    `OpenAICompatibleLLM`). Every method returns None when the call fails or
    the reply is not the requested strict JSON."""

    def __init__(self, provider: llm_service.LLMProvider, *, model: str | None = None, label: str = 'judge') -> None:
        self._provider = provider
        self._model = model
        self.label = label

    def _ask(self, system: str, user: str) -> dict | None:
        try:
            result = self._provider.chat(
                [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                model=self._model, temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001 -- a judge failure only voids the metric
            logger.warning('judge call failed: %s', exc)
            return None
        return parse_judge_json(result.content)

    def correctness(self, question: str, answer: str, spec: AnswerSpec) -> float | None:
        facts = '\n'.join(f'- {" / ".join(alternatives)}' for alternatives in spec.must_include) or '(keine)'
        user = (
            f'Frage:\n{question}\n\nReferenzantwort:\n{spec.reference or "(keine)"}\n\n'
            f'Kernfakten:\n{facts}\n\nZu bewertende Antwort:\n{answer}'
        )
        data = self._ask(_JUDGE_CORRECTNESS_SYSTEM, user)
        score = data.get('score') if data else None
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0.0 <= float(score) <= 1.0:
            return None
        return float(score)

    def faithfulness(self, answer: str, contexts: Sequence[str]) -> float | None:
        if not contexts:
            return None
        context = '\n\n'.join(
            f'[{index}] {text[:_JUDGE_CONTEXT_CHARS]}'
            for index, text in enumerate(contexts[:_JUDGE_CONTEXT_CHUNKS], start=1)
        )
        data = self._ask(_JUDGE_FAITHFULNESS_SYSTEM, f'Kontext:\n{context}\n\nAntwort:\n{answer}')
        claims = data.get('claims') if data else None
        if not isinstance(claims, list) or not claims:
            return None
        verdicts = [claim.get('supported') for claim in claims if isinstance(claim, dict)]
        if len(verdicts) != len(claims) or not all(isinstance(verdict, bool) for verdict in verdicts):
            return None
        return sum(verdicts) / len(verdicts)


# --- evaluation run --------------------------------------------------------------------


@dataclass
class QueryResult:
    model: str
    query: str
    answer: str = ''
    error: str | None = None
    abstained: bool | None = None
    guard_reason: str | None = None
    answer_model: str | None = None
    sources: list[str | None] = field(default_factory=list)  # source_job_id per returned source
    retrieved: list[str | None] = field(default_factory=list)  # source_job_id per unique retrieved hit
    searches: int = 0
    latency_ms: float = 0.0
    llm_calls: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    faithfulness_method: str | None = None
    metrics: dict[str, float | None] = field(default_factory=lambda: dict.fromkeys(METRIC_NAMES))


@dataclass
class ModelSummary:
    model: str
    queries: int
    errors: int
    metrics: dict[str, float | None]
    counts: dict[str, int]
    latency_ms_mean: float | None
    latency_ms_p95: float | None
    prompt_tokens_mean: float | None
    completion_tokens_mean: float | None


@dataclass
class AnswerEvaluation:
    bot_id: str
    models: list[str]
    judge: str | None
    summaries: list[ModelSummary]
    results: list[QueryResult]

    def summary_for(self, model: str) -> ModelSummary:
        return next(summary for summary in self.summaries if summary.model == model)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def summarize(model: str, results: list[QueryResult]) -> ModelSummary:
    metrics: dict[str, float | None] = {}
    counts: dict[str, int] = {}
    for name in METRIC_NAMES:
        values = [r.metrics[name] for r in results if r.metrics.get(name) is not None]
        metrics[name] = _mean(values)
        counts[name] = len(values)
    ok = [r for r in results if r.error is None]
    return ModelSummary(
        model=model,
        queries=len(results),
        errors=len(results) - len(ok),
        metrics=metrics,
        counts=counts,
        latency_ms_mean=_mean([r.latency_ms for r in ok]),
        latency_ms_p95=_p95([r.latency_ms for r in ok]),
        prompt_tokens_mean=_mean([r.prompt_tokens for r in ok if r.prompt_tokens is not None]),
        completion_tokens_mean=_mean([r.completion_tokens for r in ok if r.completion_tokens is not None]),
    )


def _score(item: GoldenItem, result: QueryResult, response: ChatResponse, hits: list[RetrievedChunk],
           judge: LLMJudge | None) -> None:
    answer = response.answer
    abstained = is_abstention(response)
    metrics = result.metrics
    metrics.update(retrieval_metrics(item, hits, response.sources))
    metrics.update(correctness_metrics(item, answer))
    spec = item.answer
    if judge is not None and spec is not None and spec.answerable and (spec.reference or spec.must_include):
        metrics['judge_correctness'] = judge.correctness(item.query, answer, spec)
    if not abstained:
        metrics.update(citation_metrics(item, answer, response.sources))
        contexts = faithfulness_contexts(response, hits)
        if judge is not None:
            result.faithfulness_method = 'judge'
            metrics['faithfulness'] = judge.faithfulness(answer, contexts)
        else:
            result.faithfulness_method = 'lexical'
            metrics['faithfulness'] = lexical_faithfulness(answer, contexts)
    if spec is not None:
        metrics['abstention_accuracy'] = float(abstained != spec.answerable)


def _score_failed_turn(item: GoldenItem, result: QueryResult, judge: LLMJudge | None) -> None:
    """A turn that raised is a failed answer (see this module's docstring)."""
    spec = item.answer
    if spec is None:
        return
    metrics = result.metrics
    if spec.must_include:
        metrics['key_fact_recall'] = 0.0
    if spec.reference:
        metrics['token_f1'] = 0.0
    if judge is not None and spec.answerable and (spec.reference or spec.must_include):
        metrics['judge_correctness'] = 0.0
    metrics['abstention_accuracy'] = 0.0


def preflight(bot_id: str, endpoints: list[str] | None, user: ChatUser) -> dict[str, str | None]:
    """Check once, before the first turn, what every turn would hit:
    the bot exists and the caller may use it, every requested endpoint is
    offered by the bot and actually takes effect, and the chat-provider
    control plane answers. Returns label -> resolved model (None = the
    bot's own YAML model). Raises the `FATAL_ERRORS` the first turn would
    raise, and EvalConfigError when `--endpoint` would be silently ignored:
    `handle_chat` falls back to the YAML model when `fetch_chat_provider`
    returns None (standalone) and never uses an endpoint for an n8n bot."""
    from app.services import chat as chat_service  # noqa: PLC0415 -- same lookups (and test patches) as handle_chat

    bot = chat_service.load_bot(bot_id)
    chat_service._check_permissions(bot, user)
    explicit = list(dict.fromkeys(endpoints)) if endpoints else []
    if bot.model.provider == 'n8n':
        if explicit:
            raise EvalConfigError(f'Bot {bot_id!r} delegiert an n8n; --endpoint wird dort nicht verwendet.')
        return {DEFAULT_MODEL_LABEL: None}
    resolved: dict[str, str | None] = {}
    for model in explicit or [None]:
        endpoint_id = chat_service._resolve_llm_endpoint(bot, model)
        try:
            snapshot = chat_service.fetch_chat_provider(endpoint_id)
        except ChatConfigUnavailable as exc:
            raise EvalConfigError(str(exc)) from exc
        active = snapshot is not None and snapshot.enabled
        if model is not None and not active:
            raise EvalConfigError(
                f'LLM-Endpunkt {model!r} wirkt nicht: '
                + ('zentrale Chat-Konfiguration ist nicht eingerichtet (CHAT_CONFIG_BASE_URL).'
                   if snapshot is None else 'er ist deaktiviert.')
            )
        resolved[model or DEFAULT_MODEL_LABEL] = snapshot.model if active else None
    by_model: dict[str, list[str]] = {}
    for label, resolved_model in resolved.items():
        if resolved_model is not None:
            by_model.setdefault(resolved_model, []).append(label)
    for resolved_model, labels in by_model.items():
        if len(labels) > 1:
            logger.warning('endpoints %s all resolve to model %r', ', '.join(labels), resolved_model)
    return resolved


def evaluate_answers(
    items: list[GoldenItem],
    *,
    bot_id: str,
    endpoints: list[str] | None = None,
    user: ChatUser | None = None,
    judge: LLMJudge | None = None,
    chat_fn: Callable[[ChatRequest], ChatResponse] | None = None,
) -> AnswerEvaluation:
    """Run every golden item through `chat_fn` (default: the real
    `handle_chat`) once per endpoint (`None`/empty = the bot's default
    model) and score the answers. With the real pipeline `preflight` runs
    first. A `FATAL_ERRORS` exception aborts the run; any other per-turn
    exception (including a transient `ChatConfigUnavailable`) is recorded on
    that query's result, scored as a failed answer (`_score_failed_turn`)
    and counted in `ModelSummary.errors`."""
    models: list[str | None] = list(dict.fromkeys(endpoints)) if endpoints else [None]
    labels = [model or DEFAULT_MODEL_LABEL for model in models]
    caller = user or ChatUser()
    if chat_fn is None:
        from app.services.chat import handle_chat as chat_fn  # noqa: PLC0415 -- resolved at call time for tests

        preflight(bot_id, endpoints, caller)
    results: list[QueryResult] = []
    with _recorders_installed():
        for model, label in zip(models, labels, strict=True):
            for item in items:
                result = QueryResult(model=label, query=item.query)
                recording = TurnRecording()
                token = _ACTIVE.set(recording)
                start = time.perf_counter()
                try:
                    response = chat_fn(ChatRequest(bot_id=bot_id, message=item.query, user=caller, llm_endpoint=model))
                except FATAL_ERRORS:
                    raise
                except Exception as exc:  # noqa: BLE001 -- recorded per query, see docstring
                    response = None
                    result.error = f'{type(exc).__name__}: {exc}'
                finally:
                    _ACTIVE.reset(token)
                    result.latency_ms = (time.perf_counter() - start) * 1000
                hits = _dedupe_hits(recording.hits)
                result.searches = recording.searches
                result.retrieved = [getattr(hit, 'source_job_id', None) for hit in hits]
                result.llm_calls = recording.llm_calls
                if recording.usage_reported:
                    result.prompt_tokens = recording.prompt_tokens
                    result.completion_tokens = recording.completion_tokens
                if response is not None:
                    guard = response.trace.guard
                    result.answer = response.answer
                    result.answer_model = response.trace.model
                    result.abstained = is_abstention(response)
                    result.guard_reason = guard.reason if guard is not None and guard.triggered else None
                    result.sources = [source.source_job_id for source in response.sources]
                    _score(item, result, response, hits, judge)
                else:
                    _score_failed_turn(item, result, judge)
                results.append(result)
    summaries = [summarize(label, [r for r in results if r.model == label]) for label in labels]
    return AnswerEvaluation(
        bot_id=bot_id, models=labels, judge=judge.label if judge is not None else None,
        summaries=summaries, results=results,
    )


# --- reports -------------------------------------------------------------------------------


def format_value(value: float | None, digits: int = 3) -> str:
    return '–' if value is None else f'{value:.{digits}f}'


def _report_rows(evaluation: AnswerEvaluation) -> list[tuple[str | None, str, list[str]]]:
    """(group heading or None, row label, one cell per model)."""
    rows: list[tuple[str | None, str, list[str]]] = []
    summaries = evaluation.summaries
    for _, heading, names in METRIC_GROUPS:
        rows.append((heading, '', []))
        for name in names:
            cells = [
                f'{format_value(s.metrics[name])} (n={s.counts[name]})' if s.metrics[name] is not None else '–'
                for s in summaries
            ]
            rows.append((None, name, cells))
    rows.append(('Betrieb', '', []))
    rows.append((None, 'queries', [str(s.queries) for s in summaries]))
    rows.append((None, 'errors', [str(s.errors) for s in summaries]))
    rows.append((None, 'latency_ms_mean', [format_value(s.latency_ms_mean, 1) for s in summaries]))
    rows.append((None, 'latency_ms_p95', [format_value(s.latency_ms_p95, 1) for s in summaries]))
    rows.append((None, 'prompt_tokens_mean', [format_value(s.prompt_tokens_mean, 1) for s in summaries]))
    rows.append((None, 'completion_tokens_mean', [format_value(s.completion_tokens_mean, 1) for s in summaries]))
    return rows


def format_text_report(evaluation: AnswerEvaluation) -> str:
    rows = _report_rows(evaluation)
    label_width = max([len('Metrik')] + [len(label) + 2 for _, label, _ in rows])
    widths = [
        max([len(model)] + [len(cells[i]) for heading, _, cells in rows if heading is None])
        for i, model in enumerate(evaluation.models)
    ]
    lines = [
        f'Antwort-Evaluation: Bot {evaluation.bot_id!r}, {len(evaluation.models)} Modell(e)'
        + (f', Judge {evaluation.judge!r}' if evaluation.judge else ', ohne Judge (faithfulness lexikalisch)'),
        '',
        'Metrik'.ljust(label_width) + '  ' + '  '.join(m.rjust(w) for m, w in zip(evaluation.models, widths, strict=True)),
    ]
    for heading, label, cells in rows:
        if heading is not None:
            lines.append(heading)
        else:
            lines.append(
                f'  {label}'.ljust(label_width) + '  ' + '  '.join(c.rjust(w) for c, w in zip(cells, widths, strict=True))
            )
    errors = [r for r in evaluation.results if r.error]
    if errors:
        lines += ['', f'Fehler ({len(errors)}):']
        lines += [f'  [{r.model}] {r.query[:60]}: {r.error[:200]}' for r in errors[:10]]
        if len(errors) > 10:
            lines.append(f'  ... und {len(errors) - 10} weitere (siehe --json)')
    return '\n'.join(lines)


def format_markdown_report(evaluation: AnswerEvaluation) -> str:
    lines = [
        f'# Antwort-Evaluation: `{evaluation.bot_id}`',
        '',
        f'Judge: `{evaluation.judge}`' if evaluation.judge else 'Judge: keiner (faithfulness lexikalisch)',
        '',
        '| Metrik | ' + ' | '.join(f'`{model}`' for model in evaluation.models) + ' |',
        '|---|' + '---:|' * len(evaluation.models),
    ]
    for heading, label, cells in _report_rows(evaluation):
        if heading is not None:
            lines.append(f'| **{heading}** |' + ' |' * len(evaluation.models))
        else:
            lines.append(f'| {label} | ' + ' | '.join(cells) + ' |')
    errors = [r for r in evaluation.results if r.error]
    if errors:
        lines += ['', '## Fehler', '']
        lines += [f'- `{r.model}` – {r.query}: {r.error}' for r in errors]
    return '\n'.join(lines) + '\n'


def to_json(evaluation: AnswerEvaluation) -> dict:
    return {
        'bot_id': evaluation.bot_id,
        'models': evaluation.models,
        'judge': evaluation.judge,
        'metric_groups': {key: list(names) for key, _, names in METRIC_GROUPS},
        'aggregate': {summary.model: asdict(summary) for summary in evaluation.summaries},
        'queries': [asdict(result) for result in evaluation.results],
    }
