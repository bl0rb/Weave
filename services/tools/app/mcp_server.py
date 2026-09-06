"""The MCP server (Model Context Protocol, streamable-HTTP transport) --
Weave-Tools' primary interface for an MCP-speaking caller (n8n's own MCP
node, or any other MCP client). Exposes exactly two tools:

    list_collections() -> the caller's OWN readable collections.
    search(query, collection=None, top_k=None) -> hybrid-search hits.

Neither tool takes a user/team/allowed_collections argument of any kind --
each resolves its OWN Scope, fresh, from THIS call's Authorization header
(app/services/scope.py), exactly like the REST mirror (app/api/tools.py)
does; both transports call straight through to the exact same
app/services/tools.py functions, so there is only one implementation of the
actual scoping/search logic to reason about, not two.

Getting at that header: `Context.headers` (see mcp.server.mcpserver.context)
exposes the raw HTTP headers of the CURRENT tool call when the active
transport actually carries them -- true for streamable-HTTP (the only
transport this module uses), never true for stdio. A tool function
requests it by declaring a `ctx: Context` parameter; the SDK injects the
live Context object for that specific call, it is never something a caller
can pass or forge as an ordinary tool argument.

--- Why this isn't mounted inside app/main.py's own FastAPI app ----------

`MCPServer.streamable_http_app()` returns a Starlette app whose streamable-
HTTP route depends on a background session manager that is only started by
that SAME app's OWN ASGI `lifespan` protocol messages (see
mcp.server.streamable_http_manager.StreamableHTTPSessionManager.run) --
verified directly against this SDK version: calling the returned app
without ever driving its lifespan raises "Task group is not initialized.
Make sure to use run()." on the very first request. A real ASGI server
(uvicorn) sends those lifespan events automatically to whichever app it was
started with directly; nesting this app as a Starlette `Mount(...)` inside
`app/main.py`'s own FastAPI app would NOT forward FastAPI's own lifespan
into the mounted sub-app without extra wiring this SDK version does not
provide out of the box, and getting that wrong fails silently (every tool
call would 500 with the error above) rather than at startup. Two
independent HTTP servers -- this module's `mcp_app`, main.py's `app` -- run
directly by uvicorn is the simpler, correctly-lifespan-driven shape; see
this service's own README for how to run both.

Both apps are equally stateless (see app/services/scope.py's own docstring
for why neither caches anything across requests), so running only one of
them, or both side by side on different ports, is equally correct -- a
deployment that only ever gets called over MCP has no reason to also run
main.py's REST mirror, and vice versa.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from app.core.config import settings
from app.services.scope import (
    Scope,
    ScopeConfigurationError,
    ScopeError,
    resolve_scope,
    warn_if_delegation_secret_unconfigured,
)
from app.services.tools import list_collections_for_scope, search_for_scope

mcp_server: MCPServer[None] = MCPServer(name=settings.app_name)

# Logged once, at process start (this module is its own standalone
# uvicorn-run ASGI process -- see this module's own docstring), so a
# misconfigured WEAVE_DELEGATION_SECRET shows up immediately in the startup
# log rather than only after the first Delegations-Token verification
# attempt -- see warn_if_delegation_secret_unconfigured's own docstring.
warn_if_delegation_secret_unconfigured()


def _authorization_from_context(ctx: Context[Any, Any] | None) -> str | None:
    """Pull the raw `Authorization` header off the current MCP call.

    `ctx.headers` is a case-insensitive Mapping when the transport actually
    carries HTTP headers (streamable-HTTP does -- see this module's own
    docstring), so a single lowercase lookup finds the header regardless of
    how the client cased it. `None` (no `ctx`, or a transport/session with
    no headers at all) is passed straight through to resolve_scope(), which
    already treats a missing Authorization value as just another invalid
    credential.
    """
    if ctx is None:
        return None
    headers = ctx.headers
    if not headers:
        return None
    return headers.get('authorization')


def _resolve_scope(ctx: Context[Any, Any] | None) -> Scope:
    try:
        return resolve_scope(_authorization_from_context(ctx))
    except (ScopeConfigurationError, ScopeError) as exc:
        # Re-raised as a plain ValueError: an uncaught exception from a tool
        # function is what MCPServer's own dispatch turns into a
        # CallToolResult(isError=True) carrying that exception's str() as
        # the message (see mcp.server.mcpserver.tools.Tool.run) -- str(exc)
        # here is already the raised exception's own single generic message
        # (see scope.py's module docstring), never anything that
        # distinguishes *why* resolution failed. MCP tool errors carry no
        # HTTP-style status code the way app/api/deps.py's REST surface
        # does, so ScopeConfigurationError ('service misconfigured') and
        # ScopeError ('invalid or expired token') are told apart here only
        # by their message text -- not by branching into two different
        # exception-handling paths, since there is no status code for a
        # second path to set.
        raise ValueError(str(exc)) from None


@mcp_server.tool()
async def list_collections(ctx: Context[Any, Any] | None = None) -> list[dict]:
    """List the collections THIS caller may read (name, slug, description).
    Never returns a collection outside the caller's own resolved scope.
    Takes no arguments beyond the implicit MCP context: there is no
    user/team override to accept.
    """
    scope = _resolve_scope(ctx)
    return [collection.model_dump() for collection in list_collections_for_scope(scope)]


@mcp_server.tool()
async def search(
    query: str,
    collection: str | None = None,
    top_k: int | None = None,
    ctx: Context[Any, Any] | None = None,
) -> dict:
    """Hybrid search over THIS caller's own readable collections. Returns
    text excerpts with their source (document, page, collection).

    `collection`, if given, narrows the search to that one collection --
    but only when it already lies within the caller's own resolved scope; a
    `collection` outside that scope yields an empty result, never an error
    and never a hint that the collection exists. There is deliberately no
    `team`/`user_id`/`allowed_collections` argument at all: the caller's
    identity and scope come exclusively from THIS call's own Authorization
    header, never from a tool argument.
    """
    scope = _resolve_scope(ctx)
    response = search_for_scope(scope, query=query, collection=collection, top_k=top_k)
    return response.model_dump()


# The standalone ASGI app -- see this module's own docstring for why it is
# run directly by uvicorn rather than mounted inside app/main.py's app.
mcp_app = mcp_server.streamable_http_app()
