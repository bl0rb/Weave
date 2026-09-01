"""Unit tests for app/services/botconfig.py against isolated tmp_path bot
directories -- deliberately NOT the real bots/ directory (that round trip is
test_health.py's/test_internal_api.py's job) so these can freely write
broken/edge-case fixture files without touching the shipped examples.
"""

import pytest

from app.core.config import settings
from app.services.botconfig import BotConfigError, BotNotFoundError, list_bots, load_bot

_MINIMAL_BOT = """
id: minimal
name: Minimal Bot
model:
  model: fake-chat
system_prompt: You are a bot.
"""


@pytest.fixture
def bots_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'bots_dir', str(tmp_path))
    return tmp_path


def test_list_bots_on_empty_directory_returns_empty_list(bots_dir):
    assert list_bots() == []


def test_list_bots_ignores_non_yaml_files(bots_dir):
    (bots_dir / 'minimal.yaml').write_text(_MINIMAL_BOT, encoding='utf-8')
    (bots_dir / 'README.md').write_text('not a bot config', encoding='utf-8')
    (bots_dir / '.gitkeep').write_text('', encoding='utf-8')

    bots = list_bots()
    assert len(bots) == 1
    assert bots[0].id == 'minimal'


def test_list_bots_accepts_both_yaml_and_yml_extensions(bots_dir):
    (bots_dir / 'a.yaml').write_text(_MINIMAL_BOT.replace('minimal', 'bot-a'), encoding='utf-8')
    (bots_dir / 'b.yml').write_text(_MINIMAL_BOT.replace('minimal', 'bot-b'), encoding='utf-8')

    ids = {bot.id for bot in list_bots()}
    assert ids == {'bot-a', 'bot-b'}


def test_list_bots_raises_bot_config_error_naming_the_broken_file(bots_dir):
    (bots_dir / 'ok.yaml').write_text(_MINIMAL_BOT, encoding='utf-8')
    (bots_dir / 'broken.yaml').write_text('id: broken\n', encoding='utf-8')  # missing required fields

    with pytest.raises(BotConfigError) as exc_info:
        list_bots()
    assert 'broken.yaml' in str(exc_info.value)


def test_list_bots_rejects_a_non_mapping_top_level_document(bots_dir):
    (bots_dir / 'list.yaml').write_text('- just\n- a\n- list\n', encoding='utf-8')

    with pytest.raises(BotConfigError) as exc_info:
        list_bots()
    assert 'list.yaml' in str(exc_info.value)


def test_list_bots_rejects_an_invalid_slug_id(bots_dir):
    (bots_dir / 'bad-id.yaml').write_text(_MINIMAL_BOT.replace('id: minimal', 'id: Not A Slug!'), encoding='utf-8')

    with pytest.raises(BotConfigError) as exc_info:
        list_bots()
    assert 'bad-id.yaml' in str(exc_info.value)


def test_list_bots_rejects_unknown_fields(bots_dir):
    (bots_dir / 'typo.yaml').write_text(_MINIMAL_BOT + 'desciption: typo of description\n', encoding='utf-8')

    with pytest.raises(BotConfigError):
        list_bots()


def test_load_bot_returns_matching_bot(bots_dir):
    (bots_dir / 'minimal.yaml').write_text(_MINIMAL_BOT, encoding='utf-8')
    bot = load_bot('minimal')
    assert bot.id == 'minimal'
    assert bot.name == 'Minimal Bot'


def test_load_bot_matches_by_config_id_not_filename(bots_dir):
    # Filename deliberately does not match the id inside it.
    (bots_dir / 'some-file-name.yaml').write_text(_MINIMAL_BOT, encoding='utf-8')
    bot = load_bot('minimal')
    assert bot.id == 'minimal'


def test_load_bot_raises_bot_not_found_error_for_unknown_id(bots_dir):
    (bots_dir / 'minimal.yaml').write_text(_MINIMAL_BOT, encoding='utf-8')
    with pytest.raises(BotNotFoundError):
        load_bot('does-not-exist')


def test_bot_not_found_error_is_a_key_error(bots_dir):
    assert issubclass(BotNotFoundError, KeyError)


def test_list_bots_raises_os_error_for_missing_bots_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'bots_dir', str(tmp_path / 'does-not-exist'))
    with pytest.raises(OSError):
        list_bots()


