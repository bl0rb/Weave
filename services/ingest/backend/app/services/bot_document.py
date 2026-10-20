"""Mapping between a `ManagedBot` row (or Runtime's raw BotConfig dict) and
the portable bot document (app/schemas/bot_document.py,
contracts/bot-document.md) -- the admin export/import routes in
app/api/managed_bots.py are the only callers.

Invariant: exporting a managed bot yields exactly what Weave-Runtime's
`_managed_bot` (services/runtime/backend/app/services/botconfig.py) builds
from the internal projection, minus `permissions.teams`/`users` (grants
never travel) and minus the secret (`n8n.auth_token` becomes the
placeholder). The import direction is the inverse; what cannot be stored
is reported as a warning, never silently dropped.
"""

import json
from dataclasses import dataclass
from typing import Literal

import yaml
from pydantic import ValidationError

from app.models.models import ManagedBot
from app.schemas.bot_document import (
    DEFAULT_NO_CONTEXT_REPLY,
    SCHEMA_VERSION,
    SECRET_PLACEHOLDER,
    BotDocument,
    GuardConfig,
)
from app.schemas.managed_bots import BotConfigExtras, ManagedBotCreate, ManagedBotUpdate

MAX_DOCUMENT_BYTES = 256 * 1024

# Runtime's fixed values for a managed bot (`_managed_bot`): the chat
# provider is central, so a managed LLM bot never carries its own model.
MANAGED_LLM_MODEL = {'provider': 'fake', 'model': 'fake-chat'}
MANAGED_N8N_MODEL = {'provider': 'n8n', 'model': 'n8n-agent-flow'}
N8N_SYSTEM_PROMPT = 'Du führst Anfragen über den konfigurierten n8n-Workflow aus.'

EXTRA_GUARD_KEYS = ('clarify_max_options', 'clarify_question', 'clarify_ambiguous_margin', 'clarify_ambiguous_question')

WarningCode = Literal['missing_secret', 'dropped_field', 'permissions_reset']


@dataclass
class ImportWarning:
    code: WarningCode
    detail: str


