"""GET /v1/bots and GET /v1/bots/{id} -- authenticated, rate-limited,
read-only proxy to Weave-Runtime's internal bot registry (see
app/services/runtime_client.py). Weave-API owns no bot configuration of its
own (README's Nicht-Ziele: "Kein Bot-Management" -- bots are configured in
Weave-Runtime), so every call here is a live pass-through, never a locally
cached copy.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.ratelimit import enforce_rate_limit
from app.services.runtime_client import RuntimeClientError, get_bot, list_bots

router = APIRouter(prefix='/v1', tags=['bots'], dependencies=[Depends(enforce_rate_limit)])


@router.get('/bots')
def get_bots() -> list:
    try:
        return list_bots()
    except RuntimeClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get('/bots/{bot_id}')
def get_bot_by_id(bot_id: str) -> dict:
    try:
        return get_bot(bot_id)
    except RuntimeClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
