"""`effective_scope` -- the single, pure Collections-intersection function
every future delegation (subagent `search_knowledge` calls, per the agent-
mode rollout plan) must use, generalizing app/services/chat.py's existing
two-axis `_resolve_rights_scope`/`_apply_collections_filter` pair (user
rights x main-bot collections x per-request filter) to a fourth axis: a
subagent's own explicitly allowed collections.

Kept in its own module, separate from chat.py, because it has no
dependency on `BotConfig`/`ChatUser`/`retrieval_client` at all -- every
input is already a plain, resolved `list[str]` (or `None`), which is
exactly what makes it trivially unit-testable and safe to call from a
future subagent-scope resolver without pulling in the whole chat pipeline.
`NO_COLLECTION_SENTINEL` is imported from chat.py rather than redefined
here, so there is exactly one copy of that string in this codebase (see
chat.py's own docstring on why it can't just import it from Weave-
Retrieval instead).

This function is NOT wired into `_run_knowledge_turn`/`resolve_collection_
scope` (chat.py) by this change -- those keep working exactly as before,
byte-for-byte, for every bot without agent mode. A future agent-mode
turn's own subagent-scope resolution is the first real caller.
"""

from __future__ import annotations

from app.services.chat import NO_COLLECTION_SENTINEL


def effective_scope(
    user_readable: list[str],
    bot_collections: list[str],
    subagent_collections: list[str] | None,
    request_filter: list[str] | None,
    *,
    include_uncollected: bool,
) -> list[str]:
    """The `allowed_collections` argument for a scoped `retrieval_client.
    search()` call: user rights ∩ main-bot collections ∩ subagent
    collections ∩ per-request filter, with `NO_COLLECTION_SENTINEL`
    ("__none__") appended only when Altbestand access is actually
    admissible for this call. Returns `[]` whenever the intersection is
    empty -- the caller's own signal to skip `search()` entirely and guard
    instead, exactly like chat.py's `_resolve_rights_scope`/`_run_
    knowledge_turn` already do for the two-axis case.

    Each argument follows one of two conventions already established by
    chat.py's own `_resolve_rights_scope`/`_apply_collections_filter`, and
    this function does not invent a third:

    - `user_readable`: the caller's own Collections read-authority (e.g.
      `retrieval_client.list_collections()`'s slugs) -- always a concrete
      list, never `None` (Weave-Runtime is never the "unrestricted
      service-internal caller" Weave-Retrieval reserves `None` for).
    - `bot_collections`: EMPTY = the bot names no restriction of its own,
      so `user_readable` passes through this axis unfiltered ("Leere Bot-
      Liste = alle lesbaren", `RetrievalConfig.collections`'s own
      contract). A non-empty list narrows to its intersection with
      `user_readable`.
    - `subagent_collections`: `None` = no subagent axis in play at all (no
      agent mode, or a subagent inheriting the bot's own scope wholesale)
      -- identity, no narrowing. Any list (INCLUDING `[]`) narrows for
      real: `[]` means a subagent explicitly configured with zero
      collections, and therefore sees nothing, on this axis alone.
    - `request_filter`: `ChatRequest.collections`'s own convention,
      unchanged -- `None` is identity (no filter sent), any list
      (including `[]`) narrows for real.

    `include_uncollected` is the CALLER's own already-combined decision
    (e.g. `bot.retrieval.include_uncollected AND subagent.include_
    uncollected`, when a subagent is in play) -- this function knows
    nothing about two separate flags, only whether Altbestand documents are
    admissible for THIS call at all once the real-collections intersection
    is known.

    One narrow exception, mirroring `_resolve_rights_scope`'s own carve-
    out: when NEITHER the bot NOR the subagent axis names any real-
    collection restriction of its own (`bot_collections` empty AND
    `subagent_collections` is `None`), and the user can read NO real
    Collections at all, a pure Altbestand-only result (`[NO_COLLECTION_
    SENTINEL]` alone) is still returned when `include_uncollected` is
    True, rather than guarding off -- there IS legacy content this
    combination is entitled to see. Any axis that DOES name a real
    restriction of its own (a non-empty `bot_collections`, or a
    `subagent_collections` that is not `None`) is never covered by this
    exception: an empty intersection there always means `[]`.
    """
    real_scope = list(user_readable)
    if bot_collections:
        bot_set = set(bot_collections)
        real_scope = [slug for slug in real_scope if slug in bot_set]

    no_bot_restriction = not bot_collections
    no_subagent_restriction = subagent_collections is None

    if subagent_collections is not None:
        subagent_set = set(subagent_collections)
        real_scope = [slug for slug in real_scope if slug in subagent_set]

    if not real_scope:
        if no_bot_restriction and no_subagent_restriction and not user_readable and include_uncollected:
            scoped: list[str] = [NO_COLLECTION_SENTINEL]
        else:
            scoped = []
    elif include_uncollected:
        scoped = [*real_scope, NO_COLLECTION_SENTINEL]
    else:
        scoped = real_scope

    if request_filter is None:
        return scoped
    requested_set = set(request_filter)
    return [slug for slug in scoped if slug in requested_set]
