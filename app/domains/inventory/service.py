"""Stock movements.

Both operations are single atomic statements, so concurrent requests cannot lose
updates or oversell: the database serializes them on the row, and `subtract_stock`
only succeeds while `stock_kg >= kg` still holds at the moment of the update.
Callers commit.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import status
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.core.csv_export import MAX_EXPORT_ROWS
from app.core.errors import ApiError
from app.core.pagination import paginate
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.inventory.models import InventoryItem, Material, Warehouse
from app.domains.inventory.schemas import (
    CreateWarehouseRequest,
    UpdateInventoryItemRequest,
)
from app.domains.organizations import scope
from app.domains.organizations.enums import OrganizationStatus
from app.domains.organizations.models import Organization
from app.domains.users.models import User


def _reload(db: Session, item_id: uuid.UUID) -> InventoryItem:
    # The statements above bypass the ORM, so refresh whatever the session already holds.
    return db.get(InventoryItem, item_id, populate_existing=True)  # type: ignore[return-value]


def add_stock(
    db: Session,
    material_code: str,
    warehouse_id: uuid.UUID,
    kg: Decimal,
    price_per_kg: Decimal | None,
) -> InventoryItem:
    """Add `kg` to (material, warehouse), creating the row if needed, in one upsert.

    `price_per_kg=None` leaves the stored price untouched (used when returning stock,
    which must not reprice what is already there).
    """
    now = datetime.now(timezone.utc)
    stock_col = InventoryItem.__table__.c.stock_kg

    on_conflict: dict = {"stock_kg": stock_col + kg, "updated_at": now}
    if price_per_kg is not None:
        on_conflict["price_per_kg"] = price_per_kg

    statement = (
        insert(InventoryItem)
        .values(
            id=uuid.uuid4(),
            material_code=material_code,
            warehouse_id=warehouse_id,
            stock_kg=kg,
            price_per_kg=price_per_kg if price_per_kg is not None else Decimal("0"),
            updated_at=now,
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
            updated_at=datetime.now(timezone.utc),
        )
        .returning(InventoryItem.id)
        .execution_options(synchronize_session=False)
    )
    item_id = db.execute(statement).scalar_one_or_none()
    if item_id is not None:
        return _reload(db, item_id)

    # Nothing updated: tell the caller why.
    item = db.scalars(
        select(InventoryItem).where(
            InventoryItem.material_code == material_code,
            InventoryItem.warehouse_id == warehouse_id,
        )
    ).first()
    if item is None:
        raise ApiError("inventory_not_found", 
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay inventario de {material_code} en la bodega seleccionada",
        )
    raise ApiError("insufficient_stock", 
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Stock insuficiente: disponible {item.stock_kg} kg, solicitado {kg} kg",
    )


# Material and warehouse are ordered by the name people read, not by their code or id.
SORT_COLUMNS = {
    "material": select(Material.label).where(Material.code == InventoryItem.material_code).scalar_subquery(),
    "warehouse": select(Warehouse.name).where(Warehouse.id == InventoryItem.warehouse_id).scalar_subquery(),
    "stock_kg": InventoryItem.stock_kg,
    "price_per_kg": InventoryItem.price_per_kg,
    "total_value": InventoryItem.stock_kg * InventoryItem.price_per_kg,
    "status": InventoryItem.status,
    "updated_at": InventoryItem.updated_at,
}
_RELATIONS = (selectinload(InventoryItem.material), selectinload(InventoryItem.warehouse))


def _visible_items(
    actor: User,
    *,
    material_code: str | None,
    warehouse_id: uuid.UUID | None,
    status_: str | None,
):
    """The inventory rows the actor may read, narrowed by the filters."""
    query = select(InventoryItem).where(scope.inventory_scope(actor, InventoryItem.warehouse_id))
    if material_code:
        query = query.where(InventoryItem.material_code == material_code)
    if warehouse_id:
        query = query.where(InventoryItem.warehouse_id == warehouse_id)
    if status_:
        query = query.where(InventoryItem.status == status_)
    return query


def _ordering(sort: str | None, descending: bool) -> tuple:
    if sort is None:
        # No order asked: the stable default every client got before ordering existed.
        return (InventoryItem.material_code, InventoryItem.warehouse_id)
    column = SORT_COLUMNS[sort]
    # (material, warehouse) is unique, so it keeps pages stable when many rows share the sorted value.
    return (column.desc() if descending else column.asc(), InventoryItem.material_code, InventoryItem.warehouse_id)


def list_inventory(
    db: Session,
    actor: User,
    *,
    material_code: str | None,
    warehouse_id: uuid.UUID | None,
    status_: str | None,
    sort: str | None = None,
    descending: bool = False,
    limit: int,
    offset: int,
) -> tuple[int, list[InventoryItem]]:
    """One page of what the actor may read, in the requested order."""
    query = _visible_items(actor, material_code=material_code, warehouse_id=warehouse_id, status_=status_)
    return paginate(db, query, *_ordering(sort, descending), limit=limit, offset=offset, options=_RELATIONS)


def export_inventory(
    db: Session,
    actor: User,
    *,
    material_code: str | None,
    warehouse_id: uuid.UUID | None,
    status_: str | None,
    sort: str | None,
    descending: bool,
) -> list[InventoryItem]:
    """Every inventory row matching the filters (not one page), for a file. Refuses rather than truncating:
    a silently partial report would be taken for the whole."""
    query = _visible_items(actor, material_code=material_code, warehouse_id=warehouse_id, status_=status_)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    if total > MAX_EXPORT_ROWS:
        raise ApiError(
            "export_too_large", status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El reporte tiene {total} filas; el máximo es {MAX_EXPORT_ROWS}. Acote los filtros")
    rows = db.scalars(query.options(*_RELATIONS).order_by(*_ordering(sort, descending))).all()
    audit.record(db, Action.INVENTORY_EXPORTED, actor=actor, target_type="inventory", target_id=None,
                 details={"rows": len(rows)})
    db.commit()
    return list(rows)


def stats(db: Session, actor: User) -> tuple[Decimal, Decimal, dict[str, int]]:
    """(total kg, total value, item count by status) of what the actor may see, all in SQL."""
    visible = scope.inventory_scope(actor, InventoryItem.warehouse_id)
    total_stock, total_value = db.execute(select(
        func.coalesce(func.sum(InventoryItem.stock_kg), 0),
        func.coalesce(func.sum(InventoryItem.stock_kg * InventoryItem.price_per_kg), 0),
    ).where(visible)).one()
    by_status: dict[str, int] = {
        status_: count
        for status_, count in db.execute(
            select(InventoryItem.status, func.count(InventoryItem.id)).where(visible).group_by(InventoryItem.status)
        ).all()
    }
    return total_stock, total_value, by_status


def list_warehouses(db: Session, actor: User) -> list[Warehouse]:
    return list(db.scalars(select(Warehouse).where(
        Warehouse.is_active.is_(True), scope.warehouse_scope(actor)).order_by(Warehouse.name, Warehouse.id)).all())


def create_warehouse(db: Session, actor: User, body: CreateWarehouseRequest) -> Warehouse:
    """A warehouse of the actor's own ECA. Everything that happens in it (inventory, weighings, sales)
    belongs to that ECA."""
    organization = db.get(Organization, actor.organization_id) if actor.organization_id else None
    if organization is None:
        raise ApiError("no_organization", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu cuenta no está asociada a una organización")
    if organization.status != OrganizationStatus.approved:
        raise ApiError("organization_not_active", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu organización no está activa")
    warehouse = Warehouse(name=body.name.strip(), address=body.address, organization_id=organization.id)
    db.add(warehouse)
    db.flush()
    audit.record(db, Action.WAREHOUSE_CREATED, actor=actor, target_type="warehouse", target_id=warehouse.id)
    db.commit()
    db.refresh(warehouse)
    return warehouse


def list_materials(db: Session) -> list[Material]:
    return list(db.scalars(select(Material).where(Material.is_active.is_(True)).order_by(Material.label)).all())


def get_item(db: Session, actor: User, item_id: uuid.UUID) -> InventoryItem:
    item = db.scalars(select(InventoryItem).where(
        InventoryItem.id == item_id, scope.inventory_scope(actor, InventoryItem.warehouse_id))).first()
    if not item:
        raise ApiError("inventory_item_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Ítem no encontrado")
    return item


def update_item(db: Session, actor: User, item_id: uuid.UUID, request: UpdateInventoryItemRequest) -> InventoryItem:
    item = db.scalars(select(InventoryItem).where(
        InventoryItem.id == item_id, scope.inventory_write_scope(actor, InventoryItem.warehouse_id))).first()
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
