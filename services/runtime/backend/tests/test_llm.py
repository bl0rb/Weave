"""Unit tests for app.services.llm: FakeLLM (deterministic + the
'[context:N]' convention), OpenAICompatibleLLM (with httpx.post/httpx.stream
mocked -- no real HTTP calls), iter_text_deltas/iter_chat_stream, and
get_llm().
"""

from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.services.llm import (
    FakeLLM,
    LLMError,
    LLMResult,
    LLMUsage,
    OpenAICompatibleLLM,
    get_llm,
    iter_chat_stream,
    iter_text_deltas,
)


# --- FakeLLM -----------------------------------------------------------------

def test_fake_llm_reply_contains_the_last_user_message():
    messages = [
        {'role': 'user', 'content': 'first question'},
        {'role': 'assistant', 'content': 'first answer'},
        {'role': 'user', 'content': 'second question'},
    ]
    result = FakeLLM().chat(messages)
    assert 'second question' in result.content
    assert 'first question' not in result.content


def test_fake_llm_is_deterministic():
    messages = [{'role': 'user', 'content': 'same input every time'}]
    first = FakeLLM().chat(messages)
    second = FakeLLM().chat(messages)
    assert first == second


def test_fake_llm_no_context_block_has_no_context_marker():
    messages = [{'role': 'user', 'content': 'hello'}]
    result = FakeLLM().chat(messages)
    assert '[context:' not in result.content


def test_fake_llm_system_context_block_adds_context_marker_with_source_count():
    messages = [
        {
            'role': 'system',
            'content': '[source 1] first chunk text\n[source 2] second chunk text',
        },
        {'role': 'user', 'content': 'what do the docs say?'},
    ]
    result = FakeLLM().chat(messages)
    assert 'what do the docs say?' in result.content
    assert '[context:2]' in result.content


def test_fake_llm_sums_source_markers_across_multiple_system_messages():
    messages = [
        {'role': 'system', 'content': '[source 1] a'},
        {'role': 'system', 'content': '[source 2] b\n[source 3] c'},
        {'role': 'user', 'content': 'question'},
    ]
    result = FakeLLM().chat(messages)
    assert '[context:3]' in result.content


def test_fake_llm_ignores_source_markers_outside_system_role():
    messages = [
        {'role': 'user', 'content': '[source 1] this is not a real context block'},
    ]
    result = FakeLLM().chat(messages)
    assert '[context:' not in result.content


def test_fake_llm_model_defaults_to_fake_chat():
    result = FakeLLM().chat([{'role': 'user', 'content': 'hi'}])
    assert result.model == 'fake-chat'


def test_fake_llm_model_override_is_echoed_back():
    result = FakeLLM().chat([{'role': 'user', 'content': 'hi'}], model='custom-model')
    assert result.model == 'custom-model'


def test_fake_llm_usage_is_populated_deterministically():
    messages = [{'role': 'user', 'content': 'two words'}]
    result = FakeLLM().chat(messages)
    assert isinstance(result.usage, LLMUsage)
    assert result.usage.prompt_tokens == 2
    assert result.usage.completion_tokens == len(result.content.split())


def test_fake_llm_handles_no_user_message_without_raising():
    result = FakeLLM().chat([{'role': 'system', 'content': 'just a system message'}])
    assert result.content == '[fake-llm] '


# --- FakeLLM.chat_stream / iter_text_deltas ------------------------------------


def test_fake_llm_chat_stream_reconstructs_the_exact_same_content_as_chat():
    messages = [{'role': 'user', 'content': 'what do the docs say?'}]
    streamed = ''.join(FakeLLM().chat_stream(messages))
    assert streamed == FakeLLM().chat(messages).content


def test_fake_llm_chat_stream_yields_more_than_one_delta_for_a_multi_word_reply():
    messages = [{'role': 'user', 'content': 'a longer question with several words'}]
    deltas = list(FakeLLM().chat_stream(messages))
    assert len(deltas) > 1


def test_fake_llm_chat_stream_with_context_reconstructs_the_context_marker_too():
    messages = [
        {'role': 'system', 'content': '[source 1] a\n[source 2] b'},
        {'role': 'user', 'content': 'question'},
    ]
    streamed = ''.join(FakeLLM().chat_stream(messages))
    assert streamed == '[fake-llm] question [context:2]'


