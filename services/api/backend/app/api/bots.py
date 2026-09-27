"""GET /v1/bots and GET /v1/bots/{id} -- authenticated, rate-limited,
read-only proxy to Weave-Runtime's internal bot registry (see
app/services/runtime_client.py). Weave-API owns no bot configuration of its
own (README's Nicht-Ziele: "Kein Bot-Management" -- bots are configured in
Weave-Runtime), so every call here is a live pass-through, never a locally
cached copy.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.ratelimit import enforce_rate_limit
from app.models.models import User
from app.services.runtime_client import RuntimeClientError, get_bot, list_bots, runtime_user

router = APIRouter(prefix='/v1', tags=['bots'], dependencies=[Depends(enforce_rate_limit)])


@router.get('/bots')
def get_bots(user: User = Depends(enforce_rate_limit)) -> list:
    """Only the bots the caller may chat with (ADR 0008)."""
    try:
        return list_bots(runtime_user(user))
    except RuntimeClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get('/bots/{bot_id}')
def get_bot_by_id(bot_id: str, user: User = Depends(enforce_rate_limit)) -> dict:
    """The full configuration of a bot the caller may chat with; any other
    bot is a 404, indistinguishable from one that doesn't exist."""
    try:
        if bot_id not in {bot.get('id') for bot in list_bots(runtime_user(user))}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Bot not found')
        return get_bot(bot_id)
    except RuntimeClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
