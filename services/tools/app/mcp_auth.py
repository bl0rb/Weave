"""OAuth resource discovery and per-request authentication for MCP HTTP."""

import json
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.services.scope import ScopeConfigurationError, ScopeError, ScopeInsufficientScopeError, resolve_scope


def oauth_transport_security() -> TransportSecuritySettings | None:
    if not settings.mcp_oauth_resource_url:
        return None
    url = urlsplit(settings.mcp_oauth_resource_url)
    return TransportSecuritySettings(
        allowed_hosts=[url.netloc, 'localhost:*', '127.0.0.1:*', '[::1]:*'],
        allowed_origins=[f'{url.scheme}://{url.netloc}'],
    )


class MCPOAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not settings.mcp_oauth_issuer:
            await self.app(scope, receive, send)
            return
        if not settings.mcp_oauth_resource_url or not settings.tools_introspection_token:
            await JSONResponse({'error': 'service misconfigured'}, status_code=503)(scope, receive, send)
            return

        resource = settings.mcp_oauth_resource_url
        url = urlsplit(resource)
        metadata_path = '/.well-known/oauth-protected-resource' + url.path
        if scope['path'] in ('/.well-known/oauth-protected-resource', metadata_path):
            response = JSONResponse({
                'resource': resource,
                'authorization_servers': [settings.mcp_oauth_issuer],
                'scopes_supported': settings.mcp_oauth_scopes,
                'bearer_methods_supported': ['header'],
            }, headers={'Cache-Control': 'no-store'})
            await response(scope, receive, send)
            return
        if scope['path'].rstrip('/') != '/mcp':
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        authorization = request.headers.get('authorization')
        try:
            resolved = await run_in_threadpool(resolve_scope, authorization)
        except ScopeConfigurationError:
            await JSONResponse({'error': 'service misconfigured'}, status_code=503)(scope, receive, send)
            return
        except ScopeError as exc:
            # A transport-level 401 lets VS Code discover OAuth before any
            # protocol initialization. Never redirect MCP calls to HTML.
            metadata_url = f'{url.scheme}://{url.netloc}{metadata_path}'
            challenge = f'Bearer resource_metadata={json.dumps(metadata_url)}, scope={json.dumps(" ".join(settings.mcp_oauth_scopes))}'
            insufficient = isinstance(exc, ScopeInsufficientScopeError)
            if insufficient:
                challenge += ', error="insufficient_scope"'
            elif authorization:
                challenge += ', error="invalid_token"'
            await JSONResponse(
                {'error': 'insufficient scope' if insufficient else 'invalid or expired token'},
                status_code=403 if insufficient else 401,
                headers={'WWW-Authenticate': challenge, 'Cache-Control': 'no-store'},
            )(scope, receive, send)
            return

        # Reuse only within this HTTP request. Every new request resolves
        # identity and permissions anew, including existing MCP sessions.
        scope.setdefault('state', {})['weave_scope'] = resolved
        await self.app(scope, receive, send)
