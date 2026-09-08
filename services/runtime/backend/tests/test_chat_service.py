"""Unit tests for app/services/chat.py's own small helpers -- built directly
against BotConfig/ChatUser/RetrievedChunk instances (same style as
tests/test_router.py), independent of the HTTP layer. The full pipeline
end-to-end (routing, retrieval, the guard, error mapping) is covered by
tests/test_chat_e2e.py; this file targets the handful of pure-function
decisions `handle_chat` itself is built out of that are otherwise hard to
observe from an HTTP response alone -- particularly `_allowed_teams`'s
empty-permissions.teams-means-unrestricted translation, which the two real
bots under bots/ never actually exercise via the HTTP API (see this
module's own docstring: a bot with a non-empty `permissions.teams` always
rejects a caller with no team before retrieval is ever reached, so that
combination -- non-empty teams as an `allowed_teams` fallback -- has to be
exercised directly here instead), and `resolve_collection_scope`'s own
intersection/empty-bot-list logic -- exercised here with
`retrieval_client.list_collections` monkeypatched directly (no HTTP layer
at all involved), unlike tests/test_chat_e2e.py's full-pipeline
'no_collections' guard test which goes through the real HTTP seam.
"""

from unittest.mock import patch
import pytest

import logging

from app.schemas.bot import BotConfig
from app.schemas.chat import ChatUser, Source
from app.services.chat import (
    NO_COLLECTION_SENTINEL,
    BotPermissionDenied,
    _allowed_teams,
    _check_permissions,
    _context_block,
    _filter_n8n_sources_by_scope,
    _score_for,
    _to_source,
    resolve_collection_scope,
)
from app.services.retrieval_client import Collection, RetrievedChunk, RetrievedChunkScores

_BASE_BOT = {
    'id': 'test-bot',
    'name': 'Test Bot',
    'model': {'model': 'fake-chat'},
    'system_prompt': 'You are a bot.',
}


def _bot(*, teams: list[str] | None = None) -> BotConfig:
    return BotConfig.model_validate({**_BASE_BOT, 'permissions': {'teams': teams or []}})


def _bot_with_collections(collections: list[str], *, include_uncollected: bool = True) -> BotConfig:
    return BotConfig.model_validate(
        {
            **_BASE_BOT,
            'retrieval': {
                'enabled': True,
                'collections': collections,
                'include_uncollected': include_uncollected,
            },
        }
    )


def _collection(slug: str, *, public: bool = False) -> Collection:
    return Collection(slug=slug, name=slug.capitalize(), description=None, public=public)


def _chunk(**overrides) -> RetrievedChunk:
    defaults = dict(
        chunk_id=1,
        document_id='doc-1',
        text='some text',
        source='confluence',
        original_filename='handbook.pdf',
        page_start=3,
        page_end=4,
        document_version=1,
        heading_path=['A', 'B'],
        scores=RetrievedChunkScores(),
    )
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


# --- _check_permissions -------------------------------------------------------


def test_check_permissions_allows_any_team_when_bot_has_no_team_restriction():
    _check_permissions(_bot(teams=[]), ChatUser(team=None))
    _check_permissions(_bot(teams=[]), ChatUser(team='anything'))  # no exception either way


def test_check_permissions_allows_a_member_of_an_allowed_team():
    _check_permissions(_bot(teams=['legal', 'management']), ChatUser(team='legal'))


def test_multiple_memberships_and_explicit_revocation():
    user = ChatUser(team='sales', teams=['sales', 'legal'])
    _check_permissions(_bot(teams=['legal']), user)
    assert _allowed_teams(_bot(), user) == ['sales', 'legal']
    revoked = ChatUser(team='legal', teams=[])
    with pytest.raises(BotPermissionDenied):
        _check_permissions(_bot(teams=['legal']), revoked)
    assert _allowed_teams(_bot(), revoked) == []


def test_check_permissions_denies_a_team_not_on_the_allowlist():
    try:
        _check_permissions(_bot(teams=['legal']), ChatUser(team='sales'))
        assert False, 'expected BotPermissionDenied'
    except BotPermissionDenied as exc:
        assert exc.bot_id == 'test-bot'
        assert exc.user_team == 'sales'