# --- n8n webhook_url SSRF allowlist (app/services/botconfig.py's
# _validate_n8n_webhook_allowlist), checked at LOAD time -- see that
# function's own docstring for why this lives here rather than as a
# pydantic validator (it needs settings.n8n_allowed_base_urls, which
# app/schemas/bot.py deliberately never imports).

_N8N_BOT = """
id: n8n-bot
name: n8n Bot
model:
  provider: n8n
  model: n8n-agent-flow
system_prompt: You delegate to n8n.
n8n:
  webhook_url: https://n8n.internal.example.com/webhook/agent
"""


def test_list_bots_rejects_an_n8n_bot_when_the_allowlist_is_empty(bots_dir, monkeypatch):
    # N8N_ALLOWED_BASE_URLS' own documented default/behaviour: an empty
    # allowlist disables every n8n-provider bot outright, not just ones
    # with an obviously-bogus URL.
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', [])
    (bots_dir / 'n8n-bot.yaml').write_text(_N8N_BOT, encoding='utf-8')

    with pytest.raises(BotConfigError) as exc_info:
        list_bots()
    assert 'n8n-bot.yaml' in str(exc_info.value)
    assert 'N8N_ALLOWED_BASE_URLS' in str(exc_info.value)


def test_list_bots_rejects_an_n8n_bot_whose_webhook_url_matches_no_allowed_base(bots_dir, monkeypatch):
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://some-other-n8n.example.com/'])
    (bots_dir / 'n8n-bot.yaml').write_text(_N8N_BOT, encoding='utf-8')

    with pytest.raises(BotConfigError) as exc_info:
        list_bots()
    assert 'n8n-bot.yaml' in str(exc_info.value)


def test_list_bots_accepts_an_n8n_bot_whose_webhook_url_starts_with_an_allowed_base(bots_dir, monkeypatch):
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com/webhook/'])
    (bots_dir / 'n8n-bot.yaml').write_text(_N8N_BOT, encoding='utf-8')

    bots = list_bots()
    assert len(bots) == 1
    assert bots[0].n8n.webhook_url == 'https://n8n.internal.example.com/webhook/agent'


def test_list_bots_accepts_an_n8n_bot_when_one_of_several_allowed_bases_matches(bots_dir, monkeypatch):
    monkeypatch.setattr(
        settings,
        'n8n_allowed_base_urls',
        ['https://unrelated.example.com/', 'https://n8n.internal.example.com/webhook/'],
    )
    (bots_dir / 'n8n-bot.yaml').write_text(_N8N_BOT, encoding='utf-8')
    assert len(list_bots()) == 1


# --- SSRF allowlist: scheme/host/port compared EXACTLY, never as a raw
# string prefix (app/services/botconfig.py's `_base_url_matches`) -- each
# test below is one concrete bypass a plain `str.startswith()` comparison
# used to fall for, now that the allowlist entry and the bot's own
# webhook_url are both parsed with `urllib.parse` first. `_write_n8n_bot`
# writes a fresh bot file with an arbitrary `webhook_url` so each test can
# exercise its own exact URL pair without fighting `_N8N_BOT`'s fixed one.


def _write_n8n_bot(bots_dir, webhook_url: str) -> None:
    (bots_dir / 'n8n-bot.yaml').write_text(
        f'id: n8n-bot\n'
        f'name: n8n Bot\n'
        f'model:\n'
        f'  provider: n8n\n'
        f'  model: n8n-agent-flow\n'
        f'system_prompt: You delegate to n8n.\n'
        f'n8n:\n'
        f'  webhook_url: {webhook_url}\n',
        encoding='utf-8',
    )


def test_list_bots_rejects_a_webhook_url_with_an_appended_domain_suffix(bots_dir, monkeypatch):
    # 'n8n.internal.example.com' is a STRING prefix of
    # 'n8n.internal.example.com.evil.example', but a completely different,
    # attacker-controlled host -- a bare str.startswith() would have let
    # this through.
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com/webhook/'])
    _write_n8n_bot(bots_dir, 'https://n8n.internal.example.com.evil.example/webhook/agent')

    with pytest.raises(BotConfigError) as exc_info:
        list_bots()
    assert 'n8n-bot.yaml' in str(exc_info.value)


