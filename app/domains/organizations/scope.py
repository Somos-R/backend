"""Whose operational data an organization may reach.

Warehouses, inventory, weighings and transactions belong to the ECA that owns the warehouse. The rules,
kept in one place so no endpoint can forget them:

- **ECA staff** reach what happened in their own warehouses.
- **Association staff** read the weighings and purchases of *their* recyclers, and the warehouses and
  inventory of the ECAs their association is actively linked to (read only).
- Anyone without an organization reaches nothing (fail closed).

Every function returns a SQL condition, so the same rule filters lists, totals, statistics and single
items, and a row outside the scope answers 404 like a row that does not exist.
"""
import uuid
from typing import Any

from fastapi import status
from sqlalchemy import ColumnElement, Select, and_, false, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.domains.inventory.models import Warehouse
from app.domains.organizations.enums import LinkStatus
from app.domains.organizations.models import EcaAssociationLink
from app.domains.transactions.models import Transaction
from app.domains.users.models import User
from app.domains.weighings.models import Weighing


def linked_associations(eca_id: uuid.UUID) -> Select:
    """Ids of the associations actively linked to an ECA."""
    return select(EcaAssociationLink.association_id).where(
        EcaAssociationLink.eca_id == eca_id, EcaAssociationLink.status == LinkStatus.active)


def linked_ecas(association_id: uuid.UUID) -> Select:
    """Ids of the ECAs an association is actively linked to."""
    return select(EcaAssociationLink.eca_id).where(
        EcaAssociationLink.association_id == association_id, EcaAssociationLink.status == LinkStatus.active)


def own_recyclers(association_id: uuid.UUID) -> Select:
    return select(User.id).where(User.user_type_code == "recycler", User.organization_id == association_id)


def readable_warehouses(actor: User) -> Select:
    """Ids of the warehouses the actor may read."""
    if actor.organization_id is None:
        return select(Warehouse.id).where(false())
    if actor.user_type_code == "eca":
        return select(Warehouse.id).where(Warehouse.organization_id == actor.organization_id)
    if actor.user_type_code == "association":
        return select(Warehouse.id).where(Warehouse.organization_id.in_(linked_ecas(actor.organization_id)))
    return select(Warehouse.id).where(false())


def own_warehouses(actor: User) -> Select:
    """Ids of the warehouses the actor may operate: only an ECA's own."""
    if actor.organization_id is None or actor.user_type_code != "eca":
        return select(Warehouse.id).where(false())
    return select(Warehouse.id).where(Warehouse.organization_id == actor.organization_id)


def warehouse_scope(actor: User) -> ColumnElement[bool]:
    """Warehouses to read."""
    return Warehouse.id.in_(readable_warehouses(actor))


def inventory_scope(actor: User, warehouse_id_column: Any) -> ColumnElement[bool]:
    """Inventory rows to read: those in a readable warehouse."""
    return warehouse_id_column.in_(readable_warehouses(actor))


def inventory_write_scope(actor: User, warehouse_id_column: Any) -> ColumnElement[bool]:
    """Inventory rows to change: only an ECA's own."""
    return warehouse_id_column.in_(own_warehouses(actor))


def weighing_scope(actor: User) -> ColumnElement[bool]:
    if actor.organization_id is None:
        return false()
    if actor.user_type_code == "eca":
        return Weighing.warehouse_id.in_(own_warehouses(actor))
    if actor.user_type_code == "association":
        return Weighing.recycler_id.in_(own_recyclers(actor.organization_id))
    return false()


def transaction_scope(actor: User) -> ColumnElement[bool]:
    if actor.organization_id is None:
        return false()
    if actor.user_type_code == "eca":
        return Transaction.warehouse_id.in_(own_warehouses(actor))
    if actor.user_type_code == "association":
        # Purchases from their recyclers; a sale has no recycler and is the ECA's own business.
        return and_(Transaction.recycler_id.is_not(None),
                    Transaction.recycler_id.in_(own_recyclers(actor.organization_id)))
    return false()


def recycler_linked_to(eca_id: uuid.UUID) -> ColumnElement[bool]:
    """Recyclers whose association is actively linked to the ECA."""
    return and_(User.user_type_code == "recycler", User.organization_id.in_(linked_associations(eca_id)))


# --- Write checks ---------------------------------------------------------------------------

def get_own_warehouse(db: Session, actor: User, warehouse_id: uuid.UUID) -> Warehouse:
    """The warehouse, if it belongs to the actor's ECA; otherwise it looks like a missing one."""
    warehouse = db.scalars(select(Warehouse).where(
        Warehouse.id == warehouse_id, Warehouse.id.in_(own_warehouses(actor)))).first()
    if warehouse is None:
        raise ApiError("warehouse_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Bodega no encontrada")
    return warehouse


def ensure_recycler_delivers_to(db: Session, eca_id: uuid.UUID | None, recycler: User) -> None:
    """A recycler delivers to the ECAs their association is linked to, and only to those."""
    linked = (
        db.scalar(select(User.id).where(User.id == recycler.id, recycler_linked_to(eca_id)))
        if eca_id is not None else None)
    if linked is None:
        raise ApiError(
            "recycler_not_linked", status_code=status.HTTP_409_CONFLICT,
            detail="El reciclador pertenece a una asociación que no está vinculada a tu ECA")
