import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import false
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.network import enforce_admin_network
from app.core.permissions import AUDIT_READ
from app.core.rate_limit import admin_limit, limiter
from app.core.security import require_capability, require_roles
from app.domains.audit import queries
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.audit.docs import ADMIN_LIST_AUDIT_DOCS, LIST_AUDIT_DOCS
from app.domains.audit.queries import AuditFilters
from app.domains.audit.schemas import AuditLogListResponse
from app.domains.users.models import User

router = APIRouter(prefix="/audit-log", tags=["audit"])
admin_router = APIRouter(prefix="/admin/audit-log", tags=["admin"], dependencies=[Depends(enforce_admin_network)])


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
    # An organization reads the trail of its own people only, never another organization's. Without an
    # organization there is nothing to read (fail closed).
    scope = queries.organization_scope(actor.organization_id) if actor.organization_id is not None else false()
    filters = AuditFilters(action, outcome, actor_id, None, target_type, target_id, request_id, since, until)
    total, items = queries.search(db, filters, scope=scope, limit=limit, offset=offset)
    return AuditLogListResponse(total=total, limit=limit, offset=offset, items=items)


@admin_router.get("", response_model=AuditLogListResponse, **ADMIN_LIST_AUDIT_DOCS)
@limiter.limit(admin_limit)
def admin_list_audit_log(
    request: Request,
    action: str | None = Query(default=None, max_length=50),
    outcome: str | None = Query(default=None, pattern="^(success|failure)$"),
    actor_id: uuid.UUID | None = Query(default=None),
    actor_role: str | None = Query(default=None, max_length=20),
    organization_id: uuid.UUID | None = Query(default=None),
    target_type: str | None = Query(default=None, max_length=30),
    target_id: str | None = Query(default=None, max_length=64),
    request_id: str | None = Query(default=None, max_length=64),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(require_capability("audit.read")),
):
    filters = AuditFilters(action, outcome, actor_id, actor_role, target_type, target_id, request_id, since, until)
    scope = queries.organization_scope(organization_id) if organization_id is not None else None
    total, items = queries.search(db, filters, scope=scope, limit=limit, offset=offset)

    # Reading everything is itself worth a trail: which filters were used (not their values), and by whom.
    used = filters.used() + (["organization_id"] if organization_id is not None else [])
    audit.record(db, Action.ADMIN_AUDIT_VIEWED, actor=actor, target_type="audit_log",
                 details={"filters": sorted(used), "limit": limit, "offset": offset})
    db.commit()
    return AuditLogListResponse(total=total, limit=limit, offset=offset, items=items)