def test_iter_text_deltas_reconstructs_the_original_text_exactly():
    text = 'hello   world\nwith  multiple   spaces and a newline'
    assert ''.join(iter_text_deltas(text)) == text


def test_iter_text_deltas_splits_into_more_than_one_piece_for_multiple_words():
    deltas = list(iter_text_deltas('one two three four'))
    assert len(deltas) == 4
    assert deltas == ['one ', 'two ', 'three ', 'four']


def test_iter_text_deltas_empty_string_yields_nothing():
    assert list(iter_text_deltas('')) == []


# --- iter_chat_stream ----------------------------------------------------------


class _StreamingStubProvider:
    """A minimal LLMProvider stub with its OWN `chat_stream` -- used to
    prove `iter_chat_stream` forwards to it unchanged (arguments and
    yielded deltas alike) instead of ever falling back to `chat()`."""

    def __init__(self):
        self.stream_calls: list[dict] = []

    def chat(self, messages, model=None, temperature=None):
        raise AssertionError('chat() must not be called when chat_stream() is available')

    def chat_stream(self, messages, model=None, temperature=None):
        self.stream_calls.append({'messages': messages, 'model': model, 'temperature': temperature})
        yield 'native '
        yield 'deltas'


class _ChatOnlyStubProvider:
    """An LLMProvider stub with ONLY `chat()` -- no `chat_stream` attribute
    at all -- exercising `iter_chat_stream`'s single-delta fallback for a
    provider with no native streaming capability of its own."""

    def __init__(self, content: str):
        self._content = content
        self.chat_calls: list[dict] = []

    def chat(self, messages, model=None, temperature=None):
        self.chat_calls.append({'messages': messages, 'model': model, 'temperature': temperature})
        return LLMResult(content=self._content, model=model or 'stub-model')


def test_iter_chat_stream_forwards_to_the_providers_own_chat_stream():
    provider = _StreamingStubProvider()
    messages = [{'role': 'user', 'content': 'hi'}]
    deltas = list(iter_chat_stream(provider, messages, model='m', temperature=0.5))
    assert deltas == ['native ', 'deltas']
    assert provider.stream_calls == [{'messages': messages, 'model': 'm', 'temperature': 0.5}]


def test_iter_chat_stream_falls_back_to_a_single_delta_without_native_streaming():
    provider = _ChatOnlyStubProvider('the whole answer at once')
    messages = [{'role': 'user', 'content': 'hi'}]
    deltas = list(iter_chat_stream(provider, messages, model='m', temperature=0.1))
    assert deltas == ['the whole answer at once']
    assert provider.chat_calls == [{'messages': messages, 'model': 'm', 'temperature': 0.1}]


# --- OpenAICompatibleLLM ------------------------------------------------------

class _FakeResponse:
    """Just enough of an httpx.Response to exercise the provider's own
    parsing -- status_code, .json(), and .text (mirrors
    Weave-Retrieval's tests/test_reranker.py:_FakeResponse)."""

    def __init__(self, status_code: int, json_data: dict | None = None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self) -> dict:
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


def _chat_payload(content: str, *, model: str = 'gpt-test', usage: dict | None = None) -> dict:
    body = {'choices': [{'message': {'content': content}}], 'model': model}
    if usage is not None:
        body['usage'] = usage
    return body


def _provider(**overrides) -> OpenAICompatibleLLM:
    defaults = dict(base_url='https://llm.example.com', api_key='sk-test', model='default-model')
    defaults.update(overrides)
    return OpenAICompatibleLLM(**defaults)