def test_check_permissions_denies_a_missing_team_when_bot_restricts_teams():
    try:
        _check_permissions(_bot(teams=['legal']), ChatUser(team=None))
        assert False, 'expected BotPermissionDenied'
    except BotPermissionDenied:
        pass


# --- _allowed_teams ------------------------------------------------------------


def test_allowed_teams_prefers_the_users_own_team_over_bot_permissions():
    bot = _bot(teams=['legal', 'management'])
    assert _allowed_teams(bot, ChatUser(team='legal')) == ['legal']


def test_allowed_teams_falls_back_to_bot_permissions_when_user_has_no_team():
    bot = _bot(teams=['legal', 'management'])
    assert _allowed_teams(bot, ChatUser(team=None)) == ['legal', 'management']


def test_allowed_teams_translates_empty_bot_permissions_to_unrestricted_none():
    # bot.permissions.teams == [] means "every team may use this bot" (see
    # app/schemas/bot.py's PermissionsConfig docstring) -- forwarded as-is
    # it would instead mean Weave-Retrieval's own "zero teams authorized,
    # zero results" (see app/services/retrieval_client.py). Must become
    # `None`, never `[]`.
    bot = _bot(teams=[])
    assert _allowed_teams(bot, ChatUser(team=None)) is None


# --- resolve_collection_scope --------------------------------------------------
#
# `_bot_with_collections` defaults `include_uncollected=True` (matching
# RetrievalConfig's own default), so every test below that expects a
# non-empty REAL scope also expects NO_COLLECTION_SENTINEL appended to it --
# that append is the whole point of the fix (see app/schemas/bot.py's
# `include_uncollected` docstring and resolve_collection_scope's own). Tests
# that pin `include_uncollected=False` exist separately below to cover the
# strict, sentinel-free opposite.


def test_resolve_collection_scope_intersects_bot_collections_with_readable_collections():
    bot = _bot_with_collections(['vertraege', 'handbuch'])
    readable = [_collection('vertraege'), _collection('personal')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable) as mock_list:
        scope = resolve_collection_scope(bot, ChatUser(team='legal'))

    mock_list.assert_called_once_with('legal')
    # 'handbuch' is on the bot's own list but not readable by 'legal';
    # 'personal' is readable but not on the bot's own list -- neither
    # belongs in the intersection, only 'vertraege' does. The sentinel is
    # appended on top of that real intersection, not instead of it.
    assert scope == ['vertraege', NO_COLLECTION_SENTINEL]


def test_resolve_collection_scope_returns_every_readable_collection_when_the_bot_lists_none():
    # RetrievalConfig.collections' own docstring: "Leere Bot-Liste = alle
    # lesbaren" -- Collections contract point 6.
    bot = _bot_with_collections([])
    readable = [_collection('vertraege'), _collection('handbuch', public=True)]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'))
    assert scope == ['vertraege', 'handbuch', NO_COLLECTION_SENTINEL]


def test_resolve_collection_scope_is_empty_when_the_user_cannot_read_the_bots_collection():
    # The bot names its OWN Collections restriction (['vertraege']), so the
    # one include_uncollected exception (see resolve_collection_scope's own
    # docstring) never applies here, regardless of its value -- an empty
    # real intersection for a Collections-bound bot always guards.
    bot = _bot_with_collections(['vertraege'])
    readable = [_collection('handbuch', public=True)]  # 'vertraege' not among these
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='sales'))
    assert scope == []


def test_resolve_collection_scope_guards_on_an_empty_real_intersection_despite_the_sentinel():
    # Regression for the exact bug this fix targets: appending the sentinel
    # must never turn an otherwise-empty real scope into a non-empty one for
    # a bot that DOES name its own Collections restriction -- only the one
    # documented exception (no bot restriction AND no readable collections
    # AND include_uncollected) may do that, and this bot doesn't qualify
    # (non-empty `collections`).
    bot = _bot_with_collections(['vertraege'], include_uncollected=True)
    with patch('app.services.chat.retrieval_client.list_collections', return_value=[]):
        scope = resolve_collection_scope(bot, ChatUser(team='sales'))
    assert scope == []


