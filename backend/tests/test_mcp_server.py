"""app/mcp_server.py: the MCP tools' own scope resolution (exercised via
direct calls to the underlying functions, with a lightweight duck-typed
stand-in for the MCP Context -- `@mcp_server.tool()` returns the original
function unchanged, so this calls exactly the same code a real MCP call
would) plus one full wire-level round trip proving the server is genuinely
reachable over the MCP-over-HTTP protocol, not merely callable as plain
Python.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx2 import ASGITransport, AsyncClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.mcp_server import list_collections, mcp_app, search
from tests.conftest import fake_response, make_delegation_token


def _ctx(token: str | None):
    if token is None:
        return SimpleNamespace(headers={})
    return SimpleNamespace(headers={'authorization': f'Bearer {token}'})


# --- direct calls: scope resolution + tool logic wired correctly -------------


def test_list_collections_tool_uses_authorization_header_for_scope():
    token = make_delegation_token(team='kundenservice', collections=['handbuch'])
    retrieval_resp = fake_response(
        200, [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None, 'public': True}]
    )

    with patch('app.services.tools.httpx.get', return_value=retrieval_resp):
        result = asyncio.run(list_collections(ctx=_ctx(token)))

    assert result == [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None}]


def test_list_collections_tool_without_authorization_raises_value_error():
    with pytest.raises(ValueError):
        asyncio.run(list_collections(ctx=_ctx(None)))


def test_search_tool_intersects_collection_and_maps_result():
    token = make_delegation_token(collections=['handbuch'])
    retrieval_resp = fake_response(
        200,
        {
            'results': [
                {
                    'chunk_id': 1,
                    'document_id': 'doc-1',
                    'text': 'hallo',
                    'page_start': 2,
                    'page_end': 2,
                    'original_filename': 'handbuch.pdf',
                }
            ]
        },
    )
    with patch('app.services.tools.httpx.post', return_value=retrieval_resp) as mock_post:
        result = asyncio.run(search(query='hallo', collection='handbuch', top_k=None, ctx=_ctx(token)))

    assert result['results'][0]['text'] == 'hallo'
    assert result['results'][0]['source'] == {'document': 'handbuch.pdf', 'page': '2', 'collection': 'handbuch'}
    assert mock_post.call_args.kwargs['json']['allowed_collections'] == ['handbuch']


def test_search_tool_with_out_of_scope_collection_returns_empty_never_calls_retrieval():
    token = make_delegation_token(collections=['handbuch'])
    with patch('app.services.tools.httpx.post') as mock_post:
        result = asyncio.run(search(query='x', collection='fremde-collection', top_k=None, ctx=_ctx(token)))

    assert result == {'query': 'x', 'results': []}
    mock_post.assert_not_called()


def test_search_tool_signature_has_no_team_or_user_parameter_at_all():
    # Structural proof, not just behavioural: the tool's own signature
    # cannot even accept a team/user_id argument -- passing one raises a
    # TypeError (a direct call) or fails MCP-side schema validation (a real
    # tool call) before any scope logic ever runs.
    params = set(inspect.signature(search).parameters)
    assert params == {'query', 'collection', 'top_k', 'ctx'}
    with pytest.raises(TypeError):
        asyncio.run(search(query='x', team='attacker-team', ctx=_ctx(make_delegation_token())))  # type: ignore[call-arg]


# --- full wire round trip over the streamable-HTTP transport -----------------


def test_mcp_server_reachable_over_http_end_to_end():
    """Real MCP client <-> server round trip, in-process over the ASGI
    transport (no TCP socket needed): session initialize, tools/list, and a
    tools/call -- proving the server is genuinely reachable over the
    MCP-over-HTTP wire protocol with a real Authorization header, not just
    callable as plain Python functions (the direct-call tests above).
    """
    token = make_delegation_token(team='kundenservice', collections=['handbuch'])
    collections_resp = fake_response(
        200, [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None, 'public': True}]
    )

    async def _run():
        async with mcp_app.router.lifespan_context(mcp_app):
            transport = ASGITransport(app=mcp_app)
            http_client = AsyncClient(
                transport=transport,
                base_url='http://localhost:8000',
                headers={'authorization': f'Bearer {token}'},
            )
            async with streamable_http_client('http://localhost:8000/mcp', http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {t.name for t in tools.tools} == {'list_collections', 'search'}

                    with patch('app.services.tools.httpx.get', return_value=collections_resp):
                        result = await session.call_tool('list_collections', {})

                    assert result.is_error is False
                    assert result.structured_content == {
                        'result': [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None}]
                    }

    asyncio.run(_run())


def test_mcp_server_token_never_appears_in_a_scope_error_message():
    marker = 'SENTINEL-MCP-TOKEN-should-never-be-logged'
    # No dot in `marker` -> resolved as a Personal-Token, which would
    # otherwise reach out to Weave-API; mocked here purely so this test
    # never depends on real network access, not because the log-leak
    # guarantee itself differs between the two token kinds.
    with patch('app.services.scope.httpx.post', return_value=fake_response(200, {'active': False})):
        with pytest.raises(ValueError) as excinfo:
            asyncio.run(search(query='x', ctx=_ctx(marker)))
    assert marker not in str(excinfo.value)
