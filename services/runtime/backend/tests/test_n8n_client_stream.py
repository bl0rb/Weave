"""Unit tests for app/services/n8n_client.py's `run_flow_stream` (contract
v2 streaming) -- `httpx.stream` mocked exactly like tests/test_llm.py's own
OpenAICompatibleLLM.chat_stream tests (`_FakeStreamResponse`/`_FakeStreamCtx`
below mirror that file's identical helpers, plus a `.headers` dict for
content-type dispatch), no real network call anywhere in this file. Covers
all three response shapes `_parse_stream_response` dispatches on (our own
SSE contract, n8n's native NDJSON/streaming-response contract, and the
plain-JSON fallback for a bot with streaming enabled whose flow doesn't
actually stream yet), the idle-timeout/malformed/cancel edge cases, and the
one wire addition to the signed request body (`"stream": true`).
"""

import json

import httpx
import pytest

from app.core.config import settings
from app.schemas.bot import BotConfig
from app.schemas.chat import ChatUser
from app.services.n8n_client import (
    N8nError,
    N8nStreamDelta,
    N8nStreamDone,
    N8nStreamError,
    N8nStreamSources,
    N8nUnavailable,
    run_flow_stream,
)

_WEBHOOK_URL = 'https://n8n.example.test/webhook/agent'


@pytest.fixture(autouse=True)
def _n8n_settings(monkeypatch):
    monkeypatch.setattr(settings, 'weave_delegation_secret', 'unit-test-delegation-secret')
    monkeypatch.setattr(settings, 'delegation_token_ttl_seconds', 300)
    monkeypatch.setattr(settings, 'tools_base_url', 'https://weave-tools.internal.example.com')


def _bot(*, timeout_seconds: int = 120) -> BotConfig:
    return BotConfig.model_validate({
        'id': 'n8n-agent', 'name': 'n8n Agent',
        'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'},
        'system_prompt': 'Delegates to n8n.',
        'n8n': {'webhook_url': _WEBHOOK_URL, 'streaming': True, 'timeout_seconds': timeout_seconds},
    })


class _FakeStreamResponse:
    """Mirrors tests/test_llm.py's identical helper, plus `.headers` (a
    plain dict is enough -- `_parse_stream_response` only ever calls
    `.get('content-type', '')` on it)."""

    def __init__(self, status_code: int, lines: list[str] | None = None, *, content_type: str = '', text: str = '', json_data=None) -> None:
        self.status_code = status_code
        self._lines = list(lines or [])
        self.headers = {'content-type': content_type}
        self.text = text
        self._json_data = json_data

    def iter_lines(self):
        return iter(self._lines)

    def read(self) -> None:
        pass

    def json(self):
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


class _RaisingLines:
    """An `iter_lines()` double that raises partway through -- simulates an
    httpx read timing out mid-stream (n8n went idle longer than
    `bot.n8n.timeout_seconds`)."""

    def __init__(self, lines: list[str], exc: Exception) -> None:
        self._lines = lines
        self._exc = exc

    def __iter__(self):
        yield from self._lines
        raise self._exc


class _FakeStreamCtx:
    def __init__(self, response) -> None:
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _sse(*events: dict) -> list[str]:
    return [f'data: {json.dumps(event)}' for event in events]


def _run(bot=None, **kwargs) -> list:
    return list(run_flow_stream(bot or _bot(), 'hi', [], ChatUser(id='u-1'), ['vertraege'], **kwargs))


# --- request shape: the one wire addition -------------------------------------


def test_run_flow_stream_signs_a_body_with_stream_true_and_sends_the_accept_header(monkeypatch):
    captured = {}

    def _fake_stream(method, url, content=None, headers=None, timeout=None):
        captured['method'] = method
        captured['url'] = url
        captured['body'] = json.loads(content)
        captured['headers'] = headers
        captured['timeout'] = timeout
        return _FakeStreamCtx(_FakeStreamResponse(200, _sse({'type': 'done'}), content_type='text/event-stream'))

    monkeypatch.setattr('app.services.n8n_client.httpx.stream', _fake_stream)
    list(run_flow_stream(_bot(timeout_seconds=77), 'hi', [], ChatUser(id='u-1'), ['vertraege']))

    assert captured['method'] == 'POST'
    assert captured['url'] == _WEBHOOK_URL
    assert captured['body']['stream'] is True
    assert 'text/event-stream' in captured['headers']['Accept']
    assert 'application/json-lines' in captured['headers']['Accept']
    # read timeout is the per-chunk IDLE budget -- bot.n8n.timeout_seconds,
    # not settings.llm_timeout_seconds or any other fixed default.
    assert captured['timeout'].read == 77