class BotDocumentError(ValueError):
    """A file that is not a valid bot document; `errors` lists every
    readable reason (German, shown to the administrator as is)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__('; '.join(errors))
        self.errors = errors


def _validation_errors(exc: ValidationError) -> list[str]:
    errors = []
    for item in exc.errors():
        location = '.'.join(str(part) for part in item.get('loc', ())) or '(Dokument)'
        message = item.get('msg', '')
        if message.startswith('Value error, '):
            message = message[len('Value error, '):]
        errors.append(f'{location}: {message}')
    return errors


def row_to_document(row: ManagedBot) -> BotDocument:
    """Port of Runtime's `_managed_bot` over the row itself; the secret is
    replaced by the placeholder, grants are left out."""
    is_llm = row.kind == 'llm'
    extras = {key: value for key, value in (row.config_extras or {}).items() if value is not None}
    raw = {
        'schema_version': SCHEMA_VERSION,
        'id': row.id,
        'name': row.name,
        'description': row.description,
        'model': {
            **MANAGED_LLM_MODEL, 'temperature': row.temperature,
            'endpoint': row.llm_endpoint, 'endpoints': list(row.llm_endpoints or []),
        } if is_llm else dict(MANAGED_N8N_MODEL),
        'system_prompt': row.system_prompt if is_llm else N8N_SYSTEM_PROMPT,
        'retrieval': {
            'enabled': bool(row.retrieval_enabled) if is_llm else False,
            'filters': dict(row.retrieval_filters or {}),
            'collections': list(row.collections or []),
            'top_k': row.top_k,
            'final_k': row.final_k,
            'rerank': bool(row.rerank),
            'query_rewrite': row.query_rewrite,
            'include_uncollected': bool(row.include_uncollected) if is_llm else False,
        },
        'permissions': {'public': row.public},
        'guard': {
            'require_sources': bool(row.require_sources),
            'min_score': row.min_score,
            'clarify_below': row.clarify_below,
            'uncertainty_hint': bool(row.uncertainty_hint),
            'weak_context_below': row.weak_context_below,
            'no_context_reply': row.no_context_reply or DEFAULT_NO_CONTEXT_REPLY,
            **extras,
        },
        'n8n': {
            'webhook_url': row.webhook_url,
            'timeout_seconds': row.timeout_seconds,
            'streaming': bool(row.streaming),
            'auth_token': SECRET_PLACEHOLDER if row.auth_token_encrypted else None,
        } if not is_llm else None,
        'agent': dict(row.agent_config) if is_llm and row.agent_config else None,
    }
    return BotDocument.model_validate(raw)


def runtime_dict_to_document(config: dict) -> BotDocument:
    """A raw BotConfig dict from Runtime's `GET /internal/bot-configs`
    (a YAML bot): grants stripped, any token -- Runtime already masks it --
    replaced by the placeholder."""
    raw = json.loads(json.dumps(config))
    raw.pop('kind', None)
    permissions = raw.get('permissions') if isinstance(raw.get('permissions'), dict) else {}
    raw['permissions'] = {key: value for key, value in permissions.items() if key not in ('teams', 'users')}
    n8n = raw.get('n8n')
    if isinstance(n8n, dict) and n8n.get('auth_token'):
        n8n['auth_token'] = SECRET_PLACEHOLDER
    raw.setdefault('schema_version', SCHEMA_VERSION)
    return BotDocument.model_validate(raw)


def dump_document(doc: BotDocument, fmt: Literal['yaml', 'json']) -> bytes:
    """Serialise for download. `exclude_none` keeps the file to what was
    set; a token is always written as the placeholder, never in clear."""
    data = doc.model_dump(mode='json', exclude_none=True)
    if doc.n8n is not None and doc.n8n.auth_token is not None:
        data['n8n']['auth_token'] = SECRET_PLACEHOLDER
    # Grants never travel: an exported document has no teams/users at all
    # (both default to empty on load) rather than two empty lists.
    for key in ('teams', 'users'):
        if not data.get('permissions', {}).get(key):
            data.get('permissions', {}).pop(key, None)
    if fmt == 'json':
        return (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode('utf-8')


def parse_document(raw: bytes, filename: str) -> BotDocument:
    """YAML or JSON (JSON is YAML) into a validated document; every
    problem is a `BotDocumentError` with readable German reasons."""
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise BotDocumentError([f'{filename}: Die Datei ist größer als {MAX_DOCUMENT_BYTES // 1024} KiB.'])
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise BotDocumentError([f'{filename}: Die Datei ist kein UTF-8-Text.']) from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BotDocumentError([f'{filename}: Ungültiges YAML/JSON ({exc}).']) from exc
    if not isinstance(data, dict):
        raise BotDocumentError([f'{filename}: Erwartet wird ein Bot-Dokument (Objekt auf oberster Ebene).'])
    version = data.get('schema_version', SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise BotDocumentError([
            f'{filename}: schema_version {version!r} wird nicht unterstützt (erwartet: {SCHEMA_VERSION}).'
        ])
    try:
        return BotDocument.model_validate(data)
    except ValidationError as exc:
        raise BotDocumentError(_validation_errors(exc)) from exc


def _extras_from_guard(guard: GuardConfig) -> BotConfigExtras | None:
    """Only the clarify fields that differ from Runtime's defaults."""
    values = {
        key: getattr(guard, key)
        for key in EXTRA_GUARD_KEYS
        if getattr(guard, key) != GuardConfig.model_fields[key].default
    }
    return BotConfigExtras(**values) if values else None


