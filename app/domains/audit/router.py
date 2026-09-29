import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, and_, cast, false, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import paginate
from app.core.permissions import AUDIT_READ
from app.core.security import require_roles
from app.domains.audit.docs import LIST_AUDIT_DOCS
from app.domains.audit.models import AuditLog
from app.domains.audit.schemas import AuditLogListResponse
from app.domains.users.models import User

router = APIRouter(prefix="/audit-log", tags=["audit"])


@router.get("", response_model=AuditLogListResponse, **LIST_AUDIT_DOCS)
def list_audit_log(
    action: str | None = Query(default=None, max_length=50),
    outcome: str | None = Query(default=None, pattern="^(success|failure)$"),
    actor_id: uuid.UUID | None = Query(default=None),
    target_type: str | None = Query(default=None, max_length=30),
    target_id: str | None = Query(default=None, max_length=64),
    request_id: str | None = Query(default=None, max_length=64),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*AUDIT_READ)),
):
    # An organization reads the trail of its own people only, never another organization's: what they
    # did, and what was attempted against their accounts (a failed login has no actor, only a target).
    if actor.organization_id is None:
        query = select(AuditLog).where(false())
    else:
        own = select(User.id).where(User.organization_id == actor.organization_id)
        own_as_text = select(cast(User.id, String)).where(User.organization_id == actor.organization_id)
        query = select(AuditLog).where(or_(
            AuditLog.actor_id.in_(own),
            and_(AuditLog.target_type == "user", AuditLog.target_id.in_(own_as_text)),
        ))
    if action:
        query = query.where(AuditLog.action == action)
    if outcome:
        query = query.where(AuditLog.outcome == outcome)
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)
    if target_type:
        query = query.where(AuditLog.target_type == target_type)
    if target_id:
        query = query.where(AuditLog.target_id == target_id)
    if request_id:
        query = query.where(AuditLog.request_id == request_id)
    if since:
        query = query.where(AuditLog.occurred_at >= since)
    if until:
        query = query.where(AuditLog.occurred_at <= until)

    total, items = paginate(db, query, AuditLog.occurred_at.desc(), AuditLog.id, limit=limit, offset=offset)
    return AuditLogListResponse(total=total, limit=limit, offset=offset, items=items)