def test_list_bots_rejects_a_webhook_url_with_an_appended_port_suffix(bots_dir, monkeypatch):
    # '5678' is a numeric STRING prefix of '56789', but a different port --
    # only an exact integer comparison of `.port` catches this.
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com:5678/webhook/'])
    _write_n8n_bot(bots_dir, 'https://n8n.internal.example.com:56789/webhook/agent')

    with pytest.raises(BotConfigError):
        list_bots()


def test_list_bots_rejects_a_webhook_url_with_userinfo_smuggling_a_different_host(bots_dir, monkeypatch):
    # 'https://n8n.internal.example.com@evil.example/...' textually STARTS
    # WITH the allowed base, but per RFC 3986 userinfo syntax its actual
    # host is 'evil.example' -- `urlsplit(...).hostname` already strips the
    # userinfo prefix, which is exactly what makes this rejection work.
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com/webhook/'])
    _write_n8n_bot(bots_dir, 'https://n8n.internal.example.com@evil.example/webhook/agent')

    with pytest.raises(BotConfigError):
        list_bots()


def test_list_bots_rejects_a_webhook_url_with_a_different_scheme(bots_dir, monkeypatch):
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com/webhook/'])
    _write_n8n_bot(bots_dir, 'http://n8n.internal.example.com/webhook/agent')

    with pytest.raises(BotConfigError):
        list_bots()


def test_list_bots_accepts_a_webhook_url_whose_host_differs_only_in_case(bots_dir, monkeypatch):
    # The inverse of a bypass: a same-host-different-case webhook_url must
    # not be REJECTED just because a naive comparison is case-sensitive --
    # `urlsplit(...).hostname` is already lowercased on both sides.
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com/webhook/'])
    _write_n8n_bot(bots_dir, 'https://N8N.Internal.Example.COM/webhook/agent')

    bots = list_bots()
    assert len(bots) == 1


def test_list_bots_accepts_an_allowed_base_without_a_trailing_slash(bots_dir, monkeypatch):
    # A trailing path separator on the configured base is no longer
    # required for a safe match -- see N8N_ALLOWED_BASE_URLS' own updated
    # docstring in app/core/config.py.
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com:5678'])
    _write_n8n_bot(bots_dir, 'https://n8n.internal.example.com:5678/webhook/agent')

    bots = list_bots()
    assert len(bots) == 1


def test_list_bots_rejects_a_webhook_url_whose_path_extends_the_allowed_path_without_a_separator(
    bots_dir, monkeypatch
):
    # The path-prefix check still respects a boundary at '/' even without a
    # trailing slash on the allowed base: '/webhook-evil' must not count as
    # being "inside" '/webhook'.
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com/webhook'])
    _write_n8n_bot(bots_dir, 'https://n8n.internal.example.com/webhook-evil/agent')

    with pytest.raises(BotConfigError):
        list_bots()


def test_bot_files_ignore_yaml_example_suffixed_files(bots_dir):
    # Regression guard, deliberate design decision: bots/n8n-agent.yaml.example
    # relies on exactly this -- ".example" is not ".yaml"/".yml", so
    # _bot_files()'s own suffix filter (already covered generically by
    # test_list_bots_ignores_non_yaml_files above) excludes it, meaning it
    # is never loaded/validated as a real bot regardless of its content.
    (bots_dir / 'minimal.yaml').write_text(_MINIMAL_BOT, encoding='utf-8')
    # Deliberately invalid content (missing every required field) -- if this
    # were picked up at all, list_bots() would raise; it must not be.
    (bots_dir / 'n8n-agent.yaml.example').write_text('id: n8n-agent\n', encoding='utf-8')

    bots = list_bots()
    assert len(bots) == 1
    assert bots[0].id == 'minimal'


@pytest.mark.parametrize('path', [
    '/webhook/../../admin',
    '/webhook/./../admin',
    '/webhook/sub/../../../internal',
])
def test_allowlist_rejects_dot_segments_in_the_path(path):
    # httpx normalises dot-segments before sending, so a path-scoped
    # allowlist entry would otherwise be satisfied by a URL that ends up
    # somewhere else entirely.
    from app.services.botconfig import _base_url_matches

    assert _base_url_matches(
        'https://n8n.internal:5678' + path, 'https://n8n.internal:5678/webhook'
    ) is False


