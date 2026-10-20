"""Leaf module for the handful of chat-pipeline names that more than one
service module needs: the `KEEPALIVE` transport marker, the retrieval
`NO_COLLECTION_SENTINEL`, and the `RetrievedChunk` -> `Source` projection.

Lives below app/services/chat.py, app/services/scope.py and
app/services/agent_graph.py on purpose: scope.py used to import
`NO_COLLECTION_SENTINEL` from chat.py while chat.py imported scope.py
lazily, and agent_graph.py imported chat.py's `_to_source` lazily while
chat.py imports agent_graph.py eagerly -- two import cycles held apart only
by function-local imports. Everything here depends on nothing but schemas
and the retrieval client, so every other module can import it eagerly.
chat.py re-exports these names unchanged (tests and app/api/internal.py
keep importing them from there).
"""

import re
import time

from app.schemas.chat import Source
from app.services.retrieval_client import RetrievedChunk


class Keepalive:
    """A transport-level marker the chat stream yields in place of a
    `ChatStreamEvent` (app/schemas/chat.py) while a long-running turn (an
    n8n flow, an agent-mode research round, a slow retrieval) is still
    waiting for its next event -- app/api/internal.py's own `_iter_sse`
    recognises this EXACT singleton (`KEEPALIVE` below, never a fresh
    instance -- callers compare with `is`, not `==`) and turns it into the
    literal SSE comment line `': keepalive\\n\\n'` instead of a `data: ...`
    line.

    Deliberately NOT a member of the `ChatStreamEvent` union: it carries no
    `type` field and is never JSON-serialized, so an existing consumer that
    only ever parses `'data:'`-prefixed lines (Weave-Chat's own
    src/lib/sse.ts, contracts/internal-chat.md's own reading of the SSE
    spec) ignores it by construction, exactly like it already ignores any
    other SSE comment. Exists because a bot's own `n8n.timeout_seconds`
    (app/schemas/bot.py's N8nConfig) is an IDLE budget for the n8n call
    itself -- possibly hours long, for a genuinely long-running agent flow,
    per contracts/n8n-flow.md's "Streaming" section -- not a promise that
    this response's own wire stays busy that whole time; without this
    marker, a reverse proxy or gateway between here and an eventual browser
    would be free to decide a silent connection is dead and close it long
    before the flow actually finishes. See `app/core/config.py`'s
    `stream_keepalive_seconds` for how often one is emitted.
    """

    __slots__ = ()


KEEPALIVE = Keepalive()

# Mirrors Weave-Retrieval's own NO_COLLECTION_SENTINEL (app/services/search.py
# in that service) byte-for-byte -- there is no shared Python module to import
# this from (a different service, reached only over HTTP; see
# app/services/retrieval_client.py's own docstring), so the exact string is
# duplicated here instead. Never a real collection slug (Weave-Ingest's
# Collections contract reserves it system-wide for this sentinel meaning);
# chat.py's `resolve_collection_scope` appends it to mean "documents with NO
# collection at all (`Document.collection_slug IS NULL` -- pre-Collections
# legacy content) are ALSO allowed", exactly as Weave-Retrieval's
# apply_filters() interprets it on the other end of this HTTP call.
NO_COLLECTION_SENTINEL = '__none__'


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _score_for(chunk: RetrievedChunk) -> float | None:
    """The single `Source.score` (app/schemas/chat.py) for `chunk`, chosen
    per `RetrievedChunkScores`' own docstring (app/services/retrieval_client.py):
    "which signal -- rerank if present, else rrf -- becomes 'the' score on a
    chat response" is explicitly left to THIS stage to decide. `rerank` when
    a reranker actually scored this chunk, `rrf` (the fused vector+fulltext
    rank, always present whenever there is a result at all) otherwise.
    """
    scores = chunk.scores
    return scores.rerank if scores.rerank is not None else scores.rrf


_IMAGE_MARKDOWN_RE = re.compile(r'!\[[^\]]*\]\((https?://[^)\s]+)\)')
_MAX_IMAGES_PER_SOURCE = 8


def _images_for(chunk: RetrievedChunk) -> list[str]:
    """Absolute http(s) image URLs found in `chunk.text`'s own markdown,
    deduplicated (order preserved) and capped at `_MAX_IMAGES_PER_SOURCE`.
    """
    urls = dict.fromkeys(_IMAGE_MARKDOWN_RE.findall(chunk.text or ''))
    return list(urls)[:_MAX_IMAGES_PER_SOURCE]


def _to_source(chunk: RetrievedChunk) -> Source:
    return Source(
        # chunk.source is optional at the retrieval layer (a chunk whose
        # document was never tagged with a source system) but Source.source
        # is a required str on this contract -- '' is the least-surprising
        # fill-in for "no source label", keeping every caller's `Source`
        # object uniformly a plain str instead of forcing an extra optional
        # case downstream for what is, for both of this repo's example
        # bots' documents, a case that never actually occurs.
        source=chunk.source or '',
        original_filename=chunk.original_filename,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        document_version=chunk.document_version,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        score=_score_for(chunk),
        # Same rerank-if-present-else-rrf choice _score_for makes, just
        # naming which one it picked -- see Source.score_kind's docstring.
        score_kind='rerank' if chunk.scores.rerank is not None else 'rrf',
        collection=chunk.collection,
        images=_images_for(chunk),
        source_kind=chunk.source_kind,
        source_url=chunk.source_url,
        uploaded_by=chunk.uploaded_by,
        uploaded_at=chunk.uploaded_at,
    )