def test_openai_compatible_llm_success_parses_content_model_and_usage():
    payload = _chat_payload('the answer', model='gpt-test', usage={'prompt_tokens': 10, 'completion_tokens': 3})

    with patch('app.services.llm.httpx.post', return_value=_FakeResponse(200, payload)) as mock_post:
        result = _provider().chat([{'role': 'user', 'content': 'a question'}], temperature=0.2)

    assert result.content == 'the answer'
    assert result.model == 'gpt-test'
    assert result.usage == LLMUsage(prompt_tokens=10, completion_tokens=3)
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == 'https://llm.example.com/v1/chat/completions'
    assert mock_post.call_args.kwargs['headers']['Authorization'] == 'Bearer sk-test'
    sent_json = mock_post.call_args.kwargs['json']
    assert sent_json['model'] == 'default-model'
    assert sent_json['messages'] == [{'role': 'user', 'content': 'a question'}]
    assert sent_json['temperature'] == 0.2


def test_openai_compatible_llm_omits_temperature_when_not_given():
    payload = _chat_payload('reply')
    with patch('app.services.llm.httpx.post', return_value=_FakeResponse(200, payload)) as mock_post:
        _provider().chat([{'role': 'user', 'content': 'hi'}])
    assert 'temperature' not in mock_post.call_args.kwargs['json']


def test_openai_compatible_llm_does_not_duplicate_v1_in_base_url():
    payload = _chat_payload('reply')
    with patch('app.services.llm.httpx.post', return_value=_FakeResponse(200, payload)) as mock_post:
        _provider(base_url='https://llm.example.com/openai/v1').chat([{'role': 'user', 'content': 'hi'}])
    assert mock_post.call_args.args[0] == 'https://llm.example.com/openai/v1/chat/completions'


def test_openai_compatible_llm_uses_requested_model_override():
    payload = _chat_payload('reply', model='')  # server doesn't echo a model back
    with patch('app.services.llm.httpx.post', return_value=_FakeResponse(200, payload)) as mock_post:
        result = _provider().chat([{'role': 'user', 'content': 'hi'}], model='requested-model')
    assert mock_post.call_args.kwargs['json']['model'] == 'requested-model'
    assert result.model == 'requested-model'


def test_openai_compatible_llm_retries_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr('app.services.llm.time.sleep', lambda seconds: sleeps.append(seconds))

    responses = [_FakeResponse(429, text='rate limited'), _FakeResponse(200, _chat_payload('ok'))]
    with patch('app.services.llm.httpx.post', side_effect=responses) as mock_post:
        result = _provider().chat([{'role': 'user', 'content': 'hi'}])

    assert result.content == 'ok'
    assert mock_post.call_count == 2
    assert len(sleeps) == 1


def test_openai_compatible_llm_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr('app.services.llm.time.sleep', lambda seconds: None)
    responses = [_FakeResponse(503, text='unavailable'), _FakeResponse(200, _chat_payload('ok'))]
    with patch('app.services.llm.httpx.post', side_effect=responses) as mock_post:
        result = _provider().chat([{'role': 'user', 'content': 'hi'}])
    assert result.content == 'ok'
    assert mock_post.call_count == 2


def test_openai_compatible_llm_400_raises_llm_error_without_retry():
    with patch('app.services.llm.httpx.post', return_value=_FakeResponse(400, text='bad request')) as mock_post:
        with pytest.raises(LLMError) as exc_info:
            _provider().chat([{'role': 'user', 'content': 'hi'}])
    assert mock_post.call_count == 1
    assert exc_info.value.transient is False
    assert exc_info.value.status_code == 400


