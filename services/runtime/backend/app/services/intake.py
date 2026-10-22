"""The per-bot intake question (`BotConfig.intake`, app/schemas/bot.py),
derived purely from the request's history -- Weave-Runtime is stateless and
the conversation is the only memory there is.

An assistant turn whose text IS the intake question marks "asked"; the user
turn right after it is the answer (with `allow_free_text: false` only an
option counts, anything else leaves it unanswered and gets the question
again). When the current message answers the question, the turn continues
with the question the person asked first (the user turn before the first of
those asks), so they never have to repeat it.

Limit: the gateway sends a capped history (Weave-API's
HISTORY_MAX_MESSAGES); once the answered intake scrolls out of it, the bot
asks again.
"""

from dataclasses import dataclass

from app.schemas.bot import BotConfig, IntakeConfig

# An answer is only ever quoted back to the LLM as context, never followed
# as an instruction -- capping it keeps a free-text reply from carrying a
# whole second prompt.
_MAX_ANSWER_CHARS = 200


@dataclass(frozen=True)
class IntakeState:
    # The message and history the turn actually continues with.
    message: str
    history: list[dict[str, str]]
    # The accepted answer, None while unanswered.
    answer: str | None = None
    # True when the current message tried to answer and was rejected --
    # then the question is asked again whatever the router says.
    retry: bool = False


def _is_question(turn: dict[str, str], intake: IntakeConfig) -> bool:
    return turn.get('role') == 'assistant' and turn.get('content', '').strip() == intake.question.strip()


def _accepted(reply: str, intake: IntakeConfig) -> str | None:
    cleaned = reply.strip()
    for option in intake.options:
        if option.casefold() == cleaned.casefold():
            return option
    if intake.allow_free_text and cleaned:
        return cleaned[:_MAX_ANSWER_CHARS]
    return None


def resolve(bot: BotConfig, message: str, history: list[dict[str, str]]) -> IntakeState | None:
    """None when `bot` has no intake; otherwise see this module's docstring."""
    intake = bot.intake
    if intake is None:
        return None
    asked = [index for index, turn in enumerate(history) if _is_question(turn, intake)]
    for index in reversed(asked):
        reply = history[index + 1] if index + 1 < len(history) else None
        if reply is not None and reply.get('role') == 'user':
            answer = _accepted(reply.get('content', ''), intake)
            if answer is not None:
                return IntakeState(message=message, history=history, answer=answer)
    if not asked or asked[-1] != len(history) - 1:
        return IntakeState(message=message, history=history)

    # The current message replies to the question just asked. Walk back over
    # earlier (rejected) ask/reply pairs to the question asked first.
    first = asked[-1]
    while first >= 2 and history[first - 1].get('role') == 'user' and _is_question(history[first - 2], intake):
        first -= 2
    original = history[first - 1] if first >= 1 and history[first - 1].get('role') == 'user' else None
    answer = _accepted(message, intake)
    if answer is None:
        return IntakeState(
            message=original['content'] if original else message, history=history[: max(first - 1, 0)], retry=True,
        )
    if original is None:
        return IntakeState(message=message, history=history[:first], answer=answer)
    return IntakeState(message=original['content'], history=history[: first - 1], answer=answer)


def context_line(bot: BotConfig, answer: str) -> str:
    """The system-prompt addition for an answered intake."""
    assert bot.intake is not None
    label = bot.intake.label or bot.intake.question
    return f'Angabe der Person zu Gesprächsbeginn (gilt für das ganze Gespräch) -- {label}: {answer}'
