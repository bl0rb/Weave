"""The ONE thread+queue bridge between app/services/bot_graph.py's compiled
turn graph and `POST /internal/chat/stream`'s SSE generator -- replaces the
three near-identical worker/queue loops chat.py used to carry for n8n,
single-subagent and multi-subagent turns.

Why a thread at all: a bot's n8n flow or agent research may stay silent for
minutes (see `app.services.chat_common.Keepalive`), and a plain generator
built on blocking socket reads can only produce its NEXT value by blocking
this exact call stack on that read, with no chance to interleave a
keepalive. So the graph runs on a daemon thread and streams its events
through a `queue.Queue`; the generator side drains that queue with a
`settings.stream_keepalive_seconds` timeout and yields `KEEPALIVE` on every
expiry.

Protocol on the queue (worker -> reader):
- `('event', ChatStreamEvent)` -- one already-complete stream event a graph
  node wrote via its `runtime.stream_writer` (LangGraph `custom` stream
  mode), forwarded verbatim.
- `('end', values)` -- the graph finished; `values` is its final state.
- `('error', exc, values)` -- a node raised; `values` is the last state
  snapshot seen (`branch` in it picks the user-facing German error text).

`wait_for_trace()` is what keeps the pre-stream HTTP status contract
(contracts/internal-chat.md: bot lookup, permissions, retrieval and guard
failures answer 404/403/503, never a `200` SSE stream): it blocks until the
graph's `trace` node has emitted the first event -- everything that can
still raise `RetrievalUnavailable` runs BEFORE that node -- and re-raises
any exception that arrived first on the caller's own thread, before
app/api/internal.py has built a StreamingResponse at all.
"""

import logging
import queue
import threading
from collections.abc import Iterator
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.core.config import settings
from app.schemas.chat import ChatStreamErrorEvent, ChatStreamEvent, ChatStreamTraceEvent
from app.services.chat_common import KEEPALIVE, Keepalive

logger = logging.getLogger(__name__)

# User-facing `ChatStreamErrorEvent.detail` for an exception that escaped a
# node AFTER the trace event went out, per turn branch -- byte-identical to
# the texts the three former worker loops emitted. A branch not listed here
# (the direct RAG/conversational path) re-raises instead, exactly like the
# former plain generator let anything but `LLMError` propagate.
_BRANCH_ERROR_DETAIL = {
    'agent': 'Die Recherche des Agenten ist unerwartet fehlgeschlagen.',
    'multi_agent': 'Die Recherche der Agenten ist unerwartet fehlgeschlagen.',
    'n8n': 'Der n8n-Agentenflow ist unerwartet fehlgeschlagen.',
}


class GraphStreamBridge:
    def __init__(
        self, compiled: Any, state: dict, *, context: Any, config: RunnableConfig | None, bot_id: str,
        cancel: threading.Event,
    ) -> None:
        self._compiled = compiled
        self._state = state
        self._context = context
        self._config = config
        self._bot_id = bot_id
        self._queue: queue.Queue = queue.Queue()
        self._trace: ChatStreamTraceEvent | None = None
        self._thread: threading.Thread | None = None
        # The same Event the caller wired into `context.cancel` -- set once
        # the reader stops consuming, so n8n streaming/subagent loops stop
        # forwarding work.
        self.cancel = cancel

    def _worker(self) -> None:
        last_values: dict = {}
        try:
            for mode, chunk in self._compiled.stream(
                self._state, config=self._config, context=self._context, stream_mode=['custom', 'values'],
            ):
                if mode == 'custom':
                    self._queue.put(('event', chunk))
                elif mode == 'values':
                    last_values = chunk
        except Exception as exc:  # noqa: BLE001 -- forwarded to the reader thread, never raised on this one
            self._queue.put(('error', exc, last_values))
            return
        self._queue.put(('end', last_values))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._worker, name='chat-turn-graph', daemon=True)
        self._thread.start()

    def wait_for_trace(self) -> ChatStreamTraceEvent:
        """Block (no timeout -- nothing is streaming yet, and the caller is
        about to turn any exception into an HTTP status) until the graph's
        `trace` event arrives, or re-raise whatever stopped the graph
        first."""
        while True:
            item = self._queue.get()
            kind = item[0]
            if kind == 'error':
                self.cancel.set()
                raise item[1]
            if kind == 'end':
                self.cancel.set()
                raise RuntimeError('chat turn graph ended before emitting its trace event')
            event = item[1]
            if isinstance(event, ChatStreamTraceEvent):
                self._trace = event
                return event
            # Nothing may precede the trace event (contracts/internal-chat.md
            # ordering guarantee); a node that writes earlier is a bug.
            raise RuntimeError(f'chat turn graph emitted {event.type!r} before its trace event')

    def events(self) -> Iterator[ChatStreamEvent | Keepalive]:
        assert self._trace is not None, 'wait_for_trace() must run before events()'
        try:
            yield self._trace
            while True:
                try:
                    item = self._queue.get(timeout=settings.stream_keepalive_seconds)
                except queue.Empty:
                    yield KEEPALIVE
                    continue
                kind = item[0]
                if kind == 'event':
                    yield item[1]
                    continue
                if kind == 'end':
                    return
                exc, values = item[1], item[2]
                branch = values.get('branch') if isinstance(values, dict) else None
                detail = _BRANCH_ERROR_DETAIL.get(branch or '')
                if detail is None:
                    raise exc
                logger.warning('%s stream turn failed for bot %s: %s', branch, self._bot_id, exc)
                yield ChatStreamErrorEvent(detail=detail)
                return
        finally:
            # Best-effort cancellation on client disconnect/early close -- a
            # no-op once reached on ordinary completion.
            self.cancel.set()
