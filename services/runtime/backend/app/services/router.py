"""Intent classification for an incoming chat message (see
app/schemas/chat.py's ChatTrace.intent/confidence/needs_retrieval/needs_tool
-- a later stage's chat pipeline builds that trace straight from this
module's RouterDecision).

Two interchangeable classification strategies, selected via route()'s
`mode` argument (a plain str, same looseness as settings.router_mode
itself -- see app/core/config.py) rather than read from `settings` inside
this module: callers/tests control it explicitly, the same reasoning
app/services/botconfig.py's functions apply to `settings.bots_dir` (an
explicit parameter would be even more direct, but `mode` mirrors
ROUTER_MODE's own name closely enough that threading it through as a plain
argument keeps call sites obvious without this module importing `settings`
at all).

- 'rules' (the default, and the fallback target for 'llm' -- see below): a
  deterministic, dependency-free keyword/regex classifier over the
  lowercased message. No network call, no model -- exactly the kind of
  fixture-free classifier Weave-Retrieval's FakeReranker and
  Weave-Knowledge's FakeEmbeddingProvider are for their own pipelines, and
  the only mode a bot running against the 'fake' LLM provider (the
  settings.llm_provider default) ever needs.
- 'llm' (ROUTER_MODE='llm'): delegates classification to an injected
  LLMCallable -- a slim Protocol defined below, deliberately NOT an import
  of app/services/llm.py. That module is being built in a parallel stage;
  this one must not couple to its concrete provider shape before it
  exists, and the Protocol is all route() actually needs from it (a
  `(messages, model) -> str` callable, nothing about how that string was
  produced). Any failure on this path -- the callable raising, or its
  response not parsing as the expected classifier JSON -- falls back to
  the 'rules' result rather than propagating: a classification error must
  never itself break the chat pipeline the way a retrieval or
  LLM-generation failure further downstream legitimately can.
  RouterDecision.router_fallback records when this happened, so a caller
  building ChatTrace can surface it for debugging without route() itself
  raising.

Every intent maps to (needs_retrieval, needs_tool) deterministically and
identically in BOTH modes (see _flags_for_intent) -- the 'llm' mode only
ever supplies (intent, confidence) itself, never those two flags, so a bot
with retrieval disabled can never end up with needs_retrieval=True no
matter which mode classified the message.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from app.schemas.bot import BotConfig

logger = logging.getLogger(__name__)

Intent = Literal['conversational', 'knowledge', 'document', 'action', 'complex']

_INTENTS: frozenset[str] = frozenset({'conversational', 'knowledge', 'document', 'action', 'complex'})

# RULES-mode confidence: 0.9 for an actual keyword/regex match, 0.6 for the
# 'sonst' default (knowledge-if-retrieval-else-conversational) branch that
# fires when nothing else matched -- see _decide_rules.
_CONFIDENT = 0.9
_DEFAULT = 0.6


@dataclass(frozen=True)
class RouterDecision:
    intent: Intent
    confidence: float
    needs_retrieval: bool
    needs_tool: bool
    # True only when 'llm' mode itself fell back to a RULES decision (a
    # provider exception, unparseable response, or llm_call=None with
    # mode='llm') -- always False for a decision RULES produced directly,
    # including when mode='rules' was requested outright (there is nothing
    # to fall back FROM in that case). See this module's docstring.
    router_fallback: bool = False


class LLMCallable(Protocol):
    """The slim seam route()'s 'llm' mode depends on instead of importing
    app/services/llm.py (built in a parallel stage -- see this module's
    docstring for why that import must not happen here). `messages` is an
    OpenAI-style chat transcript (`[{'role': ..., 'content': ...}, ...]`);
    the callable returns the model's raw text response as a plain string --
    this module parses that response as classifier JSON itself, the
    callable does not.
    """

    def __call__(self, messages: list[dict[str, str]], model: str) -> str: ...


def _flags_for_intent(intent: Intent, bot: BotConfig) -> tuple[bool, bool]:
    """(needs_retrieval, needs_tool) for `intent` -- shared by both RULES
    and 'llm' mode so the two flags never depend on which mode classified
    the message, only on the resulting intent plus this bot's own
    retrieval.enabled.
    """
    needs_retrieval = intent == 'knowledge' or (intent == 'complex' and bot.retrieval.enabled)
    needs_tool = intent in ('action', 'complex')
    return needs_retrieval, needs_tool


# --- RULES mode --------------------------------------------------------------


def _boundary_patterns(*phrases: str) -> tuple[re.Pattern[str], ...]:
    """One word-boundary-anchored, case-already-lowered regex per phrase.
    Boundaries matter even for multi-word phrases (a leading/trailing `\\b`
    still guards against e.g. matching inside a longer compound word), and
    matter a lot more for the short single-word ones ('hi' as a bare
    substring would match inside half the German language, e.g. 'wichtig',
    'nicht' -- \\b rules that out).
    """
    return tuple(re.compile(rf'\b{re.escape(phrase)}\b') for phrase in phrases)


# Greeting/small-talk/thanks, mixed German and English per the task's own
# example list ('hallo', 'danke', 'wie geht', ...). `dank\w*` is a stem
# match (not a fixed phrase) so it covers 'danke', 'dankeschön', and the
# 'dank' in 'vielen dank'/'besten dank' with one pattern.
_GREETING_PATTERNS = _boundary_patterns(
    'hallo', 'servus', 'moin', 'guten morgen', 'guten tag', 'guten abend',
    'hi', 'hey', 'hello', 'wie geht', 'thanks', 'thank you', 'how are you',
) + (re.compile(r'\bdank\w*\b'),)

# Document direct-processing: the user is handing this message's own
# attachment/pasted content to the bot for summarize/translate, not asking
# a knowledge-base question. 'fass\w*...zusammen' is a stem+wildcard regex
# (not a fixed-phrase boundary match) so it covers 'fasse/fasst/fass ...
# zusammen' with one pattern, mirroring 'dank\w*' above.
_DOCUMENT_PATTERNS = _boundary_patterns(
    'dieses pdf', 'diesem pdf', 'dieses dokument', 'diesem dokument',
    'anbei', 'angehängt', 'angehaengt', 'übersetze', 'uebersetze',
) + (re.compile(r'\bfass\w*\b.*\bzusammen\b'),)

# Action imperatives: the user wants something DONE (a tool call), not
# answered. 'schick\w*' covers 'schick/schicke/schicken'; the other two
# regexes cover the multi-word patterns from the task spec directly.
_ACTION_PATTERNS = _boundary_patterns('buche', 'sende', 'storniere') + (
    re.compile(r'\bschick\w*\b'),
    re.compile(r'\berstelle\s+ein\s+ticket\b'),
    re.compile(r'\blege\b.*\ban\b'),
)

# Multi-step/comparison: 'vergleich\w*' covers 'vergleiche/vergleichen/...'.
_COMPLEX_PATTERNS = (
    re.compile(r'\bvergleich\w*\b.*\b(?:und|oder)\b'),
    re.compile(r'\berst\b.*\bdann\b'),
)


def _has_multiple_linked_questions(message: str) -> bool:
    """'mehrere Fragezeichen + und/oder-Verknuepfung' from the task spec:
    at least two questions joined by 'und'/'oder' (e.g. 'Wie ist X? Und was
    ist Y?'). A plain question-mark count plus an und/oder substring check
    -- deliberately no smarter than that, same spirit as every other RULES
    pattern in this module.
    """
    return message.count('?') >= 2 and (' und ' in message or ' oder ' in message)


def _matched_rules_intent(message: str) -> Intent | None:
    """The lowercased message's intent per the RULES heuristics, checked in
    the task spec's own listed order (greeting, document, action, complex),
    or None if nothing matched -- in which case _decide_rules applies the
    retrieval-dependent 'sonst' default itself.
    """
    if any(pattern.search(message) for pattern in _GREETING_PATTERNS):
        return 'conversational'
    if any(pattern.search(message) for pattern in _DOCUMENT_PATTERNS):
        return 'document'
    if any(pattern.search(message) for pattern in _ACTION_PATTERNS):
        return 'action'
    if any(pattern.search(message) for pattern in _COMPLEX_PATTERNS) or _has_multiple_linked_questions(message):
        return 'complex'
    return None


def _decide_rules(message: str, bot: BotConfig) -> RouterDecision:
    matched = _matched_rules_intent(message.lower())
    intent = matched if matched is not None else ('knowledge' if bot.retrieval.enabled else 'conversational')
    needs_retrieval, needs_tool = _flags_for_intent(intent, bot)
    confidence = _CONFIDENT if matched is not None else _DEFAULT
    return RouterDecision(intent=intent, confidence=confidence, needs_retrieval=needs_retrieval, needs_tool=needs_tool)


# --- LLM mode ----------------------------------------------------------------

_LLM_SYSTEM_PROMPT = (
    'You are an intent classifier for a chat routing system. Classify the '
    "user's next message into exactly one of these intents:\n"
    '- conversational: greetings, small talk, thanks\n'
    "- knowledge: a question answerable from the bot's knowledge base\n"
    '- document: the user is handing over a document/text of their own to '
    'process directly (summarize, translate, ...)\n'
    '- action: the user wants an action performed (book, send, create a '
    'ticket, cancel, ...)\n'
    '- complex: a multi-step request or a comparison spanning more than one '
    'of the above\n\n'
    'Respond with JSON only, no other text and no explanation: '
    '{"intent": "<one of the five intents above>", "confidence": <number between 0.0 and 1.0>}'
)

_CODE_FENCE_RE = re.compile(r'```(?:json)?\s*(.*?)\s*```', re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Unwrap a ```/```json fenced block if present, else return `text`
    unchanged. LLMs asked for "JSON only" still commonly wrap it in a
    fenced code block anyway -- tolerating that is cheaper than fighting it
    with a stricter prompt.
    """
    match = _CODE_FENCE_RE.search(text)
    return match.group(1) if match else text


def _parse_llm_classification(raw: str) -> tuple[Intent, float]:
    """Parse `raw` (an LLMCallable's return value) as
    `{"intent": ..., "confidence": ...}`. Raises ValueError for anything
    that doesn't yield a valid (Intent, float) pair -- malformed JSON, a
    JSON value that isn't an object, an intent outside the five known
    values, or a non-numeric confidence -- which _decide_llm catches to
    trigger the RULES fallback (see this module's docstring).
    """
    stripped = _strip_code_fence(raw).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f'router LLM response is not valid JSON: {exc}') from exc

    if not isinstance(data, dict):
        raise ValueError(f'router LLM response is not a JSON object: {data!r}')

    intent = data.get('intent')
    if intent not in _INTENTS:
        raise ValueError(f'router LLM response has an unknown intent: {intent!r}')

    confidence = data.get('confidence')
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError(f'router LLM response has a non-numeric confidence: {confidence!r}')

    return intent, float(confidence)


