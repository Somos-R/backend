"""Catalogs managed from the backoffice: materials, document types and warehouses.

Entries are never deleted (inventory, weighings, transactions and accounts point at them): a retired entry
is deactivated. Deactivating stops *new* use (a weighing, a sale, an account, a warehouse operation) and
hides the entry from the public lists; what already exists keeps working and stays readable.
"""
import re
import uuid
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.catalogs.models import DocumentType
from app.domains.inventory.models import Material, Warehouse
from app.domains.users.models import User

MATERIAL_CODE = re.compile(r"^[a-z][a-z0-9_]{1,29}$")
DOCUMENT_TYPE_CODE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def list_entries(db: Session, model: type[Material] | type[DocumentType], is_active: bool | None) -> list[Any]:
    query = select(model)
    if is_active is not None:
        query = query.where(model.is_active.is_(is_active))
    return list(db.scalars(query.order_by(model.label, model.code)).all())


def create_entry(
    db: Session, actor: User, model: type[Material] | type[DocumentType], catalog: str, code: str, label: str,
) -> Any:
    pattern = MATERIAL_CODE if model is Material else DOCUMENT_TYPE_CODE
    if not pattern.match(code):
        raise ApiError("invalid_code", status_code=422, detail=f"code no cumple el formato: {pattern.pattern}")
    if db.get(model, code) is not None:
        raise ApiError("code_already_exists", status_code=status.HTTP_409_CONFLICT, detail=f"Ya existe '{code}'")
    entry = model(code=code, label=label.strip(), is_active=True)
    db.add(entry)
    audit.record(db, Action.CATALOG_CREATED, actor=actor, target_type=catalog, target_id=code,
                 details={"label": entry.label})
    db.commit()
    db.refresh(entry)
    return entry


def update_entry(
    db: Session, actor: User, model: type[Material] | type[DocumentType], catalog: str, code: str,
    label: str | None, is_active: bool | None,
) -> Any:
    entry = db.get(model, code)
    if entry is None:
        raise ApiError(f"{catalog}_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado")
    changes = _apply(entry, label=label.strip() if label is not None else None, is_active=is_active)
    if changes:
        audit.record(db, Action.CATALOG_UPDATED, actor=actor, target_type=catalog, target_id=code,
                     details={"changes": changes})
    db.commit()
    db.refresh(entry)
    return entry


def update_warehouse(
    db: Session, actor: User, warehouse_id: uuid.UUID, fields: dict[str, Any],
) -> Warehouse:
    """`fields` carries only what the request set (an address may be cleared with an explicit null)."""
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise ApiError("warehouse_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Bodega no encontrada")
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    changes = _apply(warehouse, **fields)
    if changes:
        audit.record(db, Action.WAREHOUSE_UPDATED, actor=actor, target_type="warehouse", target_id=warehouse.id,
                     details={"changes": changes})
    db.commit()
    db.refresh(warehouse)
    return warehouse


def _apply(entry: Any, **fields: Any) -> dict[str, list[Any]]:
    """Set the fields that really change and return {field: [old, new]} for the audit trail."""
    changes: dict[str, list[Any]] = {}
    for name, value in fields.items():
        if name in ("label", "is_active", "name") and value is None:
            continue  # not sent, or not clearable
        old = getattr(entry, name)
        if old != value:
            changes[name] = [old, value]
            setattr(entry, name, value)
    return changes
