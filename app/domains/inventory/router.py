import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.errors import ApiError
from app.core.pagination import paginate
from app.core.permissions import INVENTORY_READ, INVENTORY_WRITE
from app.core.security import require_roles
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.inventory.models import InventoryItem, Material, Warehouse
from app.domains.inventory.schemas import (
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
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    query = select(InventoryItem)

    if material_code:
        query = query.where(InventoryItem.material_code == material_code)
    if warehouse_id:
        query = query.where(InventoryItem.warehouse_id == warehouse_id)

    if status_:
        query = query.where(InventoryItem.status == status_)

    total, items = paginate(
        db, query,
        # warehouse_id breaks ties so that pages never overlap or skip rows
        InventoryItem.material_code, InventoryItem.warehouse_id,
        limit=limit, offset=offset,
        options=(selectinload(InventoryItem.material), selectinload(InventoryItem.warehouse)),
    )
    return InventoryListResponse(total=total, items=items)


@router.get("/stats", response_model=InventoryStatsResponse)
def inventory_stats(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    total_stock, total_value = db.execute(select(
        func.coalesce(func.sum(InventoryItem.stock_kg), 0),
        func.coalesce(func.sum(InventoryItem.stock_kg * InventoryItem.price_per_kg), 0),
    )).one()
    by_status: dict[str, int] = {
        status: count
        for status, count in db.execute(
            select(InventoryItem.status, func.count(InventoryItem.id)).group_by(InventoryItem.status)
        ).all()
    }
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
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    return db.scalars(select(Warehouse).where(Warehouse.is_active.is_(True)).order_by(Warehouse.name)).all()


@router.get("/materials", response_model=list[MaterialResponse])
def list_materials(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    return db.scalars(select(Material).where(Material.is_active.is_(True)).order_by(Material.label)).all()


@router.get("/{item_id}", response_model=InventoryItemResponse)
def get_inventory_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise ApiError("inventory_item_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Ítem no encontrado")
    return item


@router.patch("/{item_id}", response_model=InventoryItemResponse)
def update_inventory_item(
    item_id: uuid.UUID,
    request: UpdateInventoryItemRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_WRITE)),
):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise ApiError("inventory_item_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Ítem no encontrado")

    changes: dict[str, list[str]] = {}
    if request.stock_min_kg is not None:
        changes["stock_min_kg"] = [str(item.stock_min_kg), str(request.stock_min_kg)]
        item.stock_min_kg = request.stock_min_kg
    if request.price_per_kg is not None:
        changes["price_per_kg"] = [str(item.price_per_kg), str(request.price_per_kg)]
        item.price_per_kg = request.price_per_kg

    if changes:
        audit.record(db, Action.INVENTORY_UPDATED, actor=actor, target_type="inventory_item",
                     target_id=item.id, details={"material": item.material_code, "changes": changes})
    db.commit()
    db.refresh(item)
    return item
