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


def answer_knowledge_spaces(sources: object) -> list[dict]:
    """The knowledge spaces an answer draws on, in order of first appearance
    among its sources: `{collection, refs, notice}` each, `refs` being the
    `Source.ref` numbers the answer cites as `[N]` from that space (empty
    when the answer cites none). A client colours each space by its
    position in this list, so an answer mixing spaces shows which text comes
    from where. `collection` is `None` for a pre-Collections legacy source."""
    spaces: dict[object, dict] = {}
    if not isinstance(sources, list):
        return []
    for source in sources:
        if not isinstance(source, dict):
            continue
        collection = source.get('collection')
        space = spaces.setdefault(collection, {'collection': collection, 'refs': [], 'notice': None})
        ref = source.get('ref')
        if isinstance(ref, int) and ref not in space['refs']:
            space['refs'].append(ref)
        notice = source.get('notice')
        if space['notice'] is None and isinstance(notice, str) and notice.strip():
            space['notice'] = notice.strip()
    return list(spaces.values())


def answer_metadata(sources: object) -> dict:
    """`notices` and `knowledge_spaces` for an optional metadata slot (the
    stream's `sources` event, the OpenAI shim's `weave`): empty unless the
    answer is flagged or draws on more than one knowledge space, so a plain
    answer stays exactly as before."""
    notices = answer_notices(sources)
    spaces = answer_knowledge_spaces(sources)
    if not notices and len(spaces) < 2:
        return {}
    return {'notices': notices, 'knowledge_spaces': spaces}
