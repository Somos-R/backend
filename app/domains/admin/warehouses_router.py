import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.network import enforce_admin_network
from app.core.rate_limit import admin_limit, limiter
from app.core.security import require_capability
from app.domains.admin import catalogs_service
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


class UpdateWarehouseRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = Field(default=None, max_length=300, description="null borra la dirección")
    is_active: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self):
        if not self.model_fields_set:
            raise ValueError("Indica name, address o is_active")
        return self


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
    total, items = catalogs_service.list_warehouses(db, organization_id, unassigned, limit=limit, offset=offset)
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
    """Give an owner ECA to a warehouse that has none. An owner cannot be changed."""
    return catalogs_service.assign_warehouse_organization(db, actor, warehouse_id, body.organization_id)


@router.patch("/{warehouse_id}", response_model=AdminWarehouse)
@limiter.limit(admin_limit)
def update_warehouse(
    request: Request,
    warehouse_id: uuid.UUID,
    body: UpdateWarehouseRequest,
    db: Session = Depends(get_db),
    actor: User = Manager,
):
    """Rename, change the address or (de)activate. A deactivated warehouse is refused for new weighings and
    sales and disappears from its ECA's list; its inventory and history stay."""
    return catalogs_service.update_warehouse(db, actor, warehouse_id, body.model_dump(exclude_unset=True))
