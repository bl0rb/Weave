"""Answer notices: the texts Weave-Runtime puts on sources from a knowledge
space a bot flags (`Source.notice`, e.g. "Nur intern -- nicht an Kunden
weitergeben"), lifted to the answer itself so every API caller gets them as
answer metadata instead of having to dig through `sources`."""


def answer_notices(sources: object) -> list[str]:
    """The distinct, non-empty `notice` texts of an answer's sources, in
    source order. `[]` for no sources or none flagged."""
    notices: list[str] = []
    if not isinstance(sources, list):
        return notices
    for source in sources:
        notice = source.get('notice') if isinstance(source, dict) else None
        if isinstance(notice, str) and notice.strip() and notice.strip() not in notices:
            notices.append(notice.strip())
    return notices