# --- our own SSE contract -----------------------------------------------------


def test_sse_content_type_yields_delta_sources_done(monkeypatch):
    events = _sse(
        {'type': 'delta', 'text': 'Hel'},
        {'type': 'delta', 'text': 'lo'},
        {'type': 'sources', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'confluence'}]},
        {'type': 'done'},
    )
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, events, content_type='text/event-stream'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    result = _run()
    assert result[0] == N8nStreamDelta(text='Hel')
    assert result[1] == N8nStreamDelta(text='lo')
    assert isinstance(result[2], N8nStreamSources)
    assert result[2].sources[0].document_id == 'd'
    assert result[3] == N8nStreamDone()


def test_sse_error_event_yields_n8n_stream_error_and_stops(monkeypatch):
    events = _sse({'type': 'delta', 'text': 'partial'}, {'type': 'error', 'detail': 'flow blew up'})
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, events, content_type='text/event-stream'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    result = _run()
    assert result == [N8nStreamDelta(text='partial'), N8nStreamError(detail='flow blew up')]


def test_sse_unknown_event_type_is_ignored(monkeypatch):
    events = _sse({'type': 'heartbeat'}, {'type': 'delta', 'text': 'ok'}, {'type': 'done'})
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, events, content_type='text/event-stream'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    assert _run() == [N8nStreamDelta(text='ok'), N8nStreamDone()]


def test_sse_blank_and_comment_lines_are_ignored(monkeypatch):
    lines = ['', ': keepalive', 'data: {"type": "delta", "text": "ok"}', 'data: {"type": "done"}']
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, lines, content_type='text/event-stream'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    assert _run() == [N8nStreamDelta(text='ok'), N8nStreamDone()]


def test_sse_malformed_json_data_line_raises_n8n_error(monkeypatch):
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, ['data: {not json'], content_type='text/event-stream'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    with pytest.raises(N8nError):
        _run()


# --- n8n's native NDJSON / "Streaming response" contract ---------------------


@pytest.mark.parametrize('content_type', ['application/json-lines', 'application/x-ndjson', 'application/jsonl', 'text/plain'])
def test_ndjson_content_types_use_the_tolerant_native_parser(monkeypatch, content_type):
    lines = [
        json.dumps({'type': 'begin'}),  # ignored -- not item/chunk/end/done/complete/error
        json.dumps({'type': 'item', 'content': 'Hel'}),
        json.dumps({'type': 'item', 'content': 'lo'}),
        json.dumps({'type': 'end'}),
    ]
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, lines, content_type=content_type))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    result = _run()
    assert result == [N8nStreamDelta(text='Hel'), N8nStreamDelta(text='lo'), N8nStreamDone()]


def test_ndjson_chunk_type_is_also_a_delta(monkeypatch):
    lines = [json.dumps({'type': 'chunk', 'content': 'ok'}), json.dumps({'type': 'complete'})]
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, lines, content_type='application/x-ndjson'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    assert _run() == [N8nStreamDelta(text='ok'), N8nStreamDone()]


def test_ndjson_error_type_yields_n8n_stream_error(monkeypatch):
    lines = [json.dumps({'type': 'error', 'content': 'agent failed'})]
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, lines, content_type='application/x-ndjson'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    assert _run() == [N8nStreamError(detail='agent failed')]


def test_ndjson_non_object_line_is_skipped_not_rejected(monkeypatch):
    lines = [json.dumps(['not', 'an', 'object']), json.dumps({'type': 'item', 'content': 'ok'}), json.dumps({'type': 'done'})]
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, lines, content_type='application/x-ndjson'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    assert _run() == [N8nStreamDelta(text='ok'), N8nStreamDone()]


