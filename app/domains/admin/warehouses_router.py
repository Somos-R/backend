import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import ApiError
from app.core.network import enforce_admin_network
from app.core.pagination import paginate
from app.core.rate_limit import admin_limit, limiter
from app.core.security import require_capability
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.inventory.models import Warehouse
from app.domains.organizations.enums import OrganizationStatus, OrganizationType
from app.domains.organizations.models import Organization
from app.domains.users.models import User

router = APIRouter(prefix="/admin/warehouses", tags=["admin"], dependencies=[Depends(enforce_admin_network)])
Manager = Depends(require_capability("catalogs.manage"))


class AdminWarehouse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    address: str | None
    is_active: bool
    organization_id: uuid.UUID | None


class AdminWarehouseList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AdminWarehouse]


class AssignWarehouseOrganization(BaseModel):
    organization_id: uuid.UUID


@router.get("", response_model=AdminWarehouseList)
@limiter.limit(admin_limit)
def list_warehouses(
    request: Request,
    organization_id: uuid.UUID | None = Query(default=None),
    unassigned: bool | None = Query(default=None, description="true: only warehouses that have no owner yet"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Manager,
):
    """Every warehouse, across ECAs. Warehouses with no owner are invisible to customers until assigned."""
    query = select(Warehouse)
    if organization_id is not None:
        query = query.where(Warehouse.organization_id == organization_id)
    if unassigned is not None:
        query = query.where(Warehouse.organization_id.is_(None) if unassigned else Warehouse.organization_id.is_not(None))
    total, items = paginate(db, query, Warehouse.name, Warehouse.id, limit=limit, offset=offset)
    return AdminWarehouseList(total=total, limit=limit, offset=offset, items=items)


@router.put("/{warehouse_id}/organization", response_model=AdminWarehouse)
@limiter.limit(admin_limit)
def assign_owner(
    request: Request,
    warehouse_id: uuid.UUID,
    body: AssignWarehouseOrganization,
    db: Session = Depends(get_db),
    actor: User = Manager,
):
    """Give an owner ECA to a warehouse that has none. An owner cannot be changed: that would hand the
    warehouse's inventory and history to another organization."""
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise ApiError("warehouse_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Bodega no encontrada")
    if warehouse.organization_id is not None:
        raise ApiError("already_owned", status_code=status.HTTP_409_CONFLICT, detail="Esta bodega ya tiene dueña")
    organization = db.get(Organization, body.organization_id)
    if organization is None:
        raise ApiError("organization_not_found", status_code=status.HTTP_404_NOT_FOUND,
                       detail="Organización no encontrada")
    if organization.type != OrganizationType.eca:
        raise ApiError("organization_type_mismatch", status_code=422, detail="Una bodega pertenece a una ECA")
    if organization.status != OrganizationStatus.approved:
        raise ApiError("organization_not_active", status_code=status.HTTP_409_CONFLICT,
                       detail="La organización no está aprobada")
    warehouse.organization_id = organization.id
    audit.record(db, Action.WAREHOUSE_ORGANIZATION_ASSIGNED, actor=actor, target_type="warehouse",
                 target_id=warehouse.id, details={"organization_id": str(organization.id)})
    db.commit()
    db.refresh(warehouse)
    return warehouse
