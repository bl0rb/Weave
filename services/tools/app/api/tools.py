"""REST mirror of the two MCP tools (app/mcp_server.py) -- the path n8n (or
any other caller that doesn't want to speak MCP) uses instead. Every route
here sits behind `require_tools_service_token` (app/api/deps.py, checked at
the router level since both routes are just as much this "REST tool
surface"), and resolves its own Scope via `get_scope` on top of the exact
same app/services/scope.py:resolve_scope() the MCP tools call -- there is
no separate access-control logic in this module at all, it is a thin HTTP
shell around app/services/tools.py, exactly like app/mcp_server.py is.
"""

from fastapi import APIRouter, Depends

from app.api.deps import get_scope, require_tools_service_token
from app.schemas.tools import CollectionOut, SearchToolRequest, SearchToolResponse
from app.services.scope import Scope
from app.services.tools import list_collections_for_scope, search_for_scope

router = APIRouter(prefix='/api/v1/tools', dependencies=[Depends(require_tools_service_token)])


@router.get('/collections', response_model=list[CollectionOut])
def list_collections(scope: Scope = Depends(get_scope)) -> list[CollectionOut]:
    return list_collections_for_scope(scope)


@router.post('/search', response_model=SearchToolResponse)
def search(request: SearchToolRequest, scope: Scope = Depends(get_scope)) -> SearchToolResponse:
    return search_for_scope(scope, query=request.query, collection=request.collection, top_k=request.top_k)
