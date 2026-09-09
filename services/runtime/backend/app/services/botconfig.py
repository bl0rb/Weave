"""Reads and validates bot configuration YAML files (see bots/*.yaml,
app/schemas/bot.py's BotConfig).

Every function here re-reads BOTS_DIR from disk on every call -- there is no
in-process cache, and therefore nothing to invalidate when an operator
edits/adds a bot file. Weave-Runtime is deliberately stateless (see README's
"Nicht-Ziele"), and a bot roster small enough to hand-author as YAML files
is also small enough to re-parse on every request/health-check without that
ever becoming a measurable cost; the alternative -- an in-process cache plus
either a restart or a file-watcher to pick up an edited bot -- is complexity
this service has no need for at this size.
"""

import re
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

import yaml
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.bot import BotConfig
from app.services.chat_config_client import fetch_managed_bots


class BotConfigError(Exception):
    """A bot's YAML file exists but is not a valid BotConfig -- either
    malformed YAML syntax or a schema violation. The message always names
    the offending file so an operator can find it without BOTS_DIR's full
    listing being echoed back."""


class BotNotFoundError(KeyError):
    """No bot with the requested id exists in BOTS_DIR. Subclasses KeyError
    deliberately: from a caller's perspective, `load_bot(bot_id)` is a
    lookup in the same family as `some_dict[bot_id]`, so it should fail the
    same way (and be catchable the same way) rather than inventing a new
    exception hierarchy for what is fundamentally a missing-key lookup."""


def _bot_files() -> list[Path]:
    bots_dir = Path(settings.bots_dir)
    # Path.iterdir() raises FileNotFoundError/NotADirectoryError (both
    # OSError) for a missing/wrong-type BOTS_DIR -- left uncaught here so
    # app/main.py's healthcheck can tell "BOTS_DIR itself is the problem"
    # apart from "a specific file inside it is invalid" (BotConfigError
    # below) in its own response.
    return sorted(path for path in bots_dir.iterdir() if path.suffix in {'.yaml', '.yml'})


# Rejected outright, before `urlsplit()` ever sees them -- see
# `_split_or_none`'s own docstring for why. No legitimate n8n webhook_url
# needs a backslash or a raw control character, so there is no cost to
# refusing both unconditionally rather than trying to reason about how any
# particular parser happens to treat them today.
_UNSAFE_URL_CHARS = re.compile(r'[\\\x00-\x1f\x7f]')


def _split_or_none(url: str) -> SplitResult | None:
    """`urlsplit(url)`, except:

    1. A `url` containing a backslash or an ASCII control character (any of
       `\\x00`-`\\x1f` -- including tab/newline/CR -- or DEL `\\x7f`) is
       rejected outright, before `urlsplit()` is even called.

       This function's whole job is to make `_base_url_matches` agree with
       whatever code actually opens `url` over the network -- today that is
       `httpx.post()` (`app/services/n8n_client.py`'s `run_flow`).
       Experimentally (see `backend/tests/test_botconfig.py`'s
       backslash/control-character cases; verified against the installed
       httpx), httpx already agrees with `urlsplit()` on both fronts: it
       does not treat a backslash as a path/authority separator, and it
       raises `httpx.InvalidURL` outright on any embedded control character
       rather than silently dropping it. So neither character class is a
       LIVE bypass through this specific pairing right now.

       That agreement is exactly the kind of thing this allowlist cannot
       afford to depend on, though. Weave-API's `return_to` allowlist was
       ported from this very function -- and it turned out to disagree with
       the real consumer of ITS URL (a browser doing the actual
       navigation), for exactly these two character classes: the WHATWG URL
       standard treats a backslash as a slash for http(s) schemes and
       strips embedded tab/newline/CR before parsing, while `urlsplit()`
       (RFC 3986) does neither, so a browser can navigate somewhere this
       function would have said matched an entirely different, disallowed
       host. httpx happens not to share those two behaviours today, but
       "happens not to, on this version, checked once" is a property of
       httpx's current implementation, not a guarantee that survives a
       version bump or a future switch to a different HTTP client. Rejecting
       both character classes here costs nothing and makes this allowlist's
       safety a property of its OWN input validation, not a bet that two
       independently-maintained URL parsers keep agreeing forever.

    2. A netloc whose port component isn't a plain non-negative integer
       (`SplitResult.port`'s own contract) raises `ValueError` lazily, only
       once that property is actually READ -- not from `urlsplit()` itself.
       `_base_url_matches` below always needs both `.hostname` and `.port`,
       so this wrapper forces that lazy evaluation right here and turns the
       failure into `None` too.

    Either way, `None` means "this URL doesn't parse into something with a
    well-defined host/port at all" -- exactly as disqualifying for an
    allowlist match as any other mismatch, never a reason to let an
    unhandled exception abort loading the ENTIRE bot roster over what is,
    from this function's perspective, just one more malformed candidate to
    reject.
    """
    if _UNSAFE_URL_CHARS.search(url):
        return None
    try:
        split = urlsplit(url)
        _ = split.port  # forces the lazy port parse now, not on first use below
    except ValueError:
        return None
    return split


