"""End-to-end tests for the DEFERRED n8n streaming pipeline (contract v2 --
app/services/chat.py's `_stream_deferred_n8n`/`_DeferredN8nTurn`,
app/services/n8n_client.py's `run_flow_stream`) -- mirrors
tests/test_chat_n8n.py's own style (real HTTP round trips through the
FastAPI app, `httpx.get`/`httpx.stream`/`httpx.post` mocked at the exact
seams app/services/retrieval_client.py and app/services/n8n_client.py talk
to) but exercises the NEW behaviour design item 2 adds: for a bot with
`n8n.streaming=True` the webhook call runs INSIDE the stream phase, with
real incremental deltas, `guard.require_sources` buffering, keepalive
comments while idle, and terminal `error` events on failure -- none of
which tests/test_chat_n8n.py's own streaming tests (a `streaming=False`
bot, using `run_flow`'s single blocking call) exercise.
"""

import json
import time

import httpx
import pytest
import yaml

from app.core.config import settings
from app.services.botconfig import list_bots
from tests.conftest import AUTH_HEADERS, client

_WEBHOOK_URL = 'https://n8n.example.test/webhook/agent'
_SECRET = 'chat-n8n-stream-test-secret'

_OPEN_BOT_YAML = {
    'id': 'n8n-stream-open',
    'name': 'n8n Streaming (open)',
    'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'},
    'system_prompt': 'Delegates to n8n, streaming.',
    'n8n': {'webhook_url': _WEBHOOK_URL, 'streaming': True, 'timeout_seconds': 30},
    'retrieval': {'enabled': True, 'collections': ['vertraege']},
    'permissions': {'teams': []},
    'guard': {'require_sources': False, 'no_context_reply': 'Nichts gefunden.'},
}

_GUARDED_BOT_YAML = {
    **_OPEN_BOT_YAML,
    'id': 'n8n-stream-guarded',
    'guard': {'require_sources': True, 'no_context_reply': 'Kein Beleg gefunden.'},
}

_KNOWLEDGE_MESSAGE = 'Welche Vertraege laufen naechsten Monat aus?'  # -> 'knowledge' intent, reaches n8n


@pytest.fixture(autouse=True)
def _n8n_stream_bots(tmp_path, monkeypatch):
    (tmp_path / 'n8n-stream-open.yaml').write_text(yaml.safe_dump(_OPEN_BOT_YAML), encoding='utf-8')
    (tmp_path / 'n8n-stream-guarded.yaml').write_text(yaml.safe_dump(_GUARDED_BOT_YAML), encoding='utf-8')
    monkeypatch.setattr(settings, 'bots_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'weave_delegation_secret', _SECRET)
    monkeypatch.setattr(settings, 'delegation_token_ttl_seconds', 300)
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.example.test/webhook/'])
    monkeypatch.setattr(settings, 'tools_base_url', 'https://weave-tools.internal.example.com')
    assert {bot.id for bot in list_bots()} == {'n8n-stream-open', 'n8n-stream-guarded'}


def _collection(**overrides) -> dict:
    collection = {'slug': 'vertraege', 'name': 'Vertraege', 'description': None, 'public': False}
    collection.update(overrides)
    return collection


def _readable_collections(*collections: dict):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(200, list(collections) or [_collection()])

    return _fake_get


class _FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


class _FakeStreamResponse:
    """Just enough of an httpx.Response opened via `httpx.stream(...)` to
    exercise `run_flow_stream`'s own parsing -- mirrors
    tests/test_n8n_client_stream.py's identical helper, plus an optional
    per-line delay so a test can make the flow go idle long enough to
    exercise the keepalive path."""

    def __init__(self, status_code: int, lines: list[str], *, content_type: str = 'text/event-stream', delays: dict[int, float] | None = None) -> None:
        self.status_code = status_code
        self._lines = lines
        self._delays = delays or {}
        self.headers = {'content-type': content_type}
        self.text = ''

    def iter_lines(self):
        for index, line in enumerate(self._lines):
            if index in self._delays:
                time.sleep(self._delays[index])
            yield line

    def read(self) -> None:
        pass


class _FakeStreamCtx:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self.response = response

    def __enter__(self) -> _FakeStreamResponse:
        return self.response

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _sse(*events: dict) -> list[str]:
    return [f'data: {json.dumps(event)}' for event in events]


def _stream_n8n_lines(*lines: str, delays: dict[int, float] | None = None):
    def _fake_stream(method, url, content=None, headers=None, timeout=None):
        if url != _WEBHOOK_URL:
            raise AssertionError(f'unexpected httpx.stream to {url!r}')
        return _FakeStreamCtx(_FakeStreamResponse(200, list(lines), delays=delays))

    return _fake_stream


def _events(text: str) -> list[dict]:
    """Parse an SSE body into its `data:`-carrying events only -- comment
    lines (keepalives) are asserted on separately via raw substring checks,
    exactly because they are NOT `data:` lines (see `chat.Keepalive`'s own
    docstring)."""
    parsed = []
    for block in text.split('\n\n'):
        block = block.strip('\n')
        if not block or not block.startswith('data: '):
            continue
        parsed.append(json.loads(block[len('data: '):]))
    return parsed


def _post_stream(bot_id: str, *, message: str = _KNOWLEDGE_MESSAGE):
    return client.post(
        '/internal/chat/stream',
        json={'bot_id': bot_id, 'message': message, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )


# --- incremental deltas (no require_sources) ----------------------------------


def test_incremental_deltas_are_forwarded_as_soon_as_they_arrive(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.stream',
        _stream_n8n_lines(*_sse(
            {'type': 'delta', 'text': 'Drei '},
            {'type': 'delta', 'text': 'Vertraege.'},
            {'type': 'sources', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'confluence', 'collection': 'vertraege'}]},
            {'type': 'done'},
        )),
    )

    resp = _post_stream('n8n-stream-open')
    assert resp.status_code == 200
    events = _events(resp.text)
    types = [event['type'] for event in events]
    assert types[0] == 'trace'
    assert types[-2:] == ['sources', 'done']

    delta_events = [event for event in events if event['type'] == 'delta']
    assert len(delta_events) == 2  # forwarded one at a time, not buffered into one
    assert ''.join(event['text'] for event in delta_events) == 'Drei Vertraege.'
    sources_event = next(event for event in events if event['type'] == 'sources')
    assert [s['document_id'] for s in sources_event['sources']] == ['d']


