"""Domain events (5.2): validating a weighing no longer calls inventory and transactions directly; it announces
`WeighingValidated` and they react, in the same transaction."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core import events
from app.domains import handlers
from app.domains.inventory.models import InventoryItem
from app.domains.transactions.models import Transaction, TransactionType
from app.domains.weighings.events import WeighingValidated
from app.domains.weighings.models import Weighing, WeighingStatus
from tests import factories


@dataclass(frozen=True)
class Ping:
    n: int


@pytest.fixture
def clean_bus(monkeypatch):
    """An empty bus for the unit tests, restored afterwards."""
    monkeypatch.setattr(events, "_handlers", defaultdict(list))
    return events


class TestTheBus:
    def test_handlers_run_in_registration_order_with_the_session_and_the_event(self, clean_bus):
        seen = []
        events.subscribe(Ping, lambda db, e: seen.append(("first", db, e.n)))
        events.subscribe(Ping, lambda db, e: seen.append(("second", db, e.n)))
        events.publish("session", Ping(7))
        assert seen == [("first", "session", 7), ("second", "session", 7)]

    def test_registering_the_same_handler_twice_runs_it_once(self, clean_bus):
        calls = []

        def handler(db, event):
            calls.append(event.n)

        events.subscribe(Ping, handler)
        events.subscribe(Ping, handler)
        events.publish(None, Ping(1))
        assert calls == [1]

    def test_an_event_nobody_handles_fails_loudly(self, clean_bus):
        with pytest.raises(RuntimeError, match="Nobody handles Ping"):
            events.publish(None, Ping(1))

    def test_a_failing_handler_stops_the_rest_and_propagates(self, clean_bus):
        ran = []

        def boom(db, event):
            raise ValueError("no")

        events.subscribe(Ping, boom)
        events.subscribe(Ping, lambda db, e: ran.append(1))
        with pytest.raises(ValueError):
            events.publish(None, Ping(1))
        assert ran == []

    def test_registering_the_domains_is_idempotent(self, clean_bus):
        handlers.register()
        handlers.register()
        assert len(events._handlers[WeighingValidated]) == 2


class TestValidatingAWeighing:
    @pytest.fixture
    def pending(self, db, client_as):
        factories.own_all_warehouses(db)
        eca = factories.make_user(db, "eca", role_code="eca_admin",
                                  organization_id=factories.default_organization(db, "eca").id)
        warehouse = factories.first_warehouse(db)
        r = client_as(eca).post("/weighings", json={
            "material_code": "plastic", "warehouse_id": str(warehouse.id), "kg": "12.5", "price_per_kg": "400",
            "seller": {"full_name": "Ana Vendedora", "id_type": "CC", "id_number": "52123456"}})
        assert r.status_code == 201, r.text
        return eca, warehouse, r.json()["id"]

    def test_the_stock_and_the_purchase_come_from_the_event(self, db, client_as, pending):
        eca, warehouse, weighing_id = pending
        r = client_as(eca).patch(f"/weighings/{weighing_id}/status", json={"status": "validated"})
        assert r.status_code == 200, r.text
        item = db.scalars(select(InventoryItem).where(
            InventoryItem.material_code == "plastic", InventoryItem.warehouse_id == warehouse.id)).one()
        assert item.stock_kg == Decimal("12.5")
        purchase = db.scalars(select(Transaction).where(Transaction.weighing_id == weighing_id)).one()
        assert purchase.type == TransactionType.purchase and purchase.kg == Decimal("12.5")
        assert purchase.recycler_id is None and purchase.created_by == eca.id

    def test_an_error_in_a_handler_undoes_the_whole_validation(self, db, client_as, pending, monkeypatch):
        eca, warehouse, weighing_id = pending

        def boom(*args, **kwargs):
            raise RuntimeError("purchase could not be created")

        monkeypatch.setattr(handlers.transactions_service, "create_purchase", boom)
        with pytest.raises(RuntimeError):
            client_as(eca).patch(f"/weighings/{weighing_id}/status", json={"status": "validated"})
        db.rollback()  # what the request's session does when the error escapes
        weighing = db.get(Weighing, weighing_id)
        assert weighing.status == WeighingStatus.pending_validation
        assert db.scalars(select(Transaction).where(Transaction.weighing_id == weighing_id)).first() is None
