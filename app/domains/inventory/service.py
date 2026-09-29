"""Stock movements.

Both operations are single atomic statements, so concurrent requests cannot lose
updates or oversell: the database serializes them on the row, and `subtract_stock`
only succeeds while `stock_kg >= kg` still holds at the moment of the update.
Callers commit.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domains.inventory.models import InventoryItem


def _reload(db: Session, item_id: uuid.UUID) -> InventoryItem:
    # The statements above bypass the ORM, so refresh whatever the session already holds.
    return db.get(InventoryItem, item_id, populate_existing=True)  # type: ignore[return-value]


def add_stock(
    db: Session,
    material_code: str,
    warehouse_id: uuid.UUID,
    kg: Decimal,
    precio_kg: Decimal | None,
) -> InventoryItem:
    """Add `kg` to (material, warehouse), creating the row if needed, in one upsert.

    `precio_kg=None` leaves the stored price untouched (used when returning stock,
    which must not reprice what is already there).
    """
    now = datetime.now(timezone.utc)
    stock_col = InventoryItem.__table__.c.stock_kg

    on_conflict: dict = {"stock_kg": stock_col + kg, "fecha_actualizacion": now}
    if precio_kg is not None:
        on_conflict["precio_kg"] = precio_kg

    statement = (
        insert(InventoryItem)
        .values(
            id=uuid.uuid4(),
            material_code=material_code,
            warehouse_id=warehouse_id,
            stock_kg=kg,
            precio_kg=precio_kg if precio_kg is not None else Decimal("0"),
            fecha_actualizacion=now,
        )
        .on_conflict_do_update(constraint="uq_inventory_material_warehouse", set_=on_conflict)
        .returning(InventoryItem.id)
    )
    item_id = db.execute(statement).scalar_one()
    return _reload(db, item_id)


def subtract_stock(
    db: Session,
    material_code: str,
    warehouse_id: uuid.UUID,
    kg: Decimal,
) -> InventoryItem:
    """Remove `kg`, but only if enough is left at the instant of the update."""
    statement = (
        update(InventoryItem)
        .where(
            InventoryItem.material_code == material_code,
            InventoryItem.warehouse_id == warehouse_id,
            InventoryItem.stock_kg >= kg,
        )
        .values(
            stock_kg=InventoryItem.stock_kg - kg,
            fecha_actualizacion=datetime.now(timezone.utc),
        )
        .returning(InventoryItem.id)
        .execution_options(synchronize_session=False)
    )
    item_id = db.execute(statement).scalar_one_or_none()
    if item_id is not None:
        return _reload(db, item_id)

    # Nothing updated: tell the caller why.
    item = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.material_code == material_code,
            InventoryItem.warehouse_id == warehouse_id,
        )
        .first()
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay inventario de {material_code} en la bodega seleccionada",
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Stock insuficiente: disponible {item.stock_kg} kg, solicitado {kg} kg",
    )