def test_streaming_trace_has_no_model_and_no_n8n_ms(monkeypatch):
    # The webhook call has not happened yet when `trace` is built (see
    # `_DeferredN8nTurn`'s own docstring) -- both would misleadingly imply
    # an LLM call/a finished n8n call, neither of which is true at that
    # point.
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', _stream_n8n_lines(*_sse({'type': 'done'})))

    resp = _post_stream('n8n-stream-open')
    trace = _events(resp.text)[0]['trace']
    assert trace['model'] is None
    assert 'n8n_ms' not in trace['timings_ms']


# --- require_sources buffering ------------------------------------------------


def test_require_sources_buffers_every_delta_into_one_when_sources_arrive(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.stream',
        _stream_n8n_lines(*_sse(
            {'type': 'delta', 'text': 'Belegte '},
            {'type': 'delta', 'text': 'Antwort.'},
            {'type': 'sources', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'confluence', 'collection': 'vertraege'}]},
            {'type': 'done'},
        )),
    )

    resp = _post_stream('n8n-stream-guarded')
    events = _events(resp.text)
    delta_events = [event for event in events if event['type'] == 'delta']
    assert len(delta_events) == 1  # buffered -- never forwarded incrementally
    assert delta_events[0]['text'] == 'Belegte Antwort.'
    assert events[-1]['type'] == 'done'
    assert [s['document_id'] for s in events[-2]['sources']] == ['d']


def test_require_sources_guard_fires_when_no_sources_ever_arrive(monkeypatch):
    # n8n's OWN native streaming contract never sends a sources event at
    # all (see N8nStreamSources' own docstring) -- 'done' with nothing
    # reported must trigger the guard exactly like the blocking path's
    # identical outcome (tests/test_chat_n8n.py's
    # test_no_sources_with_require_sources_triggers_the_guard).
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.stream',
        _stream_n8n_lines(*_sse({'type': 'delta', 'text': 'Sollte verworfen werden.'}, {'type': 'done'})),
    )

    resp = _post_stream('n8n-stream-guarded')
    events = _events(resp.text)
    delta_events = [event for event in events if event['type'] == 'delta']
    reconstructed = ''.join(event['text'] for event in delta_events)
    assert reconstructed == 'Kein Beleg gefunden.'  # bot.guard.no_context_reply, NOT the flow's own text
    assert len(delta_events) > 1  # the fixed guard reply is chunked, not one delta
    assert events[-2] == {'type': 'sources', 'sources': []}
    assert events[-1] == {'type': 'done'}