def _classification_messages(message: str) -> list[dict[str, str]]:
    return [
        {'role': 'system', 'content': _LLM_SYSTEM_PROMPT},
        {'role': 'user', 'content': message},
    ]


def _decide_llm(message: str, bot: BotConfig, llm_call: LLMCallable) -> RouterDecision:
    try:
        raw = llm_call(_classification_messages(message), bot.model.model)
        intent, confidence = _parse_llm_classification(raw)
    except Exception as exc:  # noqa: BLE001 -- any provider/parse failure falls back, see module docstring
        logger.warning('router LLM classification failed, falling back to RULES: %s', exc)
        return replace(_decide_rules(message, bot), router_fallback=True)

    needs_retrieval, needs_tool = _flags_for_intent(intent, bot)
    return RouterDecision(intent=intent, confidence=confidence, needs_retrieval=needs_retrieval, needs_tool=needs_tool)


# --- Entry point ---------------------------------------------------------


def route(
    message: str,
    bot: BotConfig,
    mode: str = 'rules',
    llm_call: LLMCallable | None = None,
) -> RouterDecision:
    """Classify `message`'s intent for `bot` and return the RouterDecision
    a later stage's chat pipeline builds ChatTrace from (see
    app/schemas/chat.py).

    `mode` mirrors settings.router_mode's values ('rules'/'llm') but is
    taken as an explicit argument rather than read from `settings` here --
    see this module's docstring. Anything other than the literal string
    'llm' is treated as RULES, matching settings.router_mode's own "plain
    str, not a Literal/enum" looseness (app/core/config.py) -- an
    unrecognised mode falls back to the deterministic default rather than
    raising.

    `llm_call` is only consulted when `mode == 'llm'`; passing that mode
    without an `llm_call` (nothing to call) is treated the same as any
    other 'llm'-mode failure -- a RULES decision with `router_fallback=True`.
    """
    if mode == 'llm' and llm_call is not None:
        return _decide_llm(message, bot, llm_call)
    if mode == 'llm':
        return replace(_decide_rules(message, bot), router_fallback=True)
    return _decide_rules(message, bot)
