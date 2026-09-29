import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.permissions import INVENTORY_READ, INVENTORY_WRITE
from app.core.security import require_roles
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
    estado: str | None = Query(default=None, description="disponible | bajo_stock | agotado"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    query = db.query(InventoryItem)

    if material_code:
        query = query.filter(InventoryItem.material_code == material_code)
    if warehouse_id:
        query = query.filter(InventoryItem.warehouse_id == warehouse_id)

    if estado:
        query = query.filter(InventoryItem.estado == estado)

    total = query.count()
    items = (
        query.options(selectinload(InventoryItem.material), selectinload(InventoryItem.warehouse))
        # warehouse_id breaks ties so that pages never overlap or skip rows
        .order_by(InventoryItem.material_code, InventoryItem.warehouse_id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return InventoryListResponse(total=total, items=items)


@router.get("/stats", response_model=InventoryStatsResponse)
def inventory_stats(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    total_stock, total_value = db.query(
        func.coalesce(func.sum(InventoryItem.stock_kg), 0),
        func.coalesce(func.sum(InventoryItem.stock_kg * InventoryItem.precio_kg), 0),
    ).one()
    by_estado: dict[str, int] = {
        estado: count
        for estado, count in db.query(InventoryItem.estado, func.count(InventoryItem.id))
        .group_by(InventoryItem.estado)
        .all()
    }
    return InventoryStatsResponse(
        total_stock_kg=total_stock,
        total_value=total_value,
        available_count=by_estado.get("disponible", 0),
        low_stock_count=by_estado.get("bajo_stock", 0),
        out_of_stock_count=by_estado.get("agotado", 0),
    )


@router.get("/warehouses", response_model=list[WarehouseResponse])
def list_warehouses(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    return db.query(Warehouse).filter(Warehouse.is_active.is_(True)).order_by(Warehouse.name).all()


@router.get("/materials", response_model=list[MaterialResponse])
def list_materials(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    return db.query(Material).filter(Material.is_active.is_(True)).order_by(Material.label).all()


@router.get("/{item_id}", response_model=InventoryItemResponse)
def get_inventory_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ítem no encontrado")
    return item


@router.patch("/{item_id}", response_model=InventoryItemResponse)
def update_inventory_item(
    item_id: uuid.UUID,
    request: UpdateInventoryItemRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_WRITE)),
):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ítem no encontrado")

    if request.stock_min_kg is not None:
        item.stock_min_kg = request.stock_min_kg
    if request.precio_kg is not None:
        item.precio_kg = request.precio_kg

    db.commit()
    db.refresh(item)
    return item
