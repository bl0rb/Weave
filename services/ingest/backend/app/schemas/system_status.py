from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

StatusValue = Literal['ok', 'degraded', 'down']
AreaKey = Literal['portal', 'processing', 'chat']


class AreaStatus(BaseModel):
    key: AreaKey
    status: StatusValue


class SystemStatusResponse(BaseModel):
    """What every signed-in person sees: functional areas only, no service names or addresses."""

    status: StatusValue
    checked_at: datetime
    areas: list[AreaStatus]


class ComponentStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    area: AreaKey
    status: StatusValue
    latency_ms: int | None
    detail: str | None
    target: str | None


class AdminSystemStatusResponse(SystemStatusResponse):
    components: list[ComponentStatusResponse]
