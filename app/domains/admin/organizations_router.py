import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.network import enforce_admin_network
from app.core.rate_limit import admin_limit, limiter
from app.core.security import require_capability
from app.domains.admin import organizations_service as service
from app.domains.admin.users_schemas import AdminUserSummary
from app.domains.organizations.enums import (
    LinkStatus,
    OrganizationStatus,
    OrganizationType,
)
from app.domains.users.models import User

# Needs the `organizations.review` capability, a backoffice token and an allowed network.
router = APIRouter(prefix="/admin/organizations", tags=["admin"], dependencies=[Depends(enforce_admin_network)])
Reviewer = Depends(require_capability("organizations.review"))


class OrganizationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: OrganizationType
    status: OrganizationStatus
    legal_name: str
    tax_id: str | None
    city: str | None
    contact_email: str | None
    created_at: datetime
    approved_at: datetime | None
    staff_count: int
    active_links: int


class OrganizationListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[OrganizationSummary]


class OtherSide(BaseModel):
    id: uuid.UUID
    type: OrganizationType
    legal_name: str
    city: str | None


class LinkEntry(BaseModel):
    id: uuid.UUID
    status: LinkStatus
    requested_at: datetime
    decided_at: datetime | None
    rejection_reason: str | None
    other: OtherSide  # the organization on the other end: an Association for an ECA, an ECA for an Association


class WarehouseEntry(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool


class OrganizationDetail(OrganizationSummary):
    legal_representative: str | None
    contact_phone: str | None
    address: str | None
    updated_at: datetime
    staff: list[AdminUserSummary]
    links: list[LinkEntry]
    recyclers_count: int | None  # Associations only
    warehouses: list[WarehouseEntry] | None  # ECAs only


@router.get("", response_model=OrganizationListResponse)
@limiter.limit(admin_limit)
def list_organizations(
    request: Request,
    q: str | None = Query(default=None, max_length=100, description="Nombre, NIT, correo o representante (sin mayúsculas ni tildes)"),
    type: OrganizationType | None = Query(default=None),
    status: OrganizationStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Reviewer,
):
    """Every organization, with how many people work in it and how many active links it has."""
    total, items = service.list_organizations(db, q=q, type_=type, status_=status, limit=limit, offset=offset)
    return OrganizationListResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/{organization_id}", response_model=OrganizationDetail)
@limiter.limit(admin_limit)
def get_organization(
    request: Request, organization_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Reviewer,
):
    """The profile with its staff, its links and (recyclers of an Association, warehouses of an ECA). Opening it is audited."""
    return service.get_detail(db, actor, organization_id)