def _base_url_matches(url: str, base: str) -> bool:
    """Whether `url` (a bot's own `n8n.webhook_url`) is actually covered by
    one `base` entry from `settings.n8n_allowed_base_urls` -- scheme, host,
    and port compared EXACTLY (never a raw string prefix, see this
    function's own module-level context in `_validate_n8n_webhook_allowlist`
    for why a plain `str.startswith()` here is an SSRF allowlist bypass:
    `'https://n8n.internal:5678'.startswith`-matches against
    `'https://n8n.internal:56789.evil.example/'` just as readily as against
    the host it was actually meant to allow, since nothing about a bare
    string prefix check knows where a HOST/PORT ends and a PATH begins),
    with the path checked as a prefix only AFTERWARDS, and only once
    scheme/host/port already agree.

    Concretely, in order:

    1. `urlsplit()` both `url` and `base` (via `_split_or_none`, which folds
       an unparseable port into "doesn't match" rather than raising) --
       comparing raw strings can never be scheme/host/port-exact, since
       nothing about string comparison knows which substring IS the host.
    2. `scheme` compared case-insensitively (`SplitResult.scheme` is
       already lowercased by `urlsplit` itself, per its own contract, but
       this function lowercases explicitly anyway rather than depending on
       that undocumented-here implementation detail).
    3. `hostname` compared EXACTLY (also already lowercased by `urlsplit`
       itself -- see `SplitResult.hostname`'s own contract -- which is
       exactly what defeats a same-host-different-case bypass attempt
       without this function doing anything special for it). Critically,
       `.hostname` is the part of the netloc AFTER any `user:pass@`
       userinfo prefix has already been stripped off by `urlsplit` itself
       -- `'https://n8n.internal:5678@evil.example/x'` has `.hostname ==
       'evil.example'`, NOT `'n8n.internal'`, even though the raw string
       starts with the allowed base's own text. A `None` hostname on
       either side (a URL with no netloc at all) never matches anything.
    4. `port` compared EXACTLY as the parsed integer (or `None` when
       neither URL names one) -- never as substrings of the netloc, which
       is what let `':5678'` match `':56789...'` under the old check.
    5. Only once 1-4 all agree: the PATH is checked as a prefix of `url`'s
       own path against `base`'s own path, with a boundary at the next `/`
       (or the end of the string) -- `base`'s path `/webhook` matches
       `/webhook` and `/webhook/agent` but NOT `/webhook-evil`, so an
       operator's configured base no longer needs a trailing `/` to be
       safe from a same-prefix-different-endpoint sibling path the way the
       old `str.startswith()` check did (see N8N_ALLOWED_BASE_URLS's own
       docstring in app/core/config.py, now updated to match).
    """
    parsed = _split_or_none(url)
    base_parsed = _split_or_none(base)
    if parsed is None or base_parsed is None:
        return False

    if parsed.scheme.lower() != base_parsed.scheme.lower():
        return False

    if parsed.hostname is None or base_parsed.hostname is None or parsed.hostname != base_parsed.hostname:
        return False

    if parsed.port != base_parsed.port:
        return False

    base_path = base_parsed.path or '/'
    url_path = parsed.path or '/'
    # Reject dot-segments outright rather than resolving them -- checked on
    # the PERCENT-DECODED path, not the raw one. httpx normalises
    # '/webhook/../../admin' to '/admin' before the request is sent, so a
    # path-scoped allowlist entry (one base per tenant, say) would be
    # satisfied by a URL that ends up somewhere else entirely. Host and port
    # are already pinned above, so this is not cross-host SSRF -- but a path
    # scope that only holds until httpx rewrites it is no scope at all.
    #
    # Decoding first (rather than splitting the raw path like an earlier
    # version of this check did) closes the same gap with percent-encoding:
    # '/webhook/%2e%2e/admin' splits into a segment that reads '.' or '..'
    # nowhere in its RAW form, so the raw-path version of this check let it
    # straight through -- but httpx's own `.path` property (verified
    # experimentally against the installed httpx) decodes exactly this
    # into '/webhook/../admin' before anything downstream sees it, and a
    # receiving server that normalises the same way would end up outside
    # `base_path` regardless of what this function decided. `unquote()` is
    # the identity function on a path with no `%XX` escapes at all, so this
    # subsumes the plain-dot-segment case rather than needing both checks.
    decoded_url_path = unquote(url_path)
    if any(segment in ('.', '..') for segment in decoded_url_path.split('/')):
        return False
    if not url_path.startswith(base_path):
        return False
    remainder = url_path[len(base_path):]
    return base_path.endswith('/') or remainder == '' or remainder.startswith('/')