def test_openai_compatible_llm_timeout_raises_transient_llm_error(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr('app.services.llm.time.sleep', lambda seconds: sleeps.append(seconds))

    with patch('app.services.llm.httpx.post', side_effect=httpx.TimeoutException('timed out')) as mock_post:
        with pytest.raises(LLMError) as exc_info:
            _provider(max_attempts=3).chat([{'role': 'user', 'content': 'hi'}])

    assert mock_post.call_count == 3
    assert exc_info.value.transient is True
    assert exc_info.value.status_code is None
    assert len(sleeps) == 2


def test_openai_compatible_llm_exhausted_5xx_retries_raise_transient_llm_error(monkeypatch):
    monkeypatch.setattr('app.services.llm.time.sleep', lambda seconds: None)
    with patch('app.services.llm.httpx.post', return_value=_FakeResponse(503, text='down')) as mock_post:
        with pytest.raises(LLMError) as exc_info:
            _provider(max_attempts=2).chat([{'role': 'user', 'content': 'hi'}])
    assert mock_post.call_count == 2
    assert exc_info.value.transient is True
    assert exc_info.value.status_code == 503


# --- OpenAICompatibleLLM.chat_stream --------------------------------------------

class _FakeStreamResponse:
    """Just enough of an httpx.Response opened via `httpx.stream(...)` to
    exercise the provider's own SSE parsing -- status_code, .iter_lines(),
    .read() (a no-op double for the real call that makes .text available
    after the fact), and .text itself."""

    def __init__(self, status_code: int, lines: list[str] | None = None, text: str = '') -> None:
        self.status_code = status_code
        self._lines = list(lines or [])
        self.text = text

    def iter_lines(self):
        return iter(self._lines)

    def read(self) -> None:
        pass


class _FakeStreamCtx:
    """The context manager `httpx.stream(...)` itself returns -- `__enter__`
    hands back the (already "received") response, mirroring how httpx only
    actually starts reading the body once code inside the `with` block asks
    for it."""

    def __init__(self, response: _FakeStreamResponse) -> None:
        self.response = response

    def __enter__(self) -> _FakeStreamResponse:
        return self.response

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _sse_lines(*contents: str, done: bool = True) -> list[str]:
    lines = [f'data: {{"choices":[{{"delta":{{"content":"{content}"}}}}]}}' for content in contents]
    if done:
        lines.append('data: [DONE]')
    return lines


def test_openai_compatible_llm_stream_yields_content_deltas_until_done():
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, _sse_lines('Hel', 'lo')))
    with patch('app.services.llm.httpx.stream', return_value=ctx) as mock_stream:
        deltas = list(_provider().chat_stream([{'role': 'user', 'content': 'hi'}], temperature=0.3))

    assert deltas == ['Hel', 'lo']
    mock_stream.assert_called_once()
    assert mock_stream.call_args.args == ('POST', 'https://llm.example.com/v1/chat/completions')
    sent_json = mock_stream.call_args.kwargs['json']
    assert sent_json['stream'] is True
    assert sent_json['temperature'] == 0.3
    assert mock_stream.call_args.kwargs['headers']['Authorization'] == 'Bearer sk-test'


def test_openai_compatible_llm_stream_ignores_blank_and_non_data_lines():
    lines = ['', 'event: message', 'data: {"choices":[{"delta":{"content":"ok"}}]}', 'data: [DONE]']
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, lines))
    with patch('app.services.llm.httpx.stream', return_value=ctx):
        deltas = list(_provider().chat_stream([{'role': 'user', 'content': 'hi'}]))
    assert deltas == ['ok']


def test_openai_compatible_llm_stream_skips_choices_with_no_content():
    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',  # role-only chunk, no content yet
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        'data: [DONE]',
    ]
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, lines))
    with patch('app.services.llm.httpx.stream', return_value=ctx):
        deltas = list(_provider().chat_stream([{'role': 'user', 'content': 'hi'}]))
    assert deltas == ['ok']


def test_openai_compatible_llm_stream_omits_temperature_when_not_given():
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, _sse_lines('ok')))
    with patch('app.services.llm.httpx.stream', return_value=ctx) as mock_stream:
        list(_provider().chat_stream([{'role': 'user', 'content': 'hi'}]))
    assert 'temperature' not in mock_stream.call_args.kwargs['json']


def test_openai_compatible_llm_stream_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setattr('app.services.llm.time.sleep', lambda seconds: None)
    responses = [
        _FakeStreamCtx(_FakeStreamResponse(429, text='rate limited')),
        _FakeStreamCtx(_FakeStreamResponse(200, _sse_lines('ok'))),
    ]
    with patch('app.services.llm.httpx.stream', side_effect=responses) as mock_stream:
        deltas = list(_provider().chat_stream([{'role': 'user', 'content': 'hi'}]))
    assert deltas == ['ok']
    assert mock_stream.call_count == 2


def test_openai_compatible_llm_stream_400_raises_llm_error_without_retry():
    ctx = _FakeStreamCtx(_FakeStreamResponse(400, text='bad request'))
    with patch('app.services.llm.httpx.stream', return_value=ctx) as mock_stream:
        with pytest.raises(LLMError) as exc_info:
            list(_provider().chat_stream([{'role': 'user', 'content': 'hi'}]))
    assert mock_stream.call_count == 1
    assert exc_info.value.transient is False
    assert exc_info.value.status_code == 400


