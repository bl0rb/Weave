"""Unit tests for app/services/scope.py's `effective_scope` -- the pure,
4-axis (user rights x main-bot collections x subagent collections x
per-request filter) generalization of chat.py's own `_resolve_rights_
scope`/`_apply_collections_filter` pair. See that module's own docstring
for the conventions each argument follows.

Two groups of tests:

- Parity tests, proving that `subagent_collections=None` (no subagent axis
  in play) makes `effective_scope` behave byte-for-byte like the existing
  `resolve_collection_scope(bot, user, requested_collections)` two-axis
  pipeline it generalizes -- i.e. adding this function changes nothing for
  any bot without agent mode.
- The intersection matrix itself: subagent narrowing, the sentinel's three
  admissibility cases, and the empty-vs-identity convention for each axis.
"""

from app.services.chat import NO_COLLECTION_SENTINEL
from app.services.scope import effective_scope

# --- parity with the existing two-axis resolve_collection_scope pipeline ----
# (subagent_collections=None is the "no subagent axis" identity case)


def test_no_subagent_axis_intersects_bot_collections_with_readable_collections():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch'],
        bot_collections=['vertraege'],
        subagent_collections=None,
        request_filter=None,
        include_uncollected=True,
    )
    assert scope == ['vertraege', NO_COLLECTION_SENTINEL]


def test_no_subagent_axis_returns_every_readable_collection_when_the_bot_lists_none():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch'],
        bot_collections=[],
        subagent_collections=None,
        request_filter=None,
        include_uncollected=True,
    )
    assert scope == ['vertraege', 'handbuch', NO_COLLECTION_SENTINEL]


def test_no_subagent_axis_is_empty_when_the_user_cannot_read_the_bots_collection():
    scope = effective_scope(
        user_readable=['handbuch'],
        bot_collections=['vertraege'],
        subagent_collections=None,
        request_filter=None,
        include_uncollected=True,
    )
    assert scope == []


def test_no_subagent_axis_allows_a_pure_legacy_search_when_nothing_else_is_readable():
    scope = effective_scope(
        user_readable=[],
        bot_collections=[],
        subagent_collections=None,
        request_filter=None,
        include_uncollected=True,
    )
    assert scope == [NO_COLLECTION_SENTINEL]


def test_no_subagent_axis_is_empty_when_the_caller_can_read_nothing_and_uncollected_is_excluded():
    scope = effective_scope(
        user_readable=[],
        bot_collections=[],
        subagent_collections=None,
        request_filter=None,
        include_uncollected=False,
    )
    assert scope == []


def test_no_subagent_axis_omits_the_sentinel_when_include_uncollected_is_false():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch'],
        bot_collections=['vertraege'],
        subagent_collections=None,
        request_filter=None,
        include_uncollected=False,
    )
    assert scope == ['vertraege']


def test_no_subagent_axis_filter_narrows_to_the_requested_slug():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch'],
        bot_collections=[],
        subagent_collections=None,
        request_filter=['handbuch'],
        include_uncollected=True,
    )
    assert scope == ['handbuch']


def test_no_subagent_axis_filter_silently_drops_a_foreign_slug_and_keeps_the_rest():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch'],
        bot_collections=[],
        subagent_collections=None,
        request_filter=['handbuch', 'geheimprojekt'],
        include_uncollected=True,
    )
    assert scope == ['handbuch']


def test_no_subagent_axis_filter_with_an_explicit_empty_list_matches_nothing():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch'],
        bot_collections=['vertraege'],
        subagent_collections=None,
        request_filter=[],
        include_uncollected=True,
    )
    assert scope == []


def test_no_subagent_axis_filter_cannot_grant_the_sentinel_rights_never_included():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch'],
        bot_collections=['vertraege'],
        subagent_collections=None,
        request_filter=[NO_COLLECTION_SENTINEL],
        include_uncollected=False,
    )
    assert scope == []


# --- the subagent axis itself -------------------------------------------------


def test_subagent_collections_narrows_the_bot_scope_further():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch', 'it'],
        bot_collections=['vertraege', 'handbuch'],
        subagent_collections=['handbuch'],
        request_filter=None,
        include_uncollected=False,
    )
    assert scope == ['handbuch']


def test_subagent_collections_can_span_several_collections():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch', 'it'],
        bot_collections=[],
        subagent_collections=['handbuch', 'it'],
        request_filter=None,
        include_uncollected=False,
    )
    assert scope == ['handbuch', 'it']


def test_subagent_collections_empty_list_matches_nothing_even_though_bot_and_user_allow_more():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch'],
        bot_collections=[],
        subagent_collections=[],
        request_filter=None,
        include_uncollected=True,
    )
    assert scope == []


def test_subagent_collections_outside_the_bots_own_scope_are_dropped():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch', 'geheim'],
        bot_collections=['vertraege', 'handbuch'],
        subagent_collections=['geheim'],
        request_filter=None,
        include_uncollected=False,
    )
    assert scope == []


def test_subagent_collections_outside_the_users_own_readable_set_are_dropped():
    scope = effective_scope(
        user_readable=['vertraege'],
        bot_collections=[],
        subagent_collections=['vertraege', 'handbuch'],
        request_filter=None,
        include_uncollected=False,
    )
    assert scope == ['vertraege']


def test_subagent_collections_and_request_filter_both_narrow_together():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch', 'it'],
        bot_collections=[],
        subagent_collections=['handbuch', 'it'],
        request_filter=['it'],
        include_uncollected=False,
    )
    assert scope == ['it']


def test_subagent_collections_present_but_empty_intersection_never_falls_back_to_the_sentinel_exception():
    # Unlike the no-subagent-axis case, a subagent that DOES name a real
    # collections restriction (even one the caller cannot fully read) is
    # never covered by the "nothing else readable" Altbestand exception --
    # see effective_scope's own docstring.
    scope = effective_scope(
        user_readable=[],
        bot_collections=[],
        subagent_collections=['handbuch'],
        request_filter=None,
        include_uncollected=True,
    )
    assert scope == []


def test_sentinel_is_appended_only_when_the_real_intersection_across_all_four_axes_is_non_empty():
    scope = effective_scope(
        user_readable=['vertraege', 'handbuch'],
        bot_collections=['vertraege', 'handbuch'],
        subagent_collections=['handbuch'],
        request_filter=None,
        include_uncollected=True,
    )
    assert scope == ['handbuch', NO_COLLECTION_SENTINEL]


def test_request_filter_can_narrow_down_to_the_sentinel_alone():
    scope = effective_scope(
        user_readable=['vertraege'],
        bot_collections=[],
        subagent_collections=None,
        request_filter=[NO_COLLECTION_SENTINEL],
        include_uncollected=True,
    )
    assert scope == [NO_COLLECTION_SENTINEL]
