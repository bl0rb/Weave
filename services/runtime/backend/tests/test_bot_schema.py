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


# --- agent mode / AgentConfig / SubagentConfig cross-field validation -----

_ONE_SUBAGENT = {
    'id': 'it-support',
    'name': 'IT Support',
    'mission': 'Answer IT/helpdesk questions.',
    'collections': ['it-docs'],
}


def test_agent_mode_defaults_to_disabled_with_no_subagents():
    bot = BotConfig.model_validate(_MINIMAL)
    assert bot.agent is None


def test_agent_mode_is_rejected_without_tool_support():
    # `model.provider == 'fake'` is the one exception -- FakeLLM always
    # implements chat_with_tools -- so this uses a non-fake provider with
    # `supports_tools` left at its own default (None == unknown/unsupported).
    with pytest.raises(ValidationError, match='tool-call support'):
        BotConfig.model_validate({
            **_MINIMAL,
            'model': {'provider': 'openai', 'model': 'gpt-x'},
            'agent': {'enabled': True, 'subagents': [_ONE_SUBAGENT]},
        })


def test_agent_mode_is_accepted_when_model_declares_tool_support():
    bot = BotConfig.model_validate({
        **_MINIMAL,
        'model': {'provider': 'openai', 'model': 'gpt-x', 'supports_tools': True},
        'agent': {'enabled': True, 'subagents': [_ONE_SUBAGENT]},
    })
    assert bot.agent.enabled is True
    assert bot.agent.subagents[0].id == 'it-support'


def test_agent_mode_is_accepted_for_the_fake_provider_regardless_of_supports_tools():
    bot = BotConfig.model_validate({
        **_MINIMAL,
        'agent': {'enabled': True, 'subagents': [_ONE_SUBAGENT]},
    })
    assert bot.model.supports_tools is None
    assert bot.agent.enabled is True


def test_agent_mode_is_rejected_for_n8n_provider():
    with pytest.raises(ValidationError, match="n8n"):
        BotConfig.model_validate({
            **_MINIMAL,
            'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'},
            'n8n': {'webhook_url': 'https://n8n.example.test/webhook/agent'},
            'agent': {'enabled': True, 'subagents': [_ONE_SUBAGENT]},
        })


def test_agent_mode_is_rejected_when_a_subagents_own_model_override_lacks_tool_support():
    # SubagentConfig.model is optional and falls back to the main bot's own
    # (already tool-capable) model when unset -- but a subagent configured
    # with its OWN model override needs the identical check, or it silently
    # answers with zero tool calls at runtime instead of failing at load time.
    with pytest.raises(ValidationError, match="it-support.*tool-call support"):
        BotConfig.model_validate({
            **_MINIMAL,
            'agent': {
                'enabled': True,
                'subagents': [{**_ONE_SUBAGENT, 'model': {'provider': 'openai', 'model': 'gpt-x'}}],
            },
        })


def test_agent_mode_is_accepted_when_a_subagents_own_model_override_declares_tool_support():
    bot = BotConfig.model_validate({
        **_MINIMAL,
        'agent': {
            'enabled': True,
            'subagents': [
                {**_ONE_SUBAGENT, 'model': {'provider': 'openai', 'model': 'gpt-x', 'supports_tools': True}},
            ],
        },
    })
    assert bot.agent.subagents[0].model.supports_tools is True


def test_agent_enabled_requires_at_least_one_subagent():
    with pytest.raises(ValidationError, match='at least one subagent'):
        BotConfig.model_validate({**_MINIMAL, 'agent': {'enabled': True, 'subagents': []}})


def test_agent_disabled_with_no_subagents_is_fine():
    bot = BotConfig.model_validate({**_MINIMAL, 'agent': {'enabled': False}})
    assert bot.agent.enabled is False
    assert bot.agent.subagents == []


def test_subagent_ids_must_be_unique():
    with pytest.raises(ValidationError, match='unique'):
        BotConfig.model_validate({
            **_MINIMAL,
            'agent': {'enabled': True, 'subagents': [_ONE_SUBAGENT, _ONE_SUBAGENT]},
        })


def test_subagent_requires_collections_or_include_uncollected():
    with pytest.raises(ValidationError, match='include_uncollected'):
        BotConfig.model_validate({
            **_MINIMAL,
            'agent': {
                'enabled': True,
                'subagents': [{**_ONE_SUBAGENT, 'collections': []}],
            },
        })


def test_subagent_with_no_collections_is_fine_when_include_uncollected():
    bot = BotConfig.model_validate({
        **_MINIMAL,
        'agent': {
            'enabled': True,
            'subagents': [{**_ONE_SUBAGENT, 'collections': [], 'include_uncollected': True}],
        },
    })
    assert bot.agent.subagents[0].collections == []
    assert bot.agent.subagents[0].include_uncollected is True


def test_subagent_defaults():
    bot = BotConfig.model_validate({**_MINIMAL, 'agent': {'enabled': True, 'subagents': [_ONE_SUBAGENT]}})
    subagent = bot.agent.subagents[0]
    assert subagent.tools == ['search_knowledge']
    assert subagent.model is None
    assert subagent.include_uncollected is False
    assert subagent.limits.max_searches == 3
    assert subagent.limits.max_results == 5
    assert subagent.limits.timeout_seconds == 60
    assert bot.agent.limits.max_parallel == 3
    assert bot.agent.limits.max_followups == 1
    assert bot.agent.limits.budget_searches == 9
    assert bot.agent.limits.timeout_seconds == 120


def test_subagent_model_overrides_are_independent_of_the_bots_own_model():
    bot = BotConfig.model_validate({
        **_MINIMAL,
        'agent': {
            'enabled': True,
            'subagents': [{**_ONE_SUBAGENT, 'model': {'provider': 'fake', 'model': 'fake-small'}}],
        },
    })
    assert bot.agent.subagents[0].model.model == 'fake-small'
    assert bot.model.model == 'fake-chat'


def test_subagent_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        BotConfig.model_validate({
            **_MINIMAL,
            'agent': {'enabled': True, 'subagents': [{**_ONE_SUBAGENT, 'typo_field': 'x'}]},
        })