def _validate_n8n_webhook_allowlist(bot: BotConfig, path: Path) -> None:
    """SSRF guard for `bot.n8n.webhook_url` (app/schemas/bot.py's
    N8nConfig) -- a no-op for every bot without an `n8n:` block at all.

    This lives here, not as a pydantic validator on N8nConfig itself,
    because it is the one piece of n8n bot validation that genuinely needs
    `settings` (specifically `settings.n8n_allowed_base_urls`) -- keeping
    app/schemas/bot.py itself free of any dependency on runtime
    configuration, consistent with every other model in that module. The
    cross-field "n8n: block present iff provider == 'n8n'" rule, needing no
    settings at all, stays a pydantic `model_validator` on `BotConfig`
    instead (see that module's `_n8n_block_matches_provider`).

    Delegates the actual per-entry comparison to `_base_url_matches` (see
    its own docstring for exactly what "matches" means: scheme/host/port
    compared exactly, the path only as a boundary-respecting prefix
    afterwards) -- never a raw `str.startswith()` the way this used to
    work, since that let a webhook_url whose host/port merely shared a
    STRING prefix with an allowed base (a longer host, an appended port
    digit, userinfo smuggling a different real host, ...) pass as if it
    were actually covered by it.

    Raises BotConfigError (naming `path`, exactly like every other
    validation failure `_load_bot_file` can raise) when `webhook_url`
    doesn't match any configured entry -- including, deliberately, the case
    where `settings.n8n_allowed_base_urls` is itself empty (the default):
    `_base_url_matches` against an empty allowlist can never match anything,
    so EVERY n8n-provider bot fails to load until an operator explicitly
    configures at least one allowed base URL. That is the intended
    behaviour, not an edge case to special-case around -- see
    N8N_ALLOWED_BASE_URLS's own docstring in app/core/config.py ("Leere
    Liste = n8n-Bots sind deaktiviert").

    Checked once, here, at LOAD time -- every caller of `list_bots()`/
    `load_bot()` (GET /health, GET /internal/bots, every chat turn) gets
    this enforcement for free, and a misconfigured/disallowed webhook_url
    is a deployment problem surfaced immediately (a broken bot roster
    entry, same as any other BotConfigError), never discovered only once a
    real user's chat turn happens to reach that specific bot.
    """
    if bot.n8n is None:
        return
    allowed_base_urls = settings.n8n_allowed_base_urls
    if any(_base_url_matches(bot.n8n.webhook_url, base) for base in allowed_base_urls):
        return
    raise BotConfigError(
        f'{path.name}: n8n.webhook_url {bot.n8n.webhook_url!r} does not match any base URL in '
        f'N8N_ALLOWED_BASE_URLS ({allowed_base_urls!r}) -- see app/core/config.py\'s own docstring for that '
        'setting (an empty allowlist disables every n8n-provider bot outright)'
    )


