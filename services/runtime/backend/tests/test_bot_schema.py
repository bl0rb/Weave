"""BotConfig and friends (app/schemas/bot.py) validated directly, independent
of the filesystem -- app/services/botconfig.py's own tests
(test_botconfig.py) cover the YAML-loading layer on top of this."""

import pytest
from pydantic import ValidationError

from app.schemas.bot import BotConfig, GuardConfig, ModelConfig, PermissionsConfig, RetrievalConfig

_MINIMAL = {
    'id': 'minimal',
    'name': 'Minimal Bot',
    'model': {'model': 'fake-chat'},
    'system_prompt': 'You are a bot.',
}


def test_minimal_config_applies_every_documented_default():
    bot = BotConfig.model_validate(_MINIMAL)

    assert bot.description is None
    assert bot.model.provider == 'fake'
    assert bot.model.temperature is None

    assert bot.retrieval == RetrievalConfig(enabled=False, top_k=20, final_k=5, rerank=True, collections=[])
    assert bot.retrieval.filters.team is None

    assert bot.permissions == PermissionsConfig(teams=[])
    assert bot.guard == GuardConfig(require_sources=True, no_context_reply='Ich habe dazu keine belegten Informationen gefunden.')


@pytest.mark.parametrize('bad_id', ['Not-A-Slug', 'has spaces', 'trailing-', '-leading', 'UPPER', ''])
def test_invalid_slug_ids_are_rejected(bad_id):
    with pytest.raises(ValidationError):
        BotConfig.model_validate({**_MINIMAL, 'id': bad_id})


@pytest.mark.parametrize('good_id', ['minimal', 'legal-support', 'a', 'bot-2', 'general-assistant'])
def test_valid_slug_ids_are_accepted(good_id):
    bot = BotConfig.model_validate({**_MINIMAL, 'id': good_id})
    assert bot.id == good_id


def test_missing_required_fields_are_rejected():
    with pytest.raises(ValidationError):
        BotConfig.model_validate({'id': 'minimal'})


def test_unknown_top_level_field_is_rejected():
    with pytest.raises(ValidationError):
        BotConfig.model_validate({**_MINIMAL, 'unexpected_field': 'value'})


def test_unknown_nested_field_is_rejected():
    with pytest.raises(ValidationError):
        BotConfig.model_validate({**_MINIMAL, 'guard': {'require_sources': False, 'typo_field': 'x'}})


def test_full_config_round_trips_every_field():
    full = {
        'id': 'legal-support',
        'name': 'Rechtsabteilung-Assistent',
        'description': 'Beantwortet Rechtsfragen.',
        'model': {'provider': 'openai', 'model': 'gpt-x', 'temperature': 0.2},
        'system_prompt': 'You answer legal questions.',
        'retrieval': {
            'enabled': True,
            'filters': {'department': 'legal', 'tags': ['policy']},
            'collections': ['vertraege'],
            'top_k': 15,
            'final_k': 3,
            'rerank': False,
        },
        'permissions': {'teams': ['legal', 'management']},
        'guard': {'require_sources': True, 'no_context_reply': 'Keine Quellen gefunden.'},
    }
    bot = BotConfig.model_validate(full)

    assert bot.model == ModelConfig(provider='openai', model='gpt-x', temperature=0.2)
    assert bot.retrieval.enabled is True
    assert bot.retrieval.filters.department == 'legal'
    assert bot.retrieval.filters.tags == ['policy']
    assert bot.retrieval.collections == ['vertraege']
    assert bot.retrieval.top_k == 15
    assert bot.retrieval.final_k == 3
    assert bot.retrieval.rerank is False
    assert bot.permissions.teams == ['legal', 'management']
    assert bot.guard.no_context_reply == 'Keine Quellen gefunden.'


# --- n8n provider / n8n: block cross-field validation --------------------


def test_n8n_provider_without_an_n8n_block_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        BotConfig.model_validate({**_MINIMAL, 'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'}})
    assert "n8n:" in str(exc_info.value)


def test_n8n_block_without_n8n_provider_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        BotConfig.model_validate({**_MINIMAL, 'n8n': {'webhook_url': 'https://n8n.example.test/webhook/agent'}})
    assert "n8n:" in str(exc_info.value)


def test_n8n_provider_with_a_matching_n8n_block_is_accepted():
    bot = BotConfig.model_validate(
        {
            **_MINIMAL,
            'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'},
            'n8n': {'webhook_url': 'https://n8n.example.test/webhook/agent'},
        }
    )
    assert bot.model.provider == 'n8n'
    assert bot.n8n.webhook_url == 'https://n8n.example.test/webhook/agent'
    assert bot.n8n.timeout_seconds == 120  # documented default


def test_n8n_block_timeout_seconds_can_be_overridden():
    bot = BotConfig.model_validate(
        {
            **_MINIMAL,
            'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'},
            'n8n': {'webhook_url': 'https://n8n.example.test/webhook/agent', 'timeout_seconds': 30},
        }
    )
    assert bot.n8n.timeout_seconds == 30


def test_n8n_block_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        BotConfig.model_validate(
            {
                **_MINIMAL,
                'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'},
                'n8n': {'webhook_url': 'https://n8n.example.test/webhook/agent', 'typo_field': 'x'},
            }
        )


def test_minimal_config_without_provider_n8n_has_no_n8n_block():
    # Regression guard: an ordinary bot's `n8n` field defaults to None and
    # is never implicitly required just because SOME other bot uses it.
    bot = BotConfig.model_validate(_MINIMAL)
    assert bot.n8n is None


def test_retrieval_filters_default_independently_per_instance():
    # Regression guard for mutable-default sharing: two BotConfigs built
    # from separate minimal dicts must not end up aliasing the same
    # RetrievalFilters/PermissionsConfig/RetrievalConfig.collections
    # instance.
    bot_a = BotConfig.model_validate(_MINIMAL)
    bot_b = BotConfig.model_validate(_MINIMAL)
    bot_a.permissions.teams.append('only-on-a')
    bot_a.retrieval.collections.append('only-on-a')
    assert bot_b.permissions.teams == []
    assert bot_b.retrieval.collections == []