@pytest.mark.parametrize('path', [
    '/webhook/%2e%2e/admin',
    '/webhook/%2E%2E/admin',
    '/webhook/%2e%2e%2fadmin',
    '/webhook%2f..%2f..%2fadmin',
])
def test_allowlist_rejects_percent_encoded_dot_segments_in_the_path(path):
    # Same rationale as the plain-dot-segment case above, for the
    # percent-encoded form: the RAW path never spells out a literal '.' or
    # '..' segment ('%2e%2e' has no bare dot in it), but httpx's own
    # `.path` property decodes it right back into '/webhook/../admin'
    # before anything downstream sees it -- verified experimentally against
    # the installed httpx (see app/services/botconfig.py's
    # `_base_url_matches` docstring). A path-scoped allowlist entry that
    # only holds until a receiver decodes-and-normalises the same way is no
    # scope at all, exactly like the un-encoded case.
    from app.services.botconfig import _base_url_matches

    assert _base_url_matches(
        'https://n8n.internal:5678' + path, 'https://n8n.internal:5678/webhook'
    ) is False


# --- Parser-divergence hardening (app/services/botconfig.py's
# `_split_or_none`): reject backslashes and raw control characters outright
# rather than trusting today's agreement between `urlsplit()` (RFC 3986) and
# whatever actually opens the URL (httpx) to hold forever -- see that
# function's own docstring for the sibling bug (Weave-API's `return_to`
# allowlist, ported from this exact function) that motivated this. Verified
# experimentally that httpx does NOT currently diverge from `urlsplit()` on
# either character class -- these tests exercise the new input-validation
# layer directly (via `_split_or_none`) rather than relying on a host
# mismatch to already make `_base_url_matches` return False for some other
# reason.


@pytest.mark.parametrize('url', [
    r'https://n8n.internal:5678/webhook\evil',  # single backslash in the path
    r'https://n8n.internal:5678\evil/webhook',  # single backslash in the netloc
    r'https://n8n.internal:5678\\evil/webhook',  # multiple (doubled) backslashes
    r'https://n8n.internal:5678\\\evil\webhook',  # multiple, mixed positions
    r'https:\\n8n.internal:5678/webhook',  # backslash in place of the scheme's slashes
    r'https:/\n8n.internal:5678/webhook',  # mixed '/' and '\' right after the scheme
    r'https:\/n8n.internal:5678/webhook',  # mixed the other way round
    r'https://evil.example\@n8n.internal:5678/webhook',  # backslash-fronted userinfo smuggling
])
def test_split_or_none_rejects_urls_containing_a_backslash(url):
    from app.services.botconfig import _split_or_none

    assert _split_or_none(url) is None


@pytest.mark.parametrize('char', ['\t', '\n', '\r', '\x00', '\x0b', '\x1f', '\x7f'])
def test_split_or_none_rejects_urls_containing_control_characters(char):
    # '\t \n \r' are the three WHATWG explicitly strips before parsing (the
    # exact divergence found in the sibling Weave-API bug); the others
    # confirm the check isn't narrowed to just those three.
    from app.services.botconfig import _split_or_none

    assert _split_or_none(f'https://n8n.internal:5678/web{char}hook') is None


def test_split_or_none_still_accepts_an_ordinary_url():
    # Guard against the new check being over-broad -- a plain webhook_url
    # with none of the rejected characters must keep parsing normally.
    from app.services.botconfig import _split_or_none

    result = _split_or_none('https://n8n.internal:5678/webhook/agent')
    assert result is not None
    assert result.hostname == 'n8n.internal'
    assert result.port == 5678


def test_list_bots_rejects_a_webhook_url_containing_a_backslash(bots_dir, monkeypatch):
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com/webhook/'])
    _write_n8n_bot(bots_dir, r'https://evil.example\@n8n.internal.example.com/webhook/agent')

    with pytest.raises(BotConfigError):
        list_bots()


def test_list_bots_rejects_a_webhook_url_with_percent_encoded_dot_segments(bots_dir, monkeypatch):
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.internal.example.com/webhook'])
    _write_n8n_bot(bots_dir, 'https://n8n.internal.example.com/webhook/%2e%2e/admin')

    with pytest.raises(BotConfigError):
        list_bots()
