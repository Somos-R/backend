import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import ECA_ADMIN, INVENTORY_READ, INVENTORY_WRITE
from app.core.security import require_roles
from app.domains.inventory import service as inventory_service
from app.domains.inventory.schemas import (
    CreateWarehouseRequest,
    InventoryItemResponse,
    InventoryListResponse,
    InventoryStatsResponse,
    MaterialResponse,
    UpdateInventoryItemRequest,
    WarehouseResponse,
)
from app.domains.users.models import User

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("", response_model=InventoryListResponse)
def list_inventory(
    material_code: str | None = Query(default=None),
    warehouse_id: uuid.UUID | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status", description="available | low_stock | out_of_stock"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_READ)),
):
    total, items = inventory_service.list_inventory(
        db, actor, material_code=material_code, warehouse_id=warehouse_id, status_=status_,
        limit=limit, offset=offset)
    return InventoryListResponse(total=total, items=items)


@router.get("/stats", response_model=InventoryStatsResponse)
def inventory_stats(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_READ)),
):
    total_stock, total_value, by_status = inventory_service.stats(db, actor)
    return InventoryStatsResponse(
        total_stock_kg=total_stock,
        total_value=total_value,
        available_count=by_status.get("available", 0),
        low_stock_count=by_status.get("low_stock", 0),
        out_of_stock_count=by_status.get("out_of_stock", 0),
    )


@router.get("/warehouses", response_model=list[WarehouseResponse])
def list_warehouses(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_READ)),
):
    return inventory_service.list_warehouses(db, actor)


@router.post("/warehouses", response_model=WarehouseResponse, status_code=status.HTTP_201_CREATED)
def create_warehouse(
    body: CreateWarehouseRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(ECA_ADMIN)),
):
    return inventory_service.create_warehouse(db, actor, body)


@router.get("/materials", response_model=list[MaterialResponse])
def list_materials(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    return inventory_service.list_materials(db)


@router.get("/{item_id}", response_model=InventoryItemResponse)
def get_inventory_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_READ)),
):
    return inventory_service.get_item(db, actor, item_id)


@router.patch("/{item_id}", response_model=InventoryItemResponse)
def update_inventory_item(
    item_id: uuid.UUID,
    request: UpdateInventoryItemRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_WRITE)),
):
    return inventory_service.update_item(db, actor, item_id, request)