def test_require_sources_guard_fires_when_reported_sources_are_out_of_scope(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.stream',
        _stream_n8n_lines(*_sse(
            {'type': 'delta', 'text': 'Sollte nicht ankommen.'},
            {'type': 'sources', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'x', 'collection': 'geheim'}]},
            {'type': 'done'},
        )),
    )

    resp = _post_stream('n8n-stream-guarded')
    events = _events(resp.text)
    reconstructed = ''.join(event['text'] for event in events if event['type'] == 'delta')
    assert reconstructed == 'Kein Beleg gefunden.'


# --- terminal error events ------------------------------------------------------


def test_flow_reported_error_becomes_a_terminal_stream_error_event(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.stream',
        _stream_n8n_lines(*_sse({'type': 'delta', 'text': 'partial'}, {'type': 'error', 'detail': 'agent crashed'})),
    )

    resp = _post_stream('n8n-stream-open')
    assert resp.status_code == 200  # already committed -- never a 5xx this late
    events = _events(resp.text)
    assert events[-1] == {'type': 'error', 'detail': 'agent crashed'}
    assert not any(event['type'] in ('sources', 'done') for event in events)


def test_transport_failure_during_the_deferred_call_becomes_a_terminal_stream_error_event(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())

    def _connect_error(*args, **kwargs):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.services.n8n_client.httpx.stream', _connect_error)

    resp = _post_stream('n8n-stream-open')
    assert resp.status_code == 200  # unlike the blocking route's 503 -- see handle_chat_stream's own docstring
    events = _events(resp.text)
    assert events[-1]['type'] == 'error'
    # The user-facing detail is generic: the webhook_url and the raw transport
    # error stay in the server log only (this detail reaches the browser
    # verbatim via Weave-API's stream passthrough).
    assert events[-1]['detail'] == 'Der n8n-Agentenflow ist derzeit nicht erreichbar oder hat nicht rechtzeitig geantwortet.'
    assert _WEBHOOK_URL not in resp.text and 'connection refused' not in resp.text


# --- keepalive --------------------------------------------------------------


def test_keepalive_comment_is_emitted_while_the_flow_is_silent(monkeypatch):
    # A tiny keepalive interval plus an artificial delay before n8n's first
    # line lets this test exercise the real background-thread/queue
    # machinery in `_stream_deferred_n8n` without waiting anywhere near the
    # production 5s default.
    monkeypatch.setattr(settings, 'stream_keepalive_seconds', 0.02)
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.stream',
        _stream_n8n_lines(*_sse({'type': 'delta', 'text': 'ok'}, {'type': 'done'}), delays={0: 0.12}),
    )

    resp = _post_stream('n8n-stream-open')
    assert resp.status_code == 200
    assert ': keepalive\n\n' in resp.text
    # the real events still arrive, in order, once the flow speaks
    events = _events(resp.text)
    assert [event['type'] for event in events][-3:] == ['delta', 'sources', 'done']


def test_internal_chat_stream_route_raw_sse_body_contains_the_keepalive_comment_line(monkeypatch):
    # Same scenario, phrased as the task's own literal assertion against
    # app/api/internal.py's wiring (`_iter_sse` turning `chat.KEEPALIVE`
    # into this exact line) rather than through the higher-level `_events`
    # helper above.
    monkeypatch.setattr(settings, 'stream_keepalive_seconds', 0.02)
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.stream',
        _stream_n8n_lines(*_sse({'type': 'done'}), delays={0: 0.12}),
    )

    resp = _post_stream('n8n-stream-open')
    assert ': keepalive\n\n' in resp.text


# --- blocking POST /internal/chat is unaffected by streaming=True ------------


def test_blocking_chat_consumes_run_flow_stream_to_completion_for_a_streaming_bot(monkeypatch):
    # POST /internal/chat never defers anything (see _prepare_turn's own
    # docstring on `defer_n8n`) -- a bot with n8n.streaming=True still
    # answers this route with one ordinary, complete ChatResponse.
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.stream',
        _stream_n8n_lines(*_sse(
            {'type': 'delta', 'text': 'Drei '},
            {'type': 'delta', 'text': 'Vertraege.'},
            {'type': 'sources', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'confluence', 'collection': 'vertraege'}]},
            {'type': 'done'},
        )),
    )

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-stream-open', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['answer'] == 'Drei Vertraege.'
    assert [s['document_id'] for s in body['sources']] == ['d']
    assert 'n8n_ms' in body['trace']['timings_ms']  # unlike the streaming route -- this call already finished
