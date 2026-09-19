"""Read the effective chat-provider configuration from Weave-Ingest.

The lookup intentionally happens per turn. It keeps Runtime pods stateless,
requires no invalidation bus, and makes an admin change visible to every
replica on its next request.
"""

from dataclasses import dataclass

import httpx

from app.core.config import settings


class ChatConfigUnavailable(Exception):
    """The configured central control plane could not provide a safe snapshot."""


@dataclass(frozen=True)
class ChatProviderSnapshot:
    enabled: bool
    base_url: str = ''
    model: str = ''
    api_key: str = ''
    timeout_seconds: float = 60.0
    temperature: float | None = None
    # Whether the centrally managed model actually supports tool/function
    # calls -- an admin-declared fact (Weave-Ingest's chat-provider config),
    # not something this client can probe. Defaults to False (the safe,
    # pre-existing behaviour) whenever the control plane doesn't send it,
    # which also covers talking to an older Ingest version that predates
    # this field. See app/services/chat.py's `agent_mode_supported`, which
    # is the sole reader.
    supports_tools: bool = False


def fetch_chat_provider() -> ChatProviderSnapshot | None:
    base_url = settings.chat_config_base_url.rstrip('/')
    token = settings.chat_config_service_token
    if not base_url and not token:
        return None
    if not base_url or not token:
        raise ChatConfigUnavailable('Zentrale Chat-Konfiguration ist unvollständig.')

    try:
        response = httpx.get(
            f'{base_url}/api/v1/internal/chat-provider',
            headers={'Authorization': f'Bearer {token}'},
            timeout=settings.chat_config_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise ChatConfigUnavailable('Zentrale Chat-Konfiguration ist vorübergehend nicht erreichbar.') from exc
    if response.status_code != 200:
        raise ChatConfigUnavailable('Zentrale Chat-Konfiguration ist vorübergehend nicht verfügbar.')
    try:
        body = response.json()
        if not isinstance(body.get('enabled'), bool):
            raise TypeError
        snapshot = ChatProviderSnapshot(
            enabled=body['enabled'],
            base_url=str(body.get('base_url') or ''),
            model=str(body.get('model') or ''),
            api_key=str(body.get('api_key') or ''),
            timeout_seconds=float(body.get('timeout_seconds', 60.0)),
            temperature=float(body['temperature']) if body.get('temperature') is not None else None,
            supports_tools=bool(body.get('supports_tools', False)),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ChatConfigUnavailable('Zentrale Chat-Konfiguration hat ungültige Daten geliefert.') from exc
    if snapshot.enabled and (not snapshot.base_url or not snapshot.model):
        raise ChatConfigUnavailable('Zentrale Chat-Konfiguration ist unvollständig.')
    return snapshot


def fetch_managed_bots() -> list[dict] | None:
    """Fetch managed bots and suppression entries from the control plane.

    ``None`` means standalone mode (both central settings absent).  An empty
    list is a valid configured response and leaves the bundled YAML bots as
    the roster; centrally managed ids override an identically named YAML bot.
    Deleted/disabled ids become minimal ``enabled=False`` entries so callers
    can suppress bundled YAML without storing state in Runtime replicas.
    """
    base_url = settings.chat_config_base_url.rstrip('/')
    token = settings.chat_config_service_token
    if not base_url and not token:
        return None
    if not base_url or not token:
        raise ChatConfigUnavailable('Zentrale Bot-Konfiguration ist unvollständig.')
    try:
        response = httpx.get(
            f'{base_url}/api/v1/internal/bots',
            headers={'Authorization': f'Bearer {token}'},
            timeout=settings.chat_config_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise ChatConfigUnavailable('Zentrale Bot-Konfiguration ist vorübergehend nicht erreichbar.') from exc
    if response.status_code != 200:
        raise ChatConfigUnavailable('Zentrale Bot-Konfiguration ist vorübergehend nicht verfügbar.')
    try:
        body = response.json()
        items = body['items']
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise TypeError
        disabled_ids = body.get('disabled_ids', [])
        if not isinstance(disabled_ids, list) or not all(isinstance(bot_id, str) and bot_id for bot_id in disabled_ids):
            raise TypeError
        return [*items, *({'id': bot_id, 'enabled': False} for bot_id in disabled_ids)]
    except (ValueError, TypeError, KeyError) as exc:
        raise ChatConfigUnavailable('Zentrale Bot-Konfiguration hat ungültige Daten geliefert.') from exc
