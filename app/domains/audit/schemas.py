import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    action: str
    outcome: str
    actor_id: uuid.UUID | None
    actor_role: str | None
    target_type: str | None
    target_id: str | None
    ip: str | None
    request_id: str | None
    details: dict


class AuditLogListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditLogResponse]