def _load_bot_file(path: Path) -> BotConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise BotConfigError(f'{path.name}: invalid YAML ({exc})') from exc

    if not isinstance(raw, dict):
        raise BotConfigError(f'{path.name}: expected a YAML mapping at the top level, got {type(raw).__name__}')

    try:
        bot = BotConfig.model_validate(raw)
    except ValidationError as exc:
        raise BotConfigError(f'{path.name}: {exc}') from exc

    _validate_n8n_webhook_allowlist(bot, path)
    return bot


def _managed_bot(raw: dict) -> BotConfig:
    """Translate the Ingest control-plane projection into BotConfig."""
    bot_id = str(raw.get('id') or 'unknown')
    try:
        kind = raw.get('kind', 'n8n')
        is_llm = kind == 'llm'
        bot = BotConfig.model_validate({
            'id': raw['id'],
            'name': raw['name'],
            'description': raw.get('description'),
            'model': {'provider': 'fake', 'model': 'fake-chat', 'temperature': raw.get('temperature')} if is_llm else {'provider': 'n8n', 'model': 'n8n-agent-flow'},
            'system_prompt': raw.get('system_prompt') if is_llm else 'Du führst Anfragen über den konfigurierten n8n-Workflow aus.',
            'retrieval': {
                'enabled': bool(raw.get('retrieval_enabled', False)) if is_llm else False,
                'filters': raw.get('retrieval_filters') or {},
                'collections': raw.get('collections') or [],
                'top_k': raw.get('top_k', 20),
                'final_k': raw.get('final_k', 5),
                'rerank': bool(raw.get('rerank', True)),
                'include_uncollected': bool(raw.get('include_uncollected', True)) if is_llm else False,
            },
            'permissions': {'teams': raw.get('teams') or []},
            'guard': {
                'require_sources': bool(raw.get('require_sources', True)),
                'no_context_reply': raw.get('no_context_reply') or 'Ich habe dazu keine belegten Informationen gefunden.',
            },
            'n8n': {
                'webhook_url': raw['webhook_url'],
                'timeout_seconds': raw.get('timeout_seconds', 120),
                'streaming': bool(raw.get('streaming', False)),
                'auth_token': raw.get('auth_token') or None,
            } if not is_llm else None,
        })
    except (ValidationError, KeyError, TypeError) as exc:
        raise BotConfigError(f'managed bot {bot_id!r}: invalid control-plane data') from exc
    _validate_n8n_webhook_allowlist(bot, Path(f'managed-{bot.id}.yaml'))
    return bot


def list_bots() -> list[BotConfig]:
    """Return the effective local-plus-managed roster.

    Local YAML is read first.  Centrally managed ids then override matching
    local ids, and the final list is sorted by bot id for stable API output.
    Invalid local or control-plane data fails the roster as a whole: every
    caller (health, listing, and a chat turn) needs one unambiguous config,
    never a silently partial mix of old and new definitions.
    """
    local = [_load_bot_file(path) for path in _bot_files()]
    managed_raw = fetch_managed_bots()
    if managed_raw is None:
        return local
    managed = [_managed_bot(raw) for raw in managed_raw]
    merged = {bot.id: bot for bot in local}
    merged.update({bot.id: bot for bot in managed})
    return [merged[bot_id] for bot_id in sorted(merged)]


def list_local_bots() -> list[BotConfig]:
    """Validate only image/local YAML bots for the liveness probe.

    Health must stay independent of the optional Ingest control plane; the
    authenticated roster endpoint still uses list_bots() and reports that
    dependency separately.
    """
    return sorted((_load_bot_file(path) for path in _bot_files()), key=lambda bot: bot.id)


def load_bot(bot_id: str) -> BotConfig:
    """The single bot whose validated `id` matches `bot_id` -- NOT the
    filename stem. An operator could name a file differently from the slug
    inside it, and `BotConfig.id` (not the filename) is the one identifier
    callers (ChatRequest.bot_id, see app/schemas/chat.py) actually address a
    bot by."""
    for bot in list_bots():
        if bot.id == bot_id:
            return bot
    raise BotNotFoundError(bot_id)