def document_to_payload(
    doc: BotDocument,
    *,
    bot_id: str,
    existing_row: ManagedBot | None,
    as_update: bool,
) -> tuple[ManagedBotCreate | ManagedBotUpdate, list[ImportWarning]]:
    """Inverse of `row_to_document`. Grants are never imported (the bot
    starts with `public` from the document and no grants); a placeholder
    token is dropped and reported; what the row cannot store is reported."""
    warnings: list[ImportWarning] = []
    is_n8n = doc.model.provider == 'n8n'
    expected = MANAGED_N8N_MODEL if is_n8n else MANAGED_LLM_MODEL
    if (doc.model.provider, doc.model.model) != (expected['provider'], expected['model']) or doc.model.supports_tools is not None:
        warnings.append(ImportWarning('dropped_field', (
            f'model.provider/model ({doc.model.provider}/{doc.model.model}) wird nicht übernommen: '
            'verwaltete Bots antworten über den zentralen Chat-Provider (model.endpoint).'
        )))
    if doc.permissions.teams or doc.permissions.users:
        warnings.append(ImportWarning('permissions_reset', (
            'permissions.teams/users werden nicht importiert; Freigaben an Personen und Teams werden nach dem '
            'Import im Bot-Editor vergeben.'
        )))
    else:
        warnings.append(ImportWarning('permissions_reset', (
            'Der Bot wird ohne Freigaben angelegt' + (' und ist öffentlich.' if doc.permissions.is_public
            else '; nur Besitzer und Administratoren können ihn nutzen, bis Freigaben vergeben sind.')
        )))

    auth_token: str | None = None
    if is_n8n and doc.n8n is not None and doc.n8n.auth_token is not None:
        token = doc.n8n.auth_token.get_secret_value()
        if token == SECRET_PLACEHOLDER:
            keeps_token = (
                existing_row is not None and bool(existing_row.auth_token_encrypted)
                and existing_row.webhook_url == doc.n8n.webhook_url
            )
            warnings.append(ImportWarning('missing_secret', (
                f'n8n.auth_token enthält den Platzhalter {SECRET_PLACEHOLDER}. '
                + ('Das bereits hinterlegte Bearer-Token des Bots bleibt erhalten.' if keeps_token
                   else 'Das Bearer-Token muss nach dem Import im Bot-Editor hinterlegt werden.')
            )))
        else:
            auth_token = token
    if is_n8n and doc.system_prompt != N8N_SYSTEM_PROMPT:
        warnings.append(ImportWarning('dropped_field', 'system_prompt wird für n8n-Bots nicht gespeichert.'))
    if is_n8n and (doc.model.endpoint or doc.model.endpoints):
        warnings.append(ImportWarning('dropped_field', 'model.endpoint/endpoints gelten nicht für n8n-Bots.'))
    if is_n8n and doc.agent is not None:
        warnings.append(ImportWarning('dropped_field', 'agent wird für n8n-Bots nicht gespeichert.'))
    if is_n8n and doc.model.temperature is not None:
        warnings.append(ImportWarning('dropped_field', 'model.temperature gilt nicht für n8n-Bots.'))

    fields = {
        'kind': 'n8n' if is_n8n else 'llm',
        'name': doc.name,
        'description': doc.description,
        'enabled': existing_row.enabled if existing_row is not None else True,
        'webhook_url': doc.n8n.webhook_url if is_n8n and doc.n8n else None,
        'system_prompt': None if is_n8n else doc.system_prompt,
        'temperature': None if is_n8n else doc.model.temperature,
        'retrieval_enabled': False if is_n8n else doc.retrieval.enabled,
        'retrieval_filters': doc.retrieval.filters.model_dump(exclude_none=True),
        'top_k': doc.retrieval.top_k,
        'final_k': doc.retrieval.final_k,
        'rerank': doc.retrieval.rerank,
        'include_uncollected': doc.retrieval.include_uncollected,
        'streaming': doc.n8n.streaming if is_n8n and doc.n8n else False,
        'auth_token': auth_token,
        'timeout_seconds': doc.n8n.timeout_seconds if is_n8n and doc.n8n else 120,
        'public': doc.permissions.is_public,
        'grants': [],
        'collections': list(doc.retrieval.collections),
        'require_sources': doc.guard.require_sources,
        'no_context_reply': doc.guard.no_context_reply,
        'min_score': doc.guard.min_score,
        'clarify_below': doc.guard.clarify_below,
        'weak_context_below': doc.guard.weak_context_below,
        'query_rewrite': doc.retrieval.query_rewrite,
        'uncertainty_hint': doc.guard.uncertainty_hint,
        'agent': None if is_n8n else doc.agent,
        'config_extras': _extras_from_guard(doc.guard),
        'llm_endpoint': None if is_n8n else doc.model.endpoint,
        'llm_endpoints': [] if is_n8n else list(doc.model.endpoints),
    }
    try:
        payload = ManagedBotUpdate(**fields) if as_update else ManagedBotCreate(id=bot_id, **fields)
    except ValidationError as exc:
        raise BotDocumentError(_validation_errors(exc)) from exc
    return payload, warnings
