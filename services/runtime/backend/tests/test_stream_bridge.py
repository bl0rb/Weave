"""Unit tests for app/services/stream_bridge.py -- the one thread+queue
bridge between the compiled turn graph and the SSE generator."""

import threading
import time

import pytest

from app.core.config import settings
from app.schemas.chat import (
    ChatStreamDeltaEvent,
    ChatStreamDoneEvent,
    ChatStreamTraceEvent,
    ChatTrace,
)
from app.services.chat_common import KEEPALIVE
from app.services.retrieval_client import RetrievalUnavailable
from app.services.stream_bridge import GraphStreamBridge


def _trace_event() -> ChatStreamTraceEvent:
    return ChatStreamTraceEvent(trace=ChatTrace(
        intent='knowledge', confidence=0.9, needs_retrieval=True, needs_tool=False, router_mode='rules', timings_ms={},
    ))


class _FakeGraph:
    """Stands in for `bot_graph.COMPILED`: `script` is what `.stream()`
    yields (as `(mode, chunk)` pairs), or an exception to raise after the
    chunks so far."""

    def __init__(self, script):
        self.script = script

    def stream(self, state, config=None, context=None, stream_mode=None):
        assert stream_mode == ['custom', 'values']
        for item in self.script:
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, dict):
                yield 'values', item
            else:
                yield 'custom', item


def _bridge(script) -> GraphStreamBridge:
    return GraphStreamBridge(_FakeGraph(script), {}, context=None, config=None, bot_id='b', cancel=threading.Event())


def test_forwards_trace_then_events_and_sets_cancel_on_completion():
    bridge = _bridge([_trace_event(), ChatStreamDeltaEvent(text='Hallo'), {'branch': 'knowledge'}, ChatStreamDoneEvent()])
    bridge.start()
    assert bridge.wait_for_trace().type == 'trace'
    events = list(bridge.events())
    assert [event.type for event in events] == ['trace', 'delta', 'done']
    assert bridge.cancel.is_set()


def test_pre_trace_exception_is_reraised_unwrapped_on_the_caller():
    bridge = _bridge([RetrievalUnavailable('retrieval down')])
    bridge.start()
    with pytest.raises(RetrievalUnavailable, match='retrieval down'):
        bridge.wait_for_trace()
    assert bridge.cancel.is_set()


def test_keepalive_is_emitted_while_the_graph_is_silent(monkeypatch):
    monkeypatch.setattr(settings, 'stream_keepalive_seconds', 0.02)
    release = threading.Event()

    class _SlowGraph(_FakeGraph):
        def stream(self, *args, **kwargs):
            yield 'custom', _trace_event()
            release.wait(timeout=5)
            yield 'custom', ChatStreamDoneEvent()

    bridge = GraphStreamBridge(_SlowGraph([]), {}, context=None, config=None, bot_id='b', cancel=threading.Event())
    bridge.start()
    bridge.wait_for_trace()
    events = bridge.events()
    assert next(events).type == 'trace'
    first = next(events)
    assert first is KEEPALIVE
    release.set()
    rest = [event for event in events if event is not KEEPALIVE]
    assert [event.type for event in rest] == ['done']


@pytest.mark.parametrize('branch, detail', [
    ('agent', 'Die Recherche des Agenten ist unerwartet fehlgeschlagen.'),
    ('multi_agent', 'Die Recherche der Agenten ist unerwartet fehlgeschlagen.'),
    ('n8n', 'Der n8n-Agentenflow ist unerwartet fehlgeschlagen.'),
])
def test_post_trace_failure_on_a_worker_branch_becomes_a_fixed_error_event(branch, detail):
    bridge = _bridge([_trace_event(), {'branch': branch}, RuntimeError('provider exploded: secret-url')])
    bridge.start()
    bridge.wait_for_trace()
    events = list(bridge.events())
    assert [event.type for event in events] == ['trace', 'error']
    assert events[1].detail == detail  # never the raw exception text


def test_post_trace_failure_on_the_direct_path_propagates():
    bridge = _bridge([_trace_event(), {'branch': 'knowledge'}, RuntimeError('unexpected')])
    bridge.start()
    bridge.wait_for_trace()
    events = bridge.events()
    assert next(events).type == 'trace'
    with pytest.raises(RuntimeError, match='unexpected'):
        next(events)
    assert bridge.cancel.is_set()


def test_closing_the_generator_early_sets_cancel():
    bridge = _bridge([_trace_event(), ChatStreamDeltaEvent(text='a'), ChatStreamDeltaEvent(text='b'), ChatStreamDoneEvent()])
    bridge.start()
    bridge.wait_for_trace()
    events = bridge.events()
    next(events)
    events.close()
    assert bridge.cancel.is_set()
    time.sleep(0)  # the daemon worker may still be draining; nothing to join on
