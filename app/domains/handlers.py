"""The one place where domains are wired to each other's events (see `app/core/events.py`).

Call `register()` once at startup. Order matters: handlers run in the order they are registered here.
"""
from sqlalchemy.orm import Session

from app.core import events
from app.domains.inventory import service as inventory_service
from app.domains.transactions import service as transactions_service
from app.domains.weighings.events import WeighingValidated


def _add_stock(db: Session, event: WeighingValidated) -> None:
    inventory_service.add_stock(
        db=db, material_code=event.material_code, warehouse_id=event.warehouse_id,
        kg=event.kg, price_per_kg=event.price_per_kg)


def _create_purchase(db: Session, event: WeighingValidated) -> None:
    transactions_service.create_purchase(
        db, weighing_id=event.weighing_id, recycler_id=event.recycler_id, material_code=event.material_code,
        warehouse_id=event.warehouse_id, kg=event.kg, price_per_kg=event.price_per_kg,
        created_by=event.validated_by)


def register() -> None:
    events.subscribe(WeighingValidated, _add_stock)
    events.subscribe(WeighingValidated, _create_purchase)
