import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.inventory.models import InventoryItem


def add_stock(
    db: Session,
    material_code: str,
    warehouse_id: uuid.UUID,
    kg: Decimal,
    precio_kg: Decimal,
) -> InventoryItem:
    item = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.material_code == material_code,
            InventoryItem.warehouse_id == warehouse_id,
        )
        .first()
    )

    if item:
        item.stock_kg += kg
        item.precio_kg = precio_kg
        item.fecha_actualizacion = datetime.now(timezone.utc)
    else:
        item = InventoryItem(
            id=uuid.uuid4(),
            material_code=material_code,
            warehouse_id=warehouse_id,
            stock_kg=kg,
            precio_kg=precio_kg,
        )
        db.add(item)

    db.flush()
    return item


def subtract_stock(
    db: Session,
    material_code: str,
    warehouse_id: uuid.UUID,
    kg: Decimal,
) -> InventoryItem:
    item = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.material_code == material_code,
            InventoryItem.warehouse_id == warehouse_id,
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay inventario de {material_code} en la bodega seleccionada",
        )

    if item.stock_kg < kg:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stock insuficiente: disponible {item.stock_kg} kg, solicitado {kg} kg",
        )

    item.stock_kg -= kg
    item.fecha_actualizacion = datetime.now(timezone.utc)
    db.flush()
    return item