def test_ndjson_malformed_json_line_raises_n8n_error(monkeypatch):
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, ['{not json'], content_type='application/x-ndjson'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    with pytest.raises(N8nError):
        _run()


# --- application/json fallback (streaming enabled, flow itself doesn't) ------


def test_json_content_type_falls_back_to_the_non_streaming_contract(monkeypatch):
    response = _FakeStreamResponse(
        200, content_type='application/json',
        json_data={'answer': 'plain answer', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'x'}]},
    )
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: _FakeStreamCtx(response))

    result = _run()
    assert result[0] == N8nStreamDelta(text='plain answer')
    assert isinstance(result[1], N8nStreamSources) and len(result[1].sources) == 1
    assert result[2] == N8nStreamDone()


def test_missing_content_type_also_falls_back_to_the_non_streaming_contract(monkeypatch):
    response = _FakeStreamResponse(200, json_data={'answer': 'ok'})  # content_type='' by default
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: _FakeStreamCtx(response))

    assert _run() == [N8nStreamDelta(text='ok'), N8nStreamSources(sources=[]), N8nStreamDone()]


def test_json_fallback_empty_answer_yields_no_delta(monkeypatch):
    response = _FakeStreamResponse(200, content_type='application/json', json_data={'answer': ''})
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: _FakeStreamCtx(response))

    assert _run() == [N8nStreamSources(sources=[]), N8nStreamDone()]


def test_json_fallback_non_json_body_raises_n8n_error(monkeypatch):
    response = _FakeStreamResponse(200, content_type='application/json', text='not json')
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: _FakeStreamCtx(response))

    with pytest.raises(N8nError):
        _run()


# --- status codes / transport failures, identical split to run_flow ----------


@pytest.mark.parametrize('status_code', [500, 502, 503])
def test_5xx_raises_n8n_unavailable(monkeypatch, status_code):
    response = _FakeStreamResponse(status_code, text='boom')
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: _FakeStreamCtx(response))
    with pytest.raises(N8nUnavailable):
        _run()


@pytest.mark.parametrize('status_code', [400, 401, 404, 422])
def test_4xx_raises_n8n_error(monkeypatch, status_code):
    response = _FakeStreamResponse(status_code, text='bad')
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: _FakeStreamCtx(response))
    with pytest.raises(N8nError):
        _run()


def test_connection_error_raises_n8n_unavailable(monkeypatch):
    def _connect_error(*args, **kwargs):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.services.n8n_client.httpx.stream', _connect_error)
    with pytest.raises(N8nUnavailable):
        _run()


def test_idle_read_timeout_mid_stream_raises_n8n_unavailable(monkeypatch):
    # Simulates n8n going quiet longer than bot.n8n.timeout_seconds between
    # two chunks -- httpx itself raises ReadTimeout out of iter_lines() in
    # that case; this must map exactly like a timeout on the very first
    # connect does (N8nUnavailable, never N8nError -- an idle upstream is
    # the same "the upstream SERVICE is the problem" condition).
    raising = _RaisingLines(list(_sse({'type': 'delta', 'text': 'partial'})), httpx.ReadTimeout('idle too long'))
    response = _FakeStreamResponse(200, content_type='text/event-stream')
    response.iter_lines = lambda: raising
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: _FakeStreamCtx(response))

    with pytest.raises(N8nUnavailable):
        _run()


# --- cancellation --------------------------------------------------------------


def test_cancel_event_stops_iteration_without_a_synthetic_done(monkeypatch):
    import threading

    events = _sse({'type': 'delta', 'text': 'one'}, {'type': 'delta', 'text': 'two'}, {'type': 'done'})
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, events, content_type='text/event-stream'))
    monkeypatch.setattr('app.services.n8n_client.httpx.stream', lambda *a, **k: ctx)

    cancel = threading.Event()
    cancel.set()  # already cancelled before the first line is even read
    assert _run(cancel=cancel) == []
