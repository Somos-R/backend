"""Reading the audit trail: filters, organization scope and pagination, shared by every viewer."""
import uuid
from dataclasses import dataclass, fields
from datetime import datetime

from sqlalchemy import ColumnElement, String, and_, cast, or_, select
from sqlalchemy.orm import Session

from app.core.pagination import paginate
from app.domains.audit.models import AuditLog
from app.domains.users.models import User


@dataclass
class AuditFilters:
    action: str | None = None
    outcome: str | None = None
    actor_id: uuid.UUID | None = None
    actor_role: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    request_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None

    def used(self) -> list[str]:
        """The names of the filters that were set (never their values)."""
        return [f.name for f in fields(self) if getattr(self, f.name) is not None]


def organization_scope(organization_id: uuid.UUID) -> ColumnElement[bool]:
    """Events that belong to an organization: what its people did, and what was attempted against its
    accounts (a failed login has no actor, only the account it targeted)."""
    people = select(User.id).where(User.organization_id == organization_id)
    people_as_text = select(cast(User.id, String)).where(User.organization_id == organization_id)
    return or_(
        AuditLog.actor_id.in_(people),
        and_(AuditLog.target_type == "user", AuditLog.target_id.in_(people_as_text)),
    )


def search(
    db: Session, filters: AuditFilters, *, scope: ColumnElement[bool] | None, limit: int, offset: int
) -> tuple[int, list[AuditLog]]:
    """One page of events, newest first, restricted to `scope` (None = the whole trail)."""
    query = select(AuditLog)
    if scope is not None:
        query = query.where(scope)
    if filters.action:
        query = query.where(AuditLog.action == filters.action)
    if filters.outcome:
        query = query.where(AuditLog.outcome == filters.outcome)
    if filters.actor_id:
        query = query.where(AuditLog.actor_id == filters.actor_id)
    if filters.actor_role:
        query = query.where(AuditLog.actor_role == filters.actor_role)
    if filters.target_type:
        query = query.where(AuditLog.target_type == filters.target_type)
    if filters.target_id:
        query = query.where(AuditLog.target_id == filters.target_id)
    if filters.request_id:
        query = query.where(AuditLog.request_id == filters.request_id)
    if filters.since:
        query = query.where(AuditLog.occurred_at >= filters.since)
    if filters.until:
        query = query.where(AuditLog.occurred_at <= filters.until)
    return paginate(db, query, AuditLog.occurred_at.desc(), AuditLog.id, limit=limit, offset=offset)
