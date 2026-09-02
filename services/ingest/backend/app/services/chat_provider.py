"""Central OpenAI-compatible chat provider configuration helpers.

The database row belongs to Ingest's administration control plane. Runtime
remains stateless and consumes only the authenticated internal projection.
Outbound connection tests use ``safe_fetch`` so an admin-entered URL cannot
turn the API into an unrestricted network probe.
"""

import json
import logging
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, status

from app.core.config import settings
from app.services import safe_fetch as safe_fetch_module

logger = logging.getLogger(__name__)

_MAX_TEST_RESPONSE_BYTES = 1024 * 1024


def normalize_base_url(raw: str) -> str:
    value = raw.strip()
    if any(ord(char) < 32 or ord(char) == 127 or char == '\\' for char in value):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Endpoint-Adresse ist ungültig')
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https'):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Endpoint muss http oder https verwenden')
    if not parts.hostname:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Endpoint muss einen Host enthalten')
    if parts.username or parts.password:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Endpoint darf keine Zugangsdaten enthalten')
    if parts.query or parts.fragment:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Endpoint darf keine Query oder Fragment enthalten')
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def chat_completions_url(base_url: str) -> str:
    """Accept both provider roots and conventional bases ending in /v1."""
    normalized = base_url.rstrip('/')
    return f'{normalized}/chat/completions' if normalized.endswith('/v1') else f'{normalized}/v1/chat/completions'


def test_connection(*, base_url: str, model: str, api_key: str, timeout_seconds: float) -> dict[str, object]:
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': 'Antworte nur mit OK.'}],
        'temperature': 0,
        'max_tokens': 8,
    }
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    started = time.monotonic()
    try:
        response = safe_fetch_module.safe_fetch(
            chat_completions_url(base_url),
            method='POST',
            headers=headers,
            body=json.dumps(payload).encode(),
            timeout=timeout_seconds,
            max_bytes=_MAX_TEST_RESPONSE_BYTES,
            allowed_private_hosts=frozenset(settings.chat_llm_private_host_allowlist),
        )
    except safe_fetch_module.SafeFetchError:
        logger.warning('chat provider test failed: endpoint unreachable or not permitted')
        return {'ok': False, 'detail': 'Endpoint nicht erreichbar oder nicht erlaubt', 'latency_ms': int((time.monotonic() - started) * 1000)}
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.warning('chat provider test raised %s', type(exc).__name__)
        return {'ok': False, 'detail': 'Verbindungstest fehlgeschlagen', 'latency_ms': int((time.monotonic() - started) * 1000)}

    latency_ms = int((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        logger.warning('chat provider test returned HTTP %s', response.status_code)
        return {'ok': False, 'detail': f'Endpoint antwortet mit HTTP {response.status_code}', 'latency_ms': latency_ms}
    try:
        body = json.loads(response.body)
        content = body['choices'][0]['message']['content']
        if not isinstance(content, str):
            raise TypeError
    except (ValueError, KeyError, IndexError, TypeError):
        return {'ok': False, 'detail': 'Antwort entspricht nicht dem OpenAI-Chat-Format', 'latency_ms': latency_ms}
    return {'ok': True, 'detail': 'Verbindung und Modell funktionieren', 'latency_ms': latency_ms}
