"""OAuth discovery, HTTP challenges and permission-bound MCP calls."""

import asyncio
import json
from unittest.mock import patch

import pytest
from httpx2 import ASGITransport, AsyncClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.mcp_auth import MCPOAuthMiddleware, oauth_transport_security
from app.mcp_server import list_collections, mcp_app, search
from app.services.scope import ScopeError, resolve_scope
from tests.conftest import fake_response

ISSUER = 'https://idp.example.com/realms/weave'
RESOURCE = 'https://weave.example.com/mcp'
JWT_TOKEN = 'header.payload.signature'
IDENTITY = {'active': True, 'user_id': 'user-1', 'username': 'alice', 'subject': 'user-1', 'teams': ['finance']}
COLLECTIONS = [{'slug': 'policies', 'name': 'Policies'}, {'slug': 'other', 'name': 'Other'}]


@pytest.fixture(autouse=True)
def oauth_config(monkeypatch):
    monkeypatch.setattr(settings, 'mcp_oauth_issuer', ISSUER)
    monkeypatch.setattr(settings, 'mcp_oauth_resource_url', RESOURCE)
    monkeypatch.setattr(settings, 'mcp_oauth_scopes', ['mcp.read'])


def request(method, path, **kwargs):
    async def run():
        async with AsyncClient(transport=ASGITransport(app=mcp_app), base_url='https://weave.example.com') as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def test_unauthenticated_initialize_challenges_before_mcp_protocol():
    response = request('POST', '/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'})
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == (
        'Bearer resource_metadata="https://weave.example.com/.well-known/oauth-protected-resource/mcp", scope="mcp.read"'
    )


@pytest.mark.parametrize('path', ['/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp'])
def test_public_resource_metadata_uses_configured_urls_not_host_header(path):
    response = request('GET', path, headers={'Host': 'attacker.example'})
    assert response.status_code == 200
    assert response.json() == {
        'resource': RESOURCE, 'authorization_servers': [ISSUER],
        'scopes_supported': ['mcp.read'], 'bearer_methods_supported': ['header'],
    }


@pytest.mark.parametrize(('identity', 'status', 'error'), [
    ({'active': False}, 401, 'invalid_token'),
    ({'active': False, 'error': 'insufficient_scope'}, 403, 'insufficient_scope'),
])
def test_rejected_access_tokens_return_oauth_http_errors(identity, status, error):
    with patch('app.services.scope.httpx.post', return_value=fake_response(200, identity)):
        response = request('POST', '/mcp', headers={'Authorization': f'Bearer {JWT_TOKEN}'})
    assert response.status_code == status
    assert f'error="{error}"' in response.headers['www-authenticate']


def test_introspection_failure_does_not_masquerade_as_login_failure():
    with patch('app.services.scope.httpx.post', return_value=fake_response(503, {})):
        response = request('POST', '/mcp', headers={'Authorization': f'Bearer {JWT_TOKEN}'})
    assert response.status_code == 503
    assert 'www-authenticate' not in response.headers


def test_scope_forwards_person_grants_and_current_db_teams_without_cache():
    with patch('app.services.scope.httpx.post', return_value=fake_response(200, IDENTITY)) as introspect, \
         patch('app.services.scope.httpx.get', return_value=fake_response(200, COLLECTIONS)) as retrieval:
        first = resolve_scope(f'Bearer {JWT_TOKEN}')
        assert first.kind == 'oauth'
        assert first.allowed_collections == ['policies', 'other']
        assert retrieval.call_args.kwargs['params'] == {'teams': ['finance'], 'user': 'user-1'}
        assert introspect.call_args.kwargs['json'] == {'token': JWT_TOKEN, 'issuer': ISSUER}
        introspect.return_value = fake_response(200, {'active': False})
        with pytest.raises(ScopeError):
            resolve_scope(f'Bearer {JWT_TOKEN}')
        assert introspect.call_count == 2


@pytest.mark.parametrize('field,value', [
    ('mcp_oauth_issuer', 'http://idp.example.com'),
    ('mcp_oauth_resource_url', 'http://weave.example.com/mcp'),
    ('mcp_oauth_resource_url', 'https://weave.example.com/mcp?secret=1'),
    ('mcp_oauth_scopes', ['mcp.read\nInjected: bad']),
])
def test_insecure_or_header_injecting_config_is_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_oauth_mcp_client_round_trip_uses_authenticated_request_scope():
    server = MCPServer(name='Weave OAuth test')
    server.add_tool(list_collections)
    server.add_tool(search)
    app = server.streamable_http_app(transport_security=oauth_transport_security())
    app.add_middleware(MCPOAuthMiddleware)

    async def run():
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url='https://weave.example.com',
                headers={'Authorization': f'Bearer {JWT_TOKEN}'},
            ) as http_client:
                async with streamable_http_client(RESOURCE, http_client=http_client) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        assert {t.name for t in (await session.list_tools()).tools} == {'list_collections', 'search'}
                        result = await session.call_tool('search', {'query': 'x', 'collection': 'forbidden'})
                        assert result.is_error is False
                        assert json.loads(result.content[0].text) == {'query': 'x', 'results': []}

    with patch('app.services.scope.httpx.post', return_value=fake_response(200, IDENTITY)) as post, \
         patch('app.services.scope.httpx.get', return_value=fake_response(200, COLLECTIONS)):
        asyncio.run(run())
        assert post.call_count > 0
        assert all(call.args[0].endswith('/mcp-oauth/introspect') for call in post.call_args_list)
