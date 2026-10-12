"""The ledger of the stock: every entry or exit of material, in order, with its balance.

Rows are only ever added (a database trigger rejects UPDATE and DELETE). `add_stock` and `subtract_stock` in
`service.py` are the only places stock changes, and each one writes its movement in the same statement flow, so the
ledger of an item always adds up to its stock.
"""
import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domains.inventory.models import InventoryMovement


class MovementType(str, enum.Enum):
    opening = "opening"  # stock that existed before the ledger did
    purchase = "purchase"  # a validated weighing: material comes in
    sale = "sale"  # material goes out to a buyer
    sale_cancellation = "sale_cancellation"  # a cancelled sale gives the material back
    adjustment = "adjustment"  # a correction, in either direction
    loss = "loss"  # shrinkage: material that is gone


class SourceType(str, enum.Enum):
    weighing = "weighing"
    transaction = "transaction"


@dataclass(frozen=True)
class Movement:
    """Why stock is changing. Required by `add_stock` and `subtract_stock`, so no change can go unrecorded."""

    type: MovementType
    source_type: SourceType | None = None
    source_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None


def record(
    db: Session, *, material_code: str, warehouse_id: uuid.UUID, kg_delta: Decimal, balance_after_kg: Decimal,
    price_per_kg: Decimal | None, movement: Movement, occurred_at: datetime,
) -> InventoryMovement:
    row = InventoryMovement(
        material_code=material_code, warehouse_id=warehouse_id, movement_type=movement.type.value,
        kg_delta=kg_delta, balance_after_kg=balance_after_kg, price_per_kg=price_per_kg,
        source_type=movement.source_type.value if movement.source_type else None, source_id=movement.source_id,
        actor_id=movement.actor_id, occurred_at=occurred_at)
    db.add(row)
    db.flush()
    return row
