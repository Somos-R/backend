"""Concurrency and integrity of inventory movements (tasks 3.1, 3.2, 3.3, 3.5).

The race tests use real threads with real, committed data (each request opens its own
connection), so they exercise the database's locking, not the per-test SAVEPOINT session.
They are the ones that would have failed before atomic stock updates and row locks.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryItem, Warehouse
from app.domains.inventory.movements import Movement, MovementType
from app.domains.transactions.models import Transaction, TransactionType
from app.domains.weighings.models import AffiliationStatus, Weighing
from app.main import app
from tests import factories

RACERS = 6

ADJUSTMENT = Movement(MovementType.adjustment)
SALE = Movement(MovementType.sale)


def race(n, fn):
    """Run fn(i) in n threads released at the same instant; return the results in order."""
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(n) as pool:
        return list(pool.map(run, range(n)))


@pytest.fixture
def real(engine):
    """A committed world (not rolled back), wiped afterwards so other tests see empty tables."""
    with Session(engine) as session:
        warehouse = session.query(Warehouse).order_by(Warehouse.name).first()
        admin = factories.make_actor(session, "eca_admin")
        recycler = factories.make_user(session, "recycler")  # of the default association, linked to the default ECA
        warehouse.organization_id = factories.default_organization(session, "eca").id
        session.commit()
        world = {
            "engine": engine,
            "warehouse_id": warehouse.id,
            "admin": admin,
            "admin_headers": factories.auth_headers(admin),
            "recycler_id": recycler.id,
        }
    yield world
    with engine.begin() as connection:
        connection.execute(text("UPDATE warehouses SET organization_id = NULL"))
        connection.execute(text("TRUNCATE users, inventory_items, audit_log, eca_association_links CASCADE"))
        connection.execute(text("DELETE FROM organizations"))


def _api(headers):
    return TestClient(app, headers=headers)


def _stock(engine, warehouse_id, material="plastic"):
    with Session(engine) as session:
        item = session.query(InventoryItem).filter_by(
            material_code=material, warehouse_id=warehouse_id).first()
        return item.stock_kg if item else None


def _seed_stock(world, kg, precio="500", material="plastic"):
    with Session(world["engine"]) as session:
        factories.stock(session, session.get(Warehouse, world["warehouse_id"]),
                        material, kg=str(kg), price_per_kg=precio)


def _make_weighings(world, count, kg="10", precio="400", material="plastic"):
    with Session(world["engine"]) as session:
        rows = [Weighing(recycler_id=world["recycler_id"], material_code=material,
                         warehouse_id=world["warehouse_id"], kg=Decimal(kg), price_per_kg=Decimal(precio),
                         affiliation_status=AffiliationStatus.linked)
                for _ in range(count)]
        session.add_all(rows)
        session.commit()
        return [w.id for w in rows]


def _sell(world, kg):
    return _api(world["admin_headers"]).post("/transactions", json={
        "material_code": "plastic", "warehouse_id": str(world["warehouse_id"]),
        "kg": str(kg), "price_per_kg": "900"})


def _count(engine, model, **filters):
    with Session(engine) as session:
        return session.query(model).filter_by(**filters).count()


# --- 3.1 / 3.5: no overselling ------------------------------------------------------------

class TestNoOverselling:
    def test_two_sales_that_cannot_both_fit(self, real):
        _seed_stock(real, 100)
        codes = sorted(r.status_code for r in race(2, lambda i: _sell(real, 60)))
        assert codes == [201, 400]
        assert _stock(real["engine"], real["warehouse_id"]) == Decimal("40")

    def test_many_small_sales_sell_exactly_the_stock(self, real):
        _seed_stock(real, 100)
        results = race(8, lambda i: _sell(real, 20))
        codes = [r.status_code for r in results]

        assert codes.count(201) == 5 and codes.count(400) == 3
        assert _stock(real["engine"], real["warehouse_id"]) == Decimal("0")
        assert _count(real["engine"], Transaction, type=TransactionType.sale) == 5

    def test_the_error_reports_the_stock_that_is_actually_left(self, real):
        _seed_stock(real, 10)
        r = _sell(real, 25)
        assert r.status_code == 400 and "disponible 10" in r.json()["detail"]


# --- 3.2 / 3.5: a state transition happens once --------------------------------------------

class TestTransitionsHappenOnce:
    def test_a_weighing_is_validated_once(self, real):
        (weighing_id,) = _make_weighings(real, 1, kg="30")

        def validate(i):
            return _api(real["admin_headers"]).patch(
                f"/weighings/{weighing_id}/status", json={"status": "validated"})

        codes = sorted(r.status_code for r in race(RACERS, validate))
        assert codes == [200] + [400] * (RACERS - 1)
        assert _count(real["engine"], Transaction, weighing_id=weighing_id) == 1
        assert _stock(real["engine"], real["warehouse_id"]) == Decimal("30")  # added once, not 6x

    def test_a_sale_is_cancelled_once_and_its_stock_returns_once(self, real):
        _seed_stock(real, 100)
        sale = _sell(real, 40).json()

        def cancel(i):
            return _api(real["admin_headers"]).patch(
                f"/transactions/{sale['id']}/status", json={"status": "cancelled"})

        codes = sorted(r.status_code for r in race(RACERS, cancel))
        assert codes == [200] + [400] * (RACERS - 1)
        assert _stock(real["engine"], real["warehouse_id"]) == Decimal("100")

    def test_validating_and_rejecting_at_once_leaves_a_consistent_result(self, real):
        (weighing_id,) = _make_weighings(real, 1, kg="10")
        actions = [{"status": "validated"}, {"status": "rejected", "rejection_reason": "x"}] * 2

        def act(i):
            return _api(real["admin_headers"]).patch(
                f"/weighings/{weighing_id}/status", json=actions[i])

        results = race(4, act)
        assert [r.status_code for r in results].count(200) == 1
        with Session(real["engine"]) as session:
            weighing = session.get(Weighing, weighing_id)
            stock = _stock(real["engine"], real["warehouse_id"])
            if weighing.status.value == "validated":
                assert stock == Decimal("10")
            else:
                assert weighing.status.value == "rejected" and stock is None


# --- 3.1: first stock row for a material is created safely ---------------------------------

def test_concurrent_first_deliveries_create_one_row_and_sum_up(real):
    ids = _make_weighings(real, RACERS, kg="10")  # no inventory row exists yet

    def validate(i):
        return _api(real["admin_headers"]).patch(
            f"/weighings/{ids[i]}/status", json={"status": "validated"})

    results = race(RACERS, validate)
    assert [r.status_code for r in results] == [200] * RACERS  # no IntegrityError / 500
    assert _count(real["engine"], InventoryItem, material_code="plastic") == 1
    assert _stock(real["engine"], real["warehouse_id"]) == Decimal("60")


# --- 3.1: service-level behavior --------------------------------------------------------------

class TestStockService:
    def test_add_stock_creates_then_accumulates(self, db, warehouse):
        first = inventory_service.add_stock(db, "glass", warehouse.id, Decimal("10"), Decimal("200"), movement=ADJUSTMENT)
        second = inventory_service.add_stock(db, "glass", warehouse.id, Decimal("5"), Decimal("250"), movement=ADJUSTMENT)
        assert first.id == second.id
        assert second.stock_kg == Decimal("15")

    def test_add_stock_without_a_price_keeps_the_existing_one(self, db, warehouse):
        inventory_service.add_stock(db, "glass", warehouse.id, Decimal("10"), Decimal("200"), movement=ADJUSTMENT)
        item = inventory_service.add_stock(db, "glass", warehouse.id, Decimal("5"), None, movement=ADJUSTMENT)
        assert item.price_per_kg == Decimal("200") and item.stock_kg == Decimal("15")

    def test_selling_exactly_what_is_left_is_allowed(self, db, warehouse):
        inventory_service.add_stock(db, "glass", warehouse.id, Decimal("10"), Decimal("200"), movement=ADJUSTMENT)
        item = inventory_service.subtract_stock(db, "glass", warehouse.id, Decimal("10"), movement=SALE)
        assert item.stock_kg == Decimal("0")

    def test_selling_more_than_is_left_changes_nothing(self, db, warehouse):
        inventory_service.add_stock(db, "glass", warehouse.id, Decimal("10"), Decimal("200"), movement=ADJUSTMENT)
        with pytest.raises(Exception) as error:
            inventory_service.subtract_stock(db, "glass", warehouse.id, Decimal("10.01"), movement=SALE)
        assert error.value.status_code == 400  # type: ignore[attr-defined]
        db.expire_all()
        assert db.query(InventoryItem).filter_by(material_code="glass").one().stock_kg == Decimal("10")

    def test_cancelling_a_sale_does_not_reprice_the_inventory(self, client_as, eca_admin, db, warehouse):
        factories.stock(db, warehouse, "plastic", kg="100", price_per_kg="500")
        c = client_as(eca_admin)
        sale = c.post("/transactions", json={
            "material_code": "plastic", "warehouse_id": str(warehouse.id),
            "kg": "40", "price_per_kg": "900"}).json()
        c.patch(f"/transactions/{sale['id']}/status", json={"status": "cancelled"})

        db.expire_all()
        item = db.query(InventoryItem).filter_by(material_code="plastic").one()
        assert item.stock_kg == Decimal("100") and item.price_per_kg == Decimal("500")


# --- 3.3: the database refuses what the API would never send ---------------------------------

class TestDatabaseConstraints:
    def _expect_rejected(self, db, obj):
        db.add(obj)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

    def test_negative_stock_is_rejected(self, db, warehouse):
        self._expect_rejected(db, InventoryItem(
            material_code="glass", warehouse_id=warehouse.id, stock_kg=Decimal("-1")))

    def test_negative_min_stock_and_price_are_rejected(self, db, warehouse):
        self._expect_rejected(db, InventoryItem(
            material_code="glass", warehouse_id=warehouse.id, stock_min_kg=Decimal("-1")))
        self._expect_rejected(db, InventoryItem(
            material_code="metal", warehouse_id=warehouse.id, price_per_kg=Decimal("-1")))

    @pytest.mark.parametrize("kg,price", [("0", "100"), ("-5", "100"), ("10", "0")])
    def test_weighings_need_positive_quantities(self, db, warehouse, recycler, kg, price):
        self._expect_rejected(db, Weighing(
            recycler_id=recycler.id, material_code="plastic", warehouse_id=warehouse.id,
            kg=Decimal(kg), price_per_kg=Decimal(price), affiliation_status=AffiliationStatus.linked))

    def test_transactions_need_positive_quantities(self, db, warehouse, eca_admin):
        self._expect_rejected(db, Transaction(
            type=TransactionType.sale, material_code="plastic", warehouse_id=warehouse.id,
            kg=Decimal("0"), price_per_kg=Decimal("100"), created_by=eca_admin.id))

    def test_a_weighing_cannot_produce_two_purchases(self, db, warehouse, recycler, eca_admin):
        weighing = Weighing(recycler_id=recycler.id, material_code="plastic",
                            warehouse_id=warehouse.id, kg=Decimal("10"), price_per_kg=Decimal("100"), affiliation_status=AffiliationStatus.linked)
        db.add(weighing)
        db.flush()

        def purchase():
            return Transaction(
                type=TransactionType.purchase, material_code="plastic", warehouse_id=warehouse.id,
                kg=Decimal("10"), price_per_kg=Decimal("100"), weighing_id=weighing.id,
                recycler_id=recycler.id, created_by=eca_admin.id)

        db.add(purchase())
        db.flush()
        self._expect_rejected(db, purchase())

    def test_sales_without_a_weighing_are_not_constrained(self, db, warehouse, eca_admin):
        for _ in range(2):
            db.add(Transaction(
                type=TransactionType.sale, material_code="plastic", warehouse_id=warehouse.id,
                kg=Decimal("1"), price_per_kg=Decimal("100"), created_by=eca_admin.id))
        db.flush()
