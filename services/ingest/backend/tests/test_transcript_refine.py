"""Instruction-driven transcript clean-up (app/services/transcript_refine.py)."""

import json
from dataclasses import dataclass

import pytest

from app.services import safe_fetch as safe_fetch_module
from app.services import transcript_refine
from app.services.transcript_refine import TranscriptRefinement, split_into_chunks


@dataclass
class _Response:
    status_code: int
    body: bytes


def _refinement(**overrides) -> TranscriptRefinement:
    values = dict(
        instructions='Kürze "Max Mustermann" zu "Max".',
        base_url='https://llm.example.com/v1',
        model='gpt-test',
        api_key='sk-test',
        timeout_seconds=30.0,
        temperature=0.0,
        provider_name='Zentral',
    )
    values.update(overrides)
    return TranscriptRefinement(**values)


def _capture(monkeypatch, responder):
    calls: list[dict] = []

    def fake_fetch(url, *, method, headers, body, timeout, max_bytes, allowed_private_hosts):
        payload = json.loads(body)
        calls.append({'url': url, 'headers': headers, 'payload': payload, 'timeout': timeout})
        return responder(payload)

    monkeypatch.setattr(safe_fetch_module, 'safe_fetch', fake_fetch)
    return calls


def _chat_reply(text: str) -> _Response:
    return _Response(200, json.dumps({'choices': [{'message': {'content': text}}]}).encode())


def test_split_into_chunks_keeps_turns_intact():
    turns = [f'**A** [00:00:{index:02d}]: ' + 'x' * 50 for index in range(10)]
    chunks = split_into_chunks('\n\n'.join(turns), max_chars=200)
    assert len(chunks) > 1
    assert '\n\n'.join(chunks) == '\n\n'.join(turns)
    assert all(chunk.startswith('**A**') for chunk in chunks)


def test_refine_transcript_sends_instructions_and_reassembles_chunks(monkeypatch):
    def responder(payload):
        user = payload['messages'][1]['content']
        return _chat_reply(user.split('\n\n', 2)[2].replace('Max Mustermann', 'Max'))

    calls = _capture(monkeypatch, responder)
    monkeypatch.setattr(transcript_refine, '_CHUNK_CHARS', 120)
    body = '\n\n'.join(f'**Max Mustermann** [00:00:{index:02d}]: Satz Nummer {index}.' for index in range(6))

    result = transcript_refine.refine_transcript(body, _refinement())

    assert len(calls) > 1
    first = calls[0]
    assert first['url'] == 'https://llm.example.com/v1/chat/completions'
    assert first['headers']['Authorization'] == 'Bearer sk-test'
    assert first['payload']['model'] == 'gpt-test'
    assert first['payload']['temperature'] == 0.0
    assert first['payload']['messages'][0]['role'] == 'system'
    assert 'Anweisungen:\nKürze "Max Mustermann" zu "Max".' in first['payload']['messages'][1]['content']
    assert f'Transkript-Abschnitt 1 von {len(calls)}' in first['payload']['messages'][1]['content']
    assert result.count('**Max** [') == 6
    assert 'Mustermann' not in result


def test_refine_transcript_strips_code_fences_and_tolerates_an_emptied_chunk(monkeypatch):
    replies = iter(['```markdown\n**Max** [00:00:00]: Bleibt.\n```', ''])
    _capture(monkeypatch, lambda payload: _chat_reply(next(replies)))
    monkeypatch.setattr(transcript_refine, '_CHUNK_CHARS', 40)

    result = transcript_refine.refine_transcript('**Max** [00:00:00]: Bleibt.\n\n**Max** [00:00:05]: Smalltalk weg.', _refinement())

    assert result == '**Max** [00:00:00]: Bleibt.\n'


def test_refine_transcript_fails_when_everything_comes_back_empty(monkeypatch):
    _capture(monkeypatch, lambda payload: _chat_reply('   '))
    with pytest.raises(RuntimeError, match='leeres Transkript'):
        transcript_refine.refine_transcript('**Max** [00:00:00]: Hallo.', _refinement())


def test_refine_transcript_reports_http_errors_and_unreachable_provider_by_label(monkeypatch):
    _capture(monkeypatch, lambda payload: _Response(502, b'bad gateway'))
    with pytest.raises(RuntimeError, match='"Zentral" antwortet mit HTTP 502'):
        transcript_refine.refine_transcript('**Max** [00:00:00]: Hallo.', _refinement())

    def unreachable(*args, **kwargs):
        raise safe_fetch_module.SafeFetchError('https://llm.example.com/v1/chat/completions blocked')

    monkeypatch.setattr(safe_fetch_module, 'safe_fetch', unreachable)
    with pytest.raises(RuntimeError) as excinfo:
        transcript_refine.refine_transcript('**Max** [00:00:00]: Hallo.', _refinement())
    assert str(excinfo.value) == 'Chat-Provider "Zentral" nicht erreichbar'


def test_normalize_instructions_limits_length():
    assert transcript_refine.normalize_instructions('  entferne X \n') == 'entferne X'
    assert transcript_refine.normalize_instructions('') == ''
    with pytest.raises(ValueError):
        transcript_refine.normalize_instructions('x' * (transcript_refine.MAX_INSTRUCTIONS_CHARS + 1))


def test_load_refinement_requires_an_enabled_default_provider():
    from app.models.models import ChatProviderConfig
    from app.services import security
    from conftest import TestingSessionLocal

    db = TestingSessionLocal()
    try:
        db.query(ChatProviderConfig).delete()
        db.commit()
        with pytest.raises(RuntimeError, match='zentralen Chat-Provider'):
            transcript_refine.load_refinement(db, 'entferne X')

        db.add(ChatProviderConfig(
            id='default', name='Zentral', enabled=True, base_url='https://llm.example.com/v1', model='gpt-test',
            api_key_encrypted=security.encrypt_chat_provider_api_key('sk-live'), timeout_seconds=45.0, temperature=0.2,
        ))
        db.commit()
        refinement = transcript_refine.load_refinement(db, 'entferne X')
        assert refinement == TranscriptRefinement(
            instructions='entferne X', base_url='https://llm.example.com/v1', model='gpt-test', api_key='sk-live',
            timeout_seconds=45.0, temperature=0.2, provider_name='Zentral',
        )

        row = db.get(ChatProviderConfig, 'default')
        row.enabled = False
        db.commit()
        with pytest.raises(RuntimeError, match='zentralen Chat-Provider'):
            transcript_refine.load_refinement(db, 'entferne X')
    finally:
        db.query(ChatProviderConfig).delete()
        db.commit()
        db.close()
