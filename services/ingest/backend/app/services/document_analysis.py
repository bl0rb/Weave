"""On-demand LLM recommendation ("Handlungsempfehlung") for the portal review
page, answered by the central `default` chat provider.

Same provider access as app/services/transcript_refine.py: the admin-
configured ChatProviderConfig row, reached through safe_fetch with the
chat-LLM private-host allowlist. The model only sees the quality-gate result
and a capped excerpt of the markdown; it must answer with strict JSON that is
validated here, invalid items are dropped. Errors carry no base_url -- the
caller maps them to a generic API error and the details stay in the log.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import ChatProviderConfig
from app.services import safe_fetch as safe_fetch_module
from app.services import security
from app.services.chat_provider import chat_completions_url

logger = logging.getLogger(__name__)

_EXCERPT_CHARS = 12_000
_MAX_FINDINGS = 40
_MAX_RECOMMENDATIONS = 6
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_OUTPUT_TOKENS = 1500
_DEFAULT_TEMPERATURE = 0.2
_SEVERITIES = ('info', 'warning', 'critical')
_ACTIONS = ('release', 'edit', 'reprocess', 'skip', 'check')


class AnalysisNotConfigured(Exception):
    """No enabled central chat provider with base_url and model."""


class AnalysisFailed(Exception):
    """Provider unreachable, HTTP error or unusable answer."""


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    recommendations: list[dict[str, str]]


def _usable_row(db: Session) -> ChatProviderConfig | None:
    row = db.get(ChatProviderConfig, 'default')
    if row is None or not row.enabled or not row.base_url or not row.model:
        return None
    return row


def analysis_configured(db: Session) -> bool:
    return _usable_row(db) is not None


def _excerpt(markdown: str) -> str:
    if len(markdown) <= _EXCERPT_CHARS:
        return markdown
    half = _EXCERPT_CHARS // 2
    return f'{markdown[:half]}\n\n[... gekürzt ...]\n\n{markdown[-half:]}'


def _system_prompt(locale: str) -> str:
    language = 'Deutsch' if locale == 'de' else 'English'
    return (
        'You review the conversion quality of a document that was converted to Markdown (OCR/extraction) '
        'and advise the reviewer what to do before releasing it to the knowledge base. '
        'Answer ONLY with strict JSON, no prose and no code fences, in exactly this shape: '
        '{"summary": string, "recommendations": [{"severity": "info"|"warning"|"critical", '
        '"action": "release"|"edit"|"reprocess"|"skip"|"check", "title": string, "detail": string}]}. '
        f'At most {_MAX_RECOMMENDATIONS} recommendations, most important first. '
        'Base them only on the given quality data and excerpt; do not invent content. '
        f'Write summary, titles and details in {language}.'
    )


def _user_prompt(quality: dict[str, Any], findings: list[dict[str, Any]], markdown: str) -> str:
    data = {
        'grade': quality.get('grade'),
        'score': quality.get('score'),
        'recommendation': quality.get('recommendation'),
        'signals': quality.get('signals'),
        'issues': quality.get('issues'),
        'findings': findings[:_MAX_FINDINGS],
        'findings_total': len(findings),
    }
    return (
        f'Quality gate result:\n{json.dumps(data, ensure_ascii=False, default=str)}\n\n'
        f'Markdown (excerpt):\n{_excerpt(markdown)}'
    )


def _strip_fences(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.strip('`').split('\n', 1)[-1].rstrip('`').strip()
    return cleaned


def _parse(content: str) -> AnalysisResult:
    try:
        data = json.loads(_strip_fences(content))
    except ValueError as exc:
        raise AnalysisFailed('answer is not JSON') from exc
    if not isinstance(data, dict) or not isinstance(data.get('summary'), str) or not data['summary'].strip():
        raise AnalysisFailed('answer has no summary')
    recommendations: list[dict[str, str]] = []
    raw = data.get('recommendations')
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        title, detail = item.get('title'), item.get('detail')
        if item.get('severity') not in _SEVERITIES or item.get('action') not in _ACTIONS:
            continue
        if not isinstance(title, str) or not title.strip() or not isinstance(detail, str):
            continue
        recommendations.append(
            {'severity': item['severity'], 'action': item['action'], 'title': title.strip(), 'detail': detail.strip()}
        )
        if len(recommendations) >= _MAX_RECOMMENDATIONS:
            break
    return AnalysisResult(summary=data['summary'].strip(), recommendations=recommendations)


def analyze_document(
    db: Session, *, quality: dict[str, Any], findings: list[dict[str, Any]], markdown: str, locale: str
) -> AnalysisResult:
    """Ask the central chat provider for a recommendation.
    Raises AnalysisNotConfigured / AnalysisFailed."""
    row = _usable_row(db)
    if row is None:
        raise AnalysisNotConfigured
    api_key = security.decrypt_chat_provider_api_key(row.api_key_encrypted) if row.api_key_encrypted else ''
    payload: dict[str, object] = {
        'model': row.model,
        'messages': [
            {'role': 'system', 'content': _system_prompt(locale)},
            {'role': 'user', 'content': _user_prompt(quality, findings, markdown)},
        ],
        'max_tokens': _MAX_OUTPUT_TOKENS,
        'temperature': row.temperature if row.temperature is not None else _DEFAULT_TEMPERATURE,
    }
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    try:
        response = safe_fetch_module.safe_fetch(
            chat_completions_url(row.base_url),
            method='POST',
            headers=headers,
            body=json.dumps(payload).encode(),
            timeout=float(row.timeout_seconds or 60.0),
            max_bytes=_MAX_RESPONSE_BYTES,
            allowed_private_hosts=frozenset(settings.chat_llm_private_host_allowlist),
        )
    except safe_fetch_module.SafeFetchError as exc:
        # base_url is admin-configured and may name an internal host: log only.
        logger.warning('chat provider "%s" unreachable for document analysis', row.name or 'default')
        raise AnalysisFailed('provider unreachable') from exc
    if response.status_code >= 400:
        logger.warning('chat provider "%s" answered HTTP %s for document analysis', row.name or 'default', response.status_code)
        raise AnalysisFailed(f'HTTP {response.status_code}')
    try:
        content = json.loads(response.body)['choices'][0]['message']['content']
        if not isinstance(content, str):
            raise TypeError
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning('chat provider "%s" returned a non-OpenAI answer for document analysis', row.name or 'default')
        raise AnalysisFailed('unexpected response format') from exc
    try:
        return _parse(content)
    except AnalysisFailed:
        logger.warning('chat provider "%s" returned an unusable analysis', row.name or 'default')
        raise
