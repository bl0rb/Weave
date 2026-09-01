"""Unit tests for app/services/router.py's RULES and 'llm' classification
modes -- built directly against BotConfig instances (see test_bot_schema.py
for the same style), independent of the filesystem/botconfig.py layer.

The RULES-mode tests are exactly the task concept's own worked examples
(see docs/adr and the module-level task description this stage was built
from); the 'llm'-mode tests cover its three documented outcomes: a valid
JSON response, a JSON response wrapped in a markdown code fence, and a
broken (unparseable) response that must fall back to RULES with
`router_fallback=True`.
"""

from app.schemas.bot import BotConfig
from app.services.router import route

_BASE_BOT = {
    'id': 'test-bot',
    'name': 'Test Bot',
    'model': {'model': 'fake-chat'},
    'system_prompt': 'You are a bot.',
}


def _bot(*, retrieval_enabled: bool) -> BotConfig:
    return BotConfig.model_validate({**_BASE_BOT, 'retrieval': {'enabled': retrieval_enabled}})


# --- RULES mode: the task concept's own worked examples ---------------------


def test_greeting_is_conversational():
    decision = route('Hallo', _bot(retrieval_enabled=False))
    assert decision.intent == 'conversational'
    assert decision.needs_retrieval is False
    assert decision.needs_tool is False
    assert decision.confidence == 0.9


def test_knowledge_question_routes_to_knowledge_when_bot_has_retrieval():
    decision = route(
        'Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?',
        _bot(retrieval_enabled=True),
    )
    assert decision.intent == 'knowledge'
    assert decision.needs_retrieval is True
    assert decision.needs_tool is False


def test_action_imperative_routes_to_action():
    decision = route('Buche mir einen Flug nach München.', _bot(retrieval_enabled=False))
    assert decision.intent == 'action'
    assert decision.needs_tool is True
    assert decision.needs_retrieval is False
    assert decision.confidence == 0.9


def test_document_direct_processing_routes_to_document():
    decision = route('Fass dieses PDF zusammen.', _bot(retrieval_enabled=False))
    assert decision.intent == 'document'
    assert decision.needs_retrieval is False
    assert decision.needs_tool is False


def test_multi_step_comparison_routes_to_complex():
    decision = route(
        'Vergleiche die Regelungen aus Vertrag A und Vertrag B.',
        _bot(retrieval_enabled=False),
    )
    assert decision.intent == 'complex'
    assert decision.needs_tool is True


def test_complex_needs_retrieval_only_when_bot_has_retrieval_enabled():
    decision = route(
        'Vergleiche die Regelungen aus Vertrag A und Vertrag B.',
        _bot(retrieval_enabled=True),
    )
    assert decision.intent == 'complex'
    assert decision.needs_retrieval is True
    assert decision.needs_tool is True


def test_knowledge_like_question_without_retrieval_falls_back_to_conversational():
    decision = route('Wie funktioniert RAG?', _bot(retrieval_enabled=False))
    assert decision.intent == 'conversational'
    assert decision.needs_retrieval is False
    assert decision.confidence == 0.6


# --- RULES mode: a couple of supporting cases past the worked examples ------


def test_default_branch_confidence_is_lower_than_a_matched_pattern():
    matched = route('Hallo', _bot(retrieval_enabled=False))
    default = route('Wie funktioniert RAG?', _bot(retrieval_enabled=False))
    assert matched.confidence > default.confidence


def test_rules_decision_never_sets_router_fallback():
    decision = route('Hallo', _bot(retrieval_enabled=False))
    assert decision.router_fallback is False


# --- LLM mode ----------------------------------------------------------------


def test_llm_mode_valid_json_response():
    calls = []

    def fake_llm(messages, model):
        calls.append((messages, model))
        return '{"intent": "action", "confidence": 0.95}'

    decision = route('Buche einen Tisch für heute Abend.', _bot(retrieval_enabled=False), mode='llm', llm_call=fake_llm)

    assert decision.intent == 'action'
    assert decision.confidence == 0.95
    assert decision.needs_tool is True
    assert decision.router_fallback is False
    # The callable actually got invoked with (messages, model), not bypassed.
    assert len(calls) == 1
    messages, model = calls[0]
    assert model == 'fake-chat'
    assert any(msg['role'] == 'user' and 'Tisch' in msg['content'] for msg in messages)


def test_llm_mode_json_wrapped_in_code_fence():
    def fake_llm(messages, model):
        return '```json\n{"intent": "knowledge", "confidence": 0.8}\n```'

    decision = route('Irgendeine Wissensfrage.', _bot(retrieval_enabled=True), mode='llm', llm_call=fake_llm)

    assert decision.intent == 'knowledge'
    assert decision.confidence == 0.8
    assert decision.needs_retrieval is True
    assert decision.router_fallback is False


def test_llm_mode_broken_json_falls_back_to_rules_with_flag():
    def fake_llm(messages, model):
        return 'this is not json at all'

    decision = route('Hallo', _bot(retrieval_enabled=False), mode='llm', llm_call=fake_llm)

    assert decision.intent == 'conversational'
    assert decision.router_fallback is True


def test_llm_mode_unknown_intent_in_response_falls_back_to_rules():
    def fake_llm(messages, model):
        return '{"intent": "not-a-real-intent", "confidence": 0.9}'

    decision = route('Buche mir einen Flug nach München.', _bot(retrieval_enabled=False), mode='llm', llm_call=fake_llm)

    assert decision.intent == 'action'  # RULES fallback still classifies this correctly
    assert decision.router_fallback is True


def test_llm_mode_provider_exception_falls_back_to_rules():
    def raising_llm(messages, model):
        raise RuntimeError('provider unreachable')

    decision = route('Hallo', _bot(retrieval_enabled=False), mode='llm', llm_call=raising_llm)

    assert decision.intent == 'conversational'
    assert decision.router_fallback is True


def test_llm_mode_without_llm_call_falls_back_to_rules():
    decision = route('Hallo', _bot(retrieval_enabled=False), mode='llm', llm_call=None)

    assert decision.intent == 'conversational'
    assert decision.router_fallback is True