def test_resolve_collection_scope_allows_a_pure_legacy_search_when_nothing_else_is_readable():
    # The one narrow exception: no bot-side restriction, no caller-side
    # readable collections at all, but include_uncollected=True -- there is
    # no real Collections axis in play, so an Altbestand-only search
    # (sentinel alone) is allowed instead of guarding.
    bot = _bot_with_collections([], include_uncollected=True)
    with patch('app.services.chat.retrieval_client.list_collections', return_value=[]):
        scope = resolve_collection_scope(bot, ChatUser(team=None))
    assert scope == [NO_COLLECTION_SENTINEL]


def test_resolve_collection_scope_is_empty_when_the_caller_can_read_nothing_and_uncollected_is_excluded():
    # Same setup as the pure-legacy-search test above, but with
    # include_uncollected=False: the exception is gated on that flag, so
    # without it this reverts to the ordinary "nothing to search" guard case.
    bot = _bot_with_collections([], include_uncollected=False)
    with patch('app.services.chat.retrieval_client.list_collections', return_value=[]):
        scope = resolve_collection_scope(bot, ChatUser(team=None))
    assert scope == []


def test_resolve_collection_scope_forwards_the_users_team_to_list_collections():
    bot = _bot_with_collections([])
    with patch('app.services.chat.retrieval_client.list_collections', return_value=[]) as mock_list:
        resolve_collection_scope(bot, ChatUser(team=None))
    mock_list.assert_called_once_with(None)


# --- resolve_collection_scope: include_uncollected -----------------------------


