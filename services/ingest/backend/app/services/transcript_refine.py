"""Instruction-driven clean-up of a meeting transcript through the central
chat provider (the `transcript_instructions` upload option for `.vtt`).

The deterministic converter (app/services/vtt_transcript.py) only removes
what is safely recognisable as boilerplate. What else should go -- a
recurring "wie gesagt", the small talk of the first five minutes, a
speaker who should be renamed, "Du" rewritten as "Sie" -- differs per
team and per meeting, so the uploader states it in free text and the
admin-configured `default` ChatProviderConfig row applies it.

Guard rails: the request is scoped to editing (system prompt forbids
additions, summaries and answering the instructions as if they were
questions), the transcript is sent in turn-aligned chunks so one request
never exceeds `_CHUNK_CHARS`, and a transcript that comes back empty as
a whole fails the job loudly instead of silently dropping the meeting
(a single empty chunk is legitimate: "entferne den Smalltalk" may well
cover the whole first chunk). The provider
is reached through safe_fetch with the chat-LLM private-host allowlist,
same as the admin connection test.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import ChatProviderConfig
from app.services import safe_fetch as safe_fetch_module
from app.services import security
from app.services.chat_provider import chat_completions_url

logger = logging.getLogger(__name__)

MAX_INSTRUCTIONS_CHARS = 4000
_CHUNK_CHARS = 12_000
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_OUTPUT_TOKENS = 8192

_SYSTEM_PROMPT = (
    'Du bereitest ein Meeting-Transkript in Markdown nach den Anweisungen der Nutzerin oder des Nutzers auf. '
    'Du bist ein Redakteur, kein Gesprächspartner: Du beantwortest keine Fragen, fasst nicht zusammen, erfindest '
    'keine Inhalte und fügst keine Kommentare, Einleitungen oder Erklärungen hinzu. Wende ausschließlich die '
    'Anweisungen an und lass alles andere unverändert, einschließlich der Sprecherzeilen im Format '
    '`**Name** [hh:mm:ss]:` und der Markdown-Struktur. Wenn eine Anweisung auf einen Abschnitt nicht zutrifft, '
    'gib ihn unverändert zurück. Antworte nur mit dem aufbereiteten Transkript-Abschnitt.'
)


@dataclass(frozen=True)
class TranscriptRefinement:
    instructions: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    temperature: float | None
    provider_name: str


def normalize_instructions(raw: str) -> str:
    """Trim and length-check the uploader's instructions; '' when empty."""
    value = (raw or '').strip()
    if len(value) > MAX_INSTRUCTIONS_CHARS:
        raise ValueError(f'Aufbereitungs-Anweisungen dürfen höchstens {MAX_INSTRUCTIONS_CHARS} Zeichen lang sein')
    return value


def load_refinement(db: Session, instructions: str) -> TranscriptRefinement:
    """Resolve the `default` chat provider for a job that carries
    instructions. Raises RuntimeError with a user-facing message (it lands
    in job.error_message) when no usable provider is configured."""
    row = db.get(ChatProviderConfig, 'default')
    if row is None or not row.enabled or not row.base_url or not row.model:
        raise RuntimeError(
            'Aufbereitungs-Anweisungen benötigen einen aktivierten zentralen Chat-Provider (Administration › KI-Endpunkte)'
        )
    api_key = security.decrypt_chat_provider_api_key(row.api_key_encrypted) if row.api_key_encrypted else ''
    return TranscriptRefinement(
        instructions=instructions,
        base_url=row.base_url,
        model=row.model,
        api_key=api_key,
        timeout_seconds=float(row.timeout_seconds or 60.0),
        temperature=row.temperature,
        provider_name=row.name or 'default',
    )


def split_into_chunks(markdown: str, max_chars: int | None = None) -> list[str]:
    """Split on blank lines (turn boundaries in the rendered transcript) so
    no speaker turn is cut in half; a single oversized paragraph becomes
    its own chunk."""
    max_chars = _CHUNK_CHARS if max_chars is None else max_chars
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in markdown.split('\n\n'):
        extra = len(paragraph) + 2
        if current and current_len + extra > max_chars:
            chunks.append('\n\n'.join(current))
            current, current_len = [], 0
        current.append(paragraph)
        current_len += extra
    if current:
        chunks.append('\n\n'.join(current))
    return chunks


def _complete(refinement: TranscriptRefinement, chunk: str, part: int, total: int) -> str:
    user_content = (
        f'Anweisungen:\n{refinement.instructions}\n\n'
        f'Transkript-Abschnitt {part} von {total}:\n\n{chunk}'
    )
    payload: dict[str, object] = {
        'model': refinement.model,
        'messages': [
            {'role': 'system', 'content': _SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ],
        'max_tokens': _MAX_OUTPUT_TOKENS,
    }
    if refinement.temperature is not None:
        payload['temperature'] = refinement.temperature
    headers = {'Content-Type': 'application/json'}
    if refinement.api_key:
        headers['Authorization'] = f'Bearer {refinement.api_key}'
    label = refinement.provider_name
    try:
        response = safe_fetch_module.safe_fetch(
            chat_completions_url(refinement.base_url),
            method='POST',
            headers=headers,
            body=json.dumps(payload).encode(),
            timeout=refinement.timeout_seconds,
            max_bytes=_MAX_RESPONSE_BYTES,
            allowed_private_hosts=frozenset(settings.chat_llm_private_host_allowlist),
        )
    except safe_fetch_module.SafeFetchError as exc:
        # The base_url is admin-configured and may name an internal host:
        # keep it out of job.error_message, log-only via the chained exc.
        logger.warning('chat provider "%s" unreachable while refining transcript', label)
        raise RuntimeError(f'Chat-Provider "{label}" nicht erreichbar') from exc
    if response.status_code >= 400:
        raise RuntimeError(f'Chat-Provider "{label}" antwortet mit HTTP {response.status_code}')
    try:
        content = json.loads(response.body)['choices'][0]['message']['content']
        if not isinstance(content, str):
            raise TypeError
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f'Chat-Provider "{label}": Antwort entspricht nicht dem OpenAI-Chat-Format') from exc
    cleaned = content.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.strip('`').split('\n', 1)[-1].rstrip('`').strip()
    return cleaned


def refine_transcript(markdown: str, refinement: TranscriptRefinement) -> str:
    """Apply the instructions to the rendered transcript body, chunk by
    chunk. Raises RuntimeError when nothing at all comes back."""
    chunks = split_into_chunks(markdown)
    results = [_complete(refinement, chunk, index, len(chunks)) for index, chunk in enumerate(chunks, start=1)]
    body = '\n\n'.join(part for part in results if part).strip()
    if not body:
        raise RuntimeError(
            f'Chat-Provider "{refinement.provider_name}" hat ein leeres Transkript zurückgegeben; Anweisungen prüfen'
        )
    return body + '\n'
