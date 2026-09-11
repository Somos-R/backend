import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
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
    _: User = Depends(get_current_user),
):
    query = db.query(InventoryItem)

    if material_code:
        query = query.filter(InventoryItem.material_code == material_code)
    if warehouse_id:
        query = query.filter(InventoryItem.warehouse_id == warehouse_id)

    all_items = query.order_by(InventoryItem.material_code).all()

    if estado:
        all_items = [i for i in all_items if i.estado == estado]

    total = len(all_items)
    paginated = all_items[offset: offset + limit]
    return InventoryListResponse(total=total, items=paginated)


@router.get("/stats", response_model=InventoryStatsResponse)
def inventory_stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    items = db.query(InventoryItem).all()
    total_stock = sum(i.stock_kg for i in items)
    total_value = sum(i.total_value for i in items)
    available   = sum(1 for i in items if i.estado == "disponible")
    low_stock   = sum(1 for i in items if i.estado == "bajo_stock")
    out_of_stock = sum(1 for i in items if i.estado == "agotado")
    return InventoryStatsResponse(
        total_stock_kg=total_stock,
        total_value=total_value,
        available_count=available,
        low_stock_count=low_stock,
        out_of_stock_count=out_of_stock,
    )


@router.get("/warehouses", response_model=list[WarehouseResponse])
def list_warehouses(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.query(Warehouse).filter(Warehouse.is_active.is_(True)).order_by(Warehouse.name).all()


@router.get("/materials", response_model=list[MaterialResponse])
def list_materials(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.query(Material).filter(Material.is_active.is_(True)).order_by(Material.label).all()


@router.get("/{item_id}", response_model=InventoryItemResponse)
def get_inventory_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
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