def test_openai_compatible_llm_stream_exhausted_5xx_retries_raise_transient_llm_error(monkeypatch):
    monkeypatch.setattr('app.services.llm.time.sleep', lambda seconds: None)
    ctx = _FakeStreamCtx(_FakeStreamResponse(503, text='down'))
    with patch('app.services.llm.httpx.stream', return_value=ctx) as mock_stream:
        with pytest.raises(LLMError) as exc_info:
            list(_provider(max_attempts=2).chat_stream([{'role': 'user', 'content': 'hi'}]))
    assert mock_stream.call_count == 2
    assert exc_info.value.transient is True
    assert exc_info.value.status_code == 503


def test_openai_compatible_llm_stream_connect_failure_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr('app.services.llm.time.sleep', lambda seconds: None)
    responses = [httpx.ConnectError('connection refused'), _FakeStreamCtx(_FakeStreamResponse(200, _sse_lines('ok')))]
    with patch('app.services.llm.httpx.stream', side_effect=responses) as mock_stream:
        deltas = list(_provider(max_attempts=3).chat_stream([{'role': 'user', 'content': 'hi'}]))
    assert deltas == ['ok']
    assert mock_stream.call_count == 2


def test_openai_compatible_llm_stream_interrupted_after_partial_content_does_not_retry(monkeypatch):
    # Regression for the one case chat_stream() must handle differently from
    # chat(): once a delta has already reached OUR caller for this attempt,
    # a transport failure while reading the REST of that same stream must
    # never silently retry from scratch -- that would restart the answer on
    # top of text the caller already received. This must raise immediately,
    # with no second call to httpx.stream at all.
    monkeypatch.setattr('app.services.llm.time.sleep', lambda seconds: None)

    def _lines():
        yield 'data: {"choices":[{"delta":{"content":"partial"}}]}'
        raise httpx.ReadError('connection reset')

    response = _FakeStreamResponse(200)
    response.iter_lines = _lines
    ctx = _FakeStreamCtx(response)

    with patch('app.services.llm.httpx.stream', return_value=ctx) as mock_stream:
        stream = _provider(max_attempts=3).chat_stream([{'role': 'user', 'content': 'hi'}])
        assert next(stream) == 'partial'
        with pytest.raises(LLMError) as exc_info:
            next(stream)

    assert mock_stream.call_count == 1
    assert exc_info.value.transient is True


def test_openai_compatible_llm_stream_malformed_json_data_line_raises_llm_error_without_retry():
    ctx = _FakeStreamCtx(_FakeStreamResponse(200, ['data: not-json', 'data: [DONE]']))
    with patch('app.services.llm.httpx.stream', return_value=ctx) as mock_stream:
        with pytest.raises(LLMError):
            list(_provider().chat_stream([{'role': 'user', 'content': 'hi'}]))
    assert mock_stream.call_count == 1


# --- get_llm() -----------------------------------------------------------------

def test_get_llm_fake_returns_fake_llm():
    assert isinstance(get_llm('fake'), FakeLLM)


def test_get_llm_openai_returns_openai_compatible_llm():
    assert isinstance(get_llm('openai'), OpenAICompatibleLLM)


def test_get_llm_is_case_insensitive():
    assert isinstance(get_llm('FAKE'), FakeLLM)
    assert isinstance(get_llm('OpenAI'), OpenAICompatibleLLM)


def test_get_llm_falls_back_to_settings_llm_provider_when_none(monkeypatch):
    monkeypatch.setattr(settings, 'llm_provider', 'fake')
    assert isinstance(get_llm(None), FakeLLM)


def test_get_llm_falls_back_to_settings_llm_provider_when_empty_string(monkeypatch):
    monkeypatch.setattr(settings, 'llm_provider', 'fake')
    assert isinstance(get_llm(''), FakeLLM)


def test_get_llm_unknown_provider_raises_value_error():
    with pytest.raises(ValueError):
        get_llm('not-a-real-provider')