def test_resolve_collection_scope_appends_the_sentinel_when_include_uncollected_is_true():
    bot = _bot_with_collections(['vertraege'], include_uncollected=True)
    readable = [_collection('vertraege')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'))
    assert scope == ['vertraege', NO_COLLECTION_SENTINEL]


def test_resolve_collection_scope_omits_the_sentinel_when_include_uncollected_is_false():
    # Altbestand (documents with no collection at all) must NOT be visible
    # once an operator has opted out via include_uncollected=False -- strict
    # Collections-only visibility, no sentinel appended.
    bot = _bot_with_collections(['vertraege'], include_uncollected=False)
    readable = [_collection('vertraege')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'))
    assert scope == ['vertraege']


# --- resolve_collection_scope: per-request Collections filter ------------------
#
# "Collection-Filter pro Anfrage": `requested_collections` (`ChatRequest.
# collections` verbatim) is ALWAYS applied strictly after the bot/rights
# intersection above, and can only ever narrow it further -- never add
# anything that intersection did not already grant. See
# `_apply_collections_filter`'s own docstring (app/services/chat.py) for the
# exact mechanics (plain set intersection, no special-casing needed even for
# the sentinel).


def test_resolve_collection_scope_no_filter_is_byte_for_byte_the_old_behaviour():
    # Regression test: passing requested_collections=None explicitly (the
    # ChatRequest.collections default) must resolve exactly like every
    # pre-existing call site that never knew this parameter existed at all
    # (i.e. every test above this section, all of which omit it).
    bot = _bot_with_collections(['vertraege', 'handbuch'])
    readable = [_collection('vertraege'), _collection('handbuch')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        without_the_param = resolve_collection_scope(bot, ChatUser(team='legal'))
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        with_none_explicit = resolve_collection_scope(bot, ChatUser(team='legal'), None)
    assert with_none_explicit == without_the_param == ['vertraege', 'handbuch', NO_COLLECTION_SENTINEL]


def test_resolve_collection_scope_filter_narrows_to_the_requested_slug():
    bot = _bot_with_collections(['vertraege', 'handbuch'])
    readable = [_collection('vertraege'), _collection('handbuch')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'), ['handbuch'])
    # The sentinel (present in the unfiltered scope, include_uncollected
    # defaults True) falls away too -- the caller asked for exactly
    # 'handbuch', not Altbestand alongside it.
    assert scope == ['handbuch']


def test_resolve_collection_scope_filter_silently_drops_a_foreign_slug_and_keeps_the_rest():
    # A slug the filter names that this caller/bot was never entitled to
    # (here: never even part of the bot's own 'vertraege'/'handbuch' list)
    # is dropped without a trace -- the rest of the filter still resolves
    # normally, exactly as if the foreign slug had never been named.
    bot = _bot_with_collections(['vertraege', 'handbuch'])
    readable = [_collection('vertraege'), _collection('handbuch')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'), ['handbuch', 'geheimprojekt'])
    assert scope == ['handbuch']


def test_resolve_collection_scope_filter_naming_only_foreign_slugs_empties_the_scope():
    bot = _bot_with_collections(['vertraege', 'handbuch'])
    readable = [_collection('vertraege'), _collection('handbuch')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'), ['geheimprojekt'])
    assert scope == []


def test_resolve_collection_scope_filter_never_widens_even_with_an_empty_bot_collections_list():
    # bot_collections=[] means "every readable collection" (RetrievalConfig.
    # collections' own "Leere Bot-Liste = alle lesbaren" docstring) -- a
    # filter naming a slug the caller cannot even read must still never make
    # it into the result, regardless of how permissive the bot's own list is.
    bot = _bot_with_collections([])
    readable = [_collection('vertraege'), _collection('handbuch')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'), ['vertraege', 'unlesbar'])
    assert scope == ['vertraege']


def test_resolve_collection_scope_filter_with_an_explicit_empty_list_matches_nothing():
    # `[]` is a real, distinct filter ("match nothing"), not the same as
    # omitting `collections` entirely (`None`) -- see ChatRequest.
    # collections' own docstring.
    bot = _bot_with_collections(['vertraege'])
    readable = [_collection('vertraege')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'), [])
    assert scope == []


def test_resolve_collection_scope_filter_can_narrow_down_to_the_sentinel_alone():
    # The filter naming NO_COLLECTION_SENTINEL itself needs no special-
    # casing: it's just one more member of the unfiltered scope, kept by the
    # same plain intersection as any real slug -- here the caller wants
    # ONLY the Altbestand-legacy documents, not 'vertraege' alongside them.
    bot = _bot_with_collections(['vertraege'], include_uncollected=True)
    readable = [_collection('vertraege')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'), [NO_COLLECTION_SENTINEL])
    assert scope == [NO_COLLECTION_SENTINEL]


def test_resolve_collection_scope_filter_cannot_grant_the_sentinel_rights_never_included():
    # include_uncollected=False -> the sentinel was never part of the
    # unfiltered scope in the first place -- the filter naming it explicitly
    # must not be able to grant Altbestand visibility rights never resolved.
    bot = _bot_with_collections(['vertraege'], include_uncollected=False)
    readable = [_collection('vertraege')]
    with patch('app.services.chat.retrieval_client.list_collections', return_value=readable):
        scope = resolve_collection_scope(bot, ChatUser(team='legal'), [NO_COLLECTION_SENTINEL])
    assert scope == []


# --- _apply_collections_filter --------------------------------------------------


def test_apply_collections_filter_returns_the_rights_scope_unchanged_when_no_filter_is_given():
    from app.services.chat import _apply_collections_filter

    assert _apply_collections_filter(['a', 'b'], None) == ['a', 'b']


def test_apply_collections_filter_preserves_the_rights_scopes_own_order():
    from app.services.chat import _apply_collections_filter

    assert _apply_collections_filter(['a', 'b', 'c'], ['c', 'a']) == ['a', 'c']


# --- _context_block / _score_for / _to_source ---------------------------------


def test_context_block_numbers_sections_with_source_and_page():
    chunks = [
        _chunk(chunk_id=1, source='confluence', page_start=3, page_end=4, text='first'),
        _chunk(chunk_id=2, source='sharepoint', page_start=10, page_end=10, text='second'),
    ]
    block = _context_block(chunks)
    assert '[source 1] confluence, p. 3-4\nfirst' in block
    assert '[source 2] sharepoint, p. 10\nsecond' in block  # no "-10" repeat when start == end


def test_context_block_falls_back_to_document_id_without_a_source_and_no_page_without_page_start():
    chunk = _chunk(source=None, page_start=None, page_end=None, document_id='doc-42', text='body')
    block = _context_block([chunk])
    assert block == '[source 1] doc-42\nbody'


def test_score_for_prefers_rerank_over_rrf():
    chunk = _chunk(scores=RetrievedChunkScores(rrf=0.4, rerank=0.9))
    assert _score_for(chunk) == 0.9


def test_score_for_falls_back_to_rrf_when_rerank_is_absent():
    chunk = _chunk(scores=RetrievedChunkScores(rrf=0.4, rerank=None))
    assert _score_for(chunk) == 0.4


def test_to_source_defaults_missing_source_to_empty_string():
    chunk = _chunk(source=None)
    source = _to_source(chunk)
    assert source.source == ''
    assert source.document_id == chunk.document_id
    assert source.chunk_id == chunk.chunk_id


def test_to_source_propagates_the_chunk_s_collection():
    source = _to_source(_chunk(collection='vertraege'))
    assert source.collection == 'vertraege'


def test_to_source_defaults_missing_collection_to_none():
    source = _to_source(_chunk())
    assert source.collection is None


# --- _filter_n8n_sources_by_scope ----------------------------------------------
# See contracts/n8n-flow.md's "Quellen sind eine Behauptung, keine
# Berechtigung": n8n's own reported `sources` are checked against the exact
# scope signed into that turn's delegation token, AFTER the webhook call
# returns -- a source outside that scope is dropped, never the whole turn.


def _source(**overrides) -> Source:
    defaults = dict(source='confluence', document_id='doc-1', chunk_id=1, collection='vertraege')
    defaults.update(overrides)
    return Source(**defaults)


def test_filter_n8n_sources_keeps_a_source_whose_collection_is_in_scope():
    kept, dropped = _filter_n8n_sources_by_scope([_source(collection='vertraege')], ['vertraege'], bot_id='b')
    assert [s.collection for s in kept] == ['vertraege']
    assert dropped == 0


def test_filter_n8n_sources_drops_a_source_whose_collection_is_outside_scope():
    kept, dropped = _filter_n8n_sources_by_scope([_source(collection='geheim')], ['vertraege'], bot_id='b')
    assert kept == []
    assert dropped == 1


def test_filter_n8n_sources_keeps_a_collection_less_source_when_the_sentinel_is_in_scope():
    kept, dropped = _filter_n8n_sources_by_scope(
        [_source(collection=None)], ['vertraege', NO_COLLECTION_SENTINEL], bot_id='b'
    )
    assert len(kept) == 1
    assert dropped == 0


def test_filter_n8n_sources_drops_a_collection_less_source_when_the_sentinel_is_missing():
    # The Sentinel-Regel: a source claiming NO collection at all is only
    # believed when the signed scope itself grants Altbestand access --
    # otherwise it is dropped exactly like any out-of-scope real collection.
    kept, dropped = _filter_n8n_sources_by_scope([_source(collection=None)], ['vertraege'], bot_id='b')
    assert kept == []
    assert dropped == 1


def test_filter_n8n_sources_drops_everything_against_an_empty_scope():
    kept, dropped = _filter_n8n_sources_by_scope([_source(collection='vertraege')], [], bot_id='b')
    assert kept == []
    assert dropped == 1


def test_filter_n8n_sources_only_drops_the_offending_entries_preserving_order():
    sources = [_source(chunk_id=1, collection='vertraege'), _source(chunk_id=2, collection='geheim'), _source(chunk_id=3, collection='vertraege')]
    kept, dropped = _filter_n8n_sources_by_scope(sources, ['vertraege'], bot_id='b')
    assert [s.chunk_id for s in kept] == [1, 3]
    assert dropped == 1


def test_filter_n8n_sources_logs_a_warning_naming_the_bot_id_and_count_only(caplog):
    with caplog.at_level(logging.WARNING):
        _filter_n8n_sources_by_scope([_source(collection='geheim'), _source(collection='top-secret')], ['vertraege'], bot_id='legal-agent')

    messages = [record.getMessage() for record in caplog.records]
    assert any('legal-agent' in message and '2' in message for message in messages)
    for message in messages:
        assert 'geheim' not in message
        assert 'top-secret' not in message
        assert 'doc-1' not in message


def test_filter_n8n_sources_logs_nothing_when_nothing_is_dropped(caplog):
    with caplog.at_level(logging.WARNING):
        _filter_n8n_sources_by_scope([_source(collection='vertraege')], ['vertraege'], bot_id='legal-agent')
    assert caplog.records == []
