"""Unit tests for app/services/intake.py -- the intake state is derived
purely from the request history."""

from app.schemas.bot import BotConfig
from app.services import intake

_QUESTION = 'Um welche Beschäftigungsart geht es?'


def _bot(**intake_overrides) -> BotConfig:
    return BotConfig.model_validate({
        'id': 'fk', 'name': 'FK', 'model': {'model': 'fake-chat'}, 'system_prompt': 'p',
        'intake': {
            'question': _QUESTION, 'options': ['Festanstellung Teilzeit', 'Aushilfe'], 'label': 'Beschäftigungsart',
            **intake_overrides,
        },
    })


def _u(text):
    return {'role': 'user', 'content': text}


def _a(text):
    return {'role': 'assistant', 'content': text}


def test_no_intake_configured_is_none():
    bot = BotConfig.model_validate({'id': 'x', 'name': 'X', 'model': {'model': 'm'}, 'system_prompt': 'p'})
    assert intake.resolve(bot, 'Frage', []) is None


def test_unasked_is_pending():
    state = intake.resolve(_bot(), 'Wie viel Urlaub?', [_u('Hallo'), _a('Hallo!')])
    assert state.answer is None and state.retry is False and state.message == 'Wie viel Urlaub?'


def test_reply_to_the_question_continues_with_the_original_question():
    history = [_u('Hallo'), _a('Hallo!'), _u('Wie viel Urlaub?'), _a(_QUESTION)]
    state = intake.resolve(_bot(), 'festanstellung teilzeit', history)
    assert state.answer == 'Festanstellung Teilzeit'
    assert state.message == 'Wie viel Urlaub?'
    assert state.history == [_u('Hallo'), _a('Hallo!')]
    assert intake.context_line(_bot(), state.answer).endswith('Beschäftigungsart: Festanstellung Teilzeit')


def test_answer_is_remembered_on_later_turns():
    history = [_u('Wie viel Urlaub?'), _a(_QUESTION), _u('Aushilfe'), _a('30 Tage.')]
    state = intake.resolve(_bot(), 'Und Krankheit?', history)
    assert state.answer == 'Aushilfe' and state.message == 'Und Krankheit?' and state.history == history


def test_without_free_text_an_invalid_reply_re_asks_and_keeps_the_original_question():
    bot = _bot(allow_free_text=False)
    history = [_u('Wie viel Urlaub?'), _a(_QUESTION)]
    state = intake.resolve(bot, 'weiß nicht', history)
    assert state.answer is None and state.retry is True and state.message == 'Wie viel Urlaub?'
    # The next valid reply still answers the question asked first.
    history += [_u('weiß nicht'), _a(_QUESTION)]
    state = intake.resolve(bot, 'Aushilfe', history)
    assert state.answer == 'Aushilfe' and state.message == 'Wie viel Urlaub?' and state.history == []


def test_free_text_answers_are_accepted_and_capped():
    state = intake.resolve(_bot(), 'x' * 500, [_u('Frage?'), _a(_QUESTION)])
    assert state.answer == 'x' * 200
