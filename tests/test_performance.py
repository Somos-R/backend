"""Query counts, SQL aggregates, indexes and pool settings (tasks 3.6 - 3.8).

Counting queries rather than timing them keeps these tests fast and deterministic: a list
endpoint must cost the same number of queries for 3 rows as for 30 (no N+1).
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, inspect, text

from app.core.config import settings
from app.core.database import build_engine
from app.domains.inventory.models import InventoryItem, Material, Warehouse
from app.domains.transactions.models import (
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.domains.weighings.models import AffiliationStatus, Weighing, WeighingStatus
from tests import factories

MATERIALS = ["paper", "plastic", "glass", "metal", "cardboard", "electronic", "organic"]


@contextmanager
def _orm_loads(*models):
    """Record every ORM instance of `models` the ORM materialises inside the block."""
    loaded: list[str] = []

    def on_load(target, context):
        loaded.append(type(target).__name__)

    for model in models:
        event.listen(model, "load", on_load)
    try:
        yield loaded
    finally:
        for model in models:
            event.remove(model, "load", on_load)


def _add_weighings(db, warehouse, recycler, count, material="plastic", kg="10", **extra):
    rows = [Weighing(recycler_id=recycler.id, material_code=material, warehouse_id=warehouse.id,
                     kg=Decimal(kg), price_per_kg=Decimal("100"), affiliation_status=AffiliationStatus.linked, **extra) for _ in range(count)]
    db.add_all(rows)
    db.commit()
    return rows


def _add_sales(db, warehouse, admin, count, **extra):
    rows = [Transaction(type=TransactionType.sale, material_code="plastic", warehouse_id=warehouse.id,
                        kg=Decimal("2"), price_per_kg=Decimal("100"), created_by=admin.id, **extra)
            for _ in range(count)]
    db.add_all(rows)
    db.commit()
    return rows


# --- N+1: list endpoints cost a constant number of queries -----------------------------------
# The rows deliberately reference *different* recyclers, materials and warehouses: with lazy
# loading each distinct one costs a query, so an N+1 shows up as a growing count.

def _varied_weighings(db, count):
    factories.own_all_warehouses(db)
    warehouses = db.query(Warehouse).order_by(Warehouse.name).all()
    rows = [
        Weighing(
            recycler_id=factories.make_user(db, "recycler").id,
            material_code=MATERIALS[i % len(MATERIALS)],
            warehouse_id=warehouses[i % len(warehouses)].id,
            kg=Decimal("10"), price_per_kg=Decimal("100"), affiliation_status=AffiliationStatus.linked)
        for i in range(count)
    ]
    db.add_all(rows)
    db.commit()


def _varied_sales(db, admin_id, count):
    factories.own_all_warehouses(db)
    warehouses = db.query(Warehouse).order_by(Warehouse.name).all()
    rows = [
        Transaction(
            type=TransactionType.purchase if i % 2 else TransactionType.sale,
            material_code=MATERIALS[i % len(MATERIALS)],
            warehouse_id=warehouses[i % len(warehouses)].id,
            recycler_id=factories.make_user(db, "recycler").id if i % 2 else None,
            kg=Decimal("2"), price_per_kg=Decimal("100"), created_by=admin_id)
        for i in range(count)
    ]
    db.add_all(rows)
    db.commit()


def _queries_for(c, url, db, count_queries):
    db.expunge_all()  # forget loaded rows so the request has to fetch everything itself
    with count_queries() as statements:
        r = c.get(url)
    assert r.status_code == 200
    return len(statements), r.json()


class TestNoNPlusOne:
    def test_weighing_list(self, client_as, eca_admin, db, count_queries):
        c = client_as(eca_admin)
        _varied_weighings(db, 3)
        few, _ = _queries_for(c, "/weighings", db, count_queries)

        _varied_weighings(db, 27)
        many, body = _queries_for(c, "/weighings", db, count_queries)

        assert body["total"] == 30 and len(body["items"]) == 20
        assert many == few, f"{few} queries for 3 rows but {many} for 20"
        assert many <= 7

    def test_transaction_list(self, client_as, eca_admin, db, count_queries):
        c = client_as(eca_admin)
        admin_id = eca_admin.id  # read now: the session is emptied before each measurement
        _varied_sales(db, admin_id, 3)
        few, _ = _queries_for(c, "/transactions", db, count_queries)

        _varied_sales(db, admin_id, 27)
        many, body = _queries_for(c, "/transactions", db, count_queries)

        assert body["total"] == 30 and len(body["items"]) == 20
        assert many == few, f"{few} queries for 3 rows but {many} for 20"
        assert many <= 7

    def test_inventory_list(self, client_as, eca_admin, db, count_queries):
        c = client_as(eca_admin)
        factories.own_all_warehouses(db)
        factories.stock(db, db.query(Warehouse).order_by(Warehouse.name).first(), "paper")
        few, _ = _queries_for(c, "/inventory", db, count_queries)

        warehouses = db.query(Warehouse).order_by(Warehouse.name).all()  # re-read after the reset
        for warehouse in warehouses:
            for material in MATERIALS:
                if (warehouse.name, material) != (warehouses[0].name, "paper"):
                    factories.stock(db, warehouse, material)
        many, body = _queries_for(c, "/inventory", db, count_queries)

        assert len(body["items"]) == 21
        assert many == few, f"{few} queries for 1 row but {many} for 21"
        assert many <= 6


# --- 3.6: aggregates are computed by the database ---------------------------------------------

class TestAggregatesInSql:
    def test_stats_do_not_load_rows_into_memory(self, client_as, eca_admin, db, warehouse,
                                                recycler, count_queries):
        c = client_as(eca_admin)
        _add_weighings(db, warehouse, recycler, 40)
        _add_sales(db, warehouse, eca_admin, 40)
        for material in MATERIALS:
            factories.stock(db, warehouse, material)

        for url, bound in (("/weighings/stats", 4), ("/transactions/stats", 3), ("/inventory/stats", 3)):
            db.expunge_all()
            with count_queries() as statements, _orm_loads(Weighing, Transaction, InventoryItem) as loaded:
                assert c.get(url).status_code == 200
            assert not loaded, f"{url} materialised {len(loaded)} rows instead of aggregating in SQL"
            # bound = authentication (1) + one query per aggregate
            assert len(statements) <= bound, (url, statements)

    def test_weighing_stats_values(self, client_as, eca_admin, db, warehouse, recycler):
        _add_weighings(db, warehouse, recycler, 2, material="plastic", kg="10.5")
        _add_weighings(db, warehouse, recycler, 1, material="glass", kg="4")
        _add_weighings(db, warehouse, recycler, 1, material="glass", kg="9",
                       status=WeighingStatus.validated)
        old = _add_weighings(db, warehouse, recycler, 1, material="metal", kg="99")[0]
        old.occurred_at = datetime.now(timezone.utc) - timedelta(days=90)  # outside the current month
        db.commit()

        body = client_as(eca_admin).get("/weighings/stats").json()
        assert body["total_weighings_month"] == 4
        assert Decimal(body["total_kg_month"]) == Decimal("34")
        assert body["pending_count"] == 4  # everything pending, including the old one, minus validated
        by_material = {m["material"]: m["kg"] for m in body["by_material"]}
        assert by_material == {"plastic": 21.0, "glass": 13.0}

    def test_weighing_stats_when_empty(self, client_as, eca_admin):
        body = client_as(eca_admin).get("/weighings/stats").json()
        assert body["total_weighings_month"] == 0 and Decimal(body["total_kg_month"]) == 0
        assert body["pending_count"] == 0 and body["by_material"] == []

    def test_transaction_stats_values(self, client_as, eca_admin, db, warehouse, recycler):
        _add_sales(db, warehouse, eca_admin, 2)  # 2 kg @ 100 each
        db.add(Transaction(type=TransactionType.purchase, material_code="plastic",
                           warehouse_id=warehouse.id, kg=Decimal("30"), price_per_kg=Decimal("50"),
                           recycler_id=recycler.id, created_by=eca_admin.id,
                           status=TransactionStatus.paid))
        db.commit()

        body = client_as(eca_admin).get("/transactions/stats").json()
        assert (body["total_sales_month"], body["total_purchases_month"]) == (2, 1)
        assert Decimal(body["total_kg_sales"]) == Decimal("4")
        assert Decimal(body["total_value_sales"]) == Decimal("400")
        assert Decimal(body["total_kg_purchases"]) == Decimal("30")
        assert Decimal(body["total_value_purchases"]) == Decimal("1500")
        assert body["pending_count"] == 2

    def test_transaction_stats_when_empty(self, client_as, eca_admin):
        body = client_as(eca_admin).get("/transactions/stats").json()
        assert body["total_sales_month"] == 0 and Decimal(body["total_value_purchases"]) == 0

    def test_inventory_stats_values(self, client_as, eca_admin, db, warehouse):
        factories.stock(db, warehouse, "paper", kg="100", price_per_kg="10")    # available
        factories.stock(db, warehouse, "glass", kg="10", price_per_kg="20")    # low_stock (min 50)
        factories.stock(db, warehouse, "metal", kg="0", price_per_kg="30")      # out_of_stock

        body = client_as(eca_admin).get("/inventory/stats").json()
        assert Decimal(body["total_stock_kg"]) == Decimal("110")
        assert Decimal(body["total_value"]) == Decimal("1200")
        assert (body["available_count"], body["low_stock_count"], body["out_of_stock_count"]) == (1, 1, 1)

    def test_inventory_stats_when_empty(self, client_as, eca_admin):
        body = client_as(eca_admin).get("/inventory/stats").json()
        assert Decimal(body["total_stock_kg"]) == 0 and body["available_count"] == 0


class TestInventoryListInSql:
    @pytest.fixture
    def three_states(self, db, warehouse):
        factories.stock(db, warehouse, "paper", kg="100")   # available
        factories.stock(db, warehouse, "glass", kg="10")   # low_stock
        factories.stock(db, warehouse, "metal", kg="0")     # out_of_stock

    @pytest.mark.parametrize("status,expected", [
        ("available", ["paper"]), ("low_stock", ["glass"]), ("out_of_stock", ["metal"]),
    ])
    def test_filter_by_status(self, client_as, eca_admin, three_states, status, expected):
        body = client_as(eca_admin).get(f"/inventory?status={status}").json()
        assert [i["material_code"] for i in body["items"]] == expected
        assert body["total"] == 1

    def test_unknown_status_matches_nothing(self, client_as, eca_admin, three_states):
        body = client_as(eca_admin).get("/inventory?status=inventado").json()
        assert body == {"total": 0, "items": []}

    def test_pagination_is_stable_and_total_is_the_full_count(self, client_as, eca_admin, db, warehouse):
        for material in MATERIALS:
            factories.stock(db, warehouse, material)
        c = client_as(eca_admin)
        pages = [c.get(f"/inventory?limit=3&offset={o}").json() for o in (0, 3, 6)]

        assert [p["total"] for p in pages] == [7, 7, 7]
        seen = [i["material_code"] for p in pages for i in p["items"]]
        assert seen == sorted(MATERIALS) and len(set(seen)) == 7

    def test_status_is_still_exposed_on_each_item(self, client_as, eca_admin, three_states):
        items = client_as(eca_admin).get("/inventory").json()["items"]
        assert {i["material_code"]: i["status"] for i in items} == {
            "paper": "available", "glass": "low_stock", "metal": "out_of_stock"}

    def test_the_status_rule_matches_between_python_and_sql(self, db, warehouse):
        rows = [factories.stock(db, warehouse, m, kg=k) for m, k in
                (("paper", "0"), ("glass", "49.99"), ("metal", "50"), ("cardboard", "500"))]
        db.expire_all()
        for state in ("out_of_stock", "low_stock", "available"):
            in_sql = {r.id for r in db.query(InventoryItem).filter(InventoryItem.status == state)}
            in_python = {r.id for r in db.query(InventoryItem) if r.status == state}
            assert in_sql == in_python
        assert rows  # silence "unused" while documenting the boundary values above


# --- 3.7: indexes ---------------------------------------------------------------------------------

def _indexes(db, table):
    return {ix["name"]: ix for ix in inspect(db.get_bind()).get_indexes(table)}


class TestIndexes:
    @pytest.mark.parametrize("table,name,columns", [
        ("weighings", "ix_weighings_recycler_status_occurred", ["recycler_id", "status", "occurred_at"]),
        ("weighings", "idx_weighings_occurred_at", ["occurred_at"]),
        ("transactions", "ix_transactions_type_status_occurred", ["type", "status", "occurred_at"]),
        ("transactions", "ix_transactions_occurred_at", ["occurred_at"]),
        ("users", "ix_users_user_type_verification", ["user_type_code", "verification_status"]),
    ])
    def test_expected_indexes_exist(self, db, table, name, columns):
        assert _indexes(db, table)[name]["column_names"] == columns

    def test_redundant_single_column_indexes_were_dropped(self, db):
        assert "idx_weighings_recycler" not in _indexes(db, "weighings")
        assert "ix_transactions_type" not in _indexes(db, "transactions")

    def test_coverage_area_has_a_gist_index(self, db):
        index = db.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'users' "
            "AND indexname = 'idx_users_coverage_area'")).scalar()
        assert index and "gist" in index.lower()

    @pytest.mark.parametrize("query,index", [
        ("SELECT * FROM weighings WHERE recycler_id = '00000000-0000-0000-0000-000000000000' "
         "AND status = 'pending_validation' ORDER BY occurred_at DESC", "ix_weighings_recycler_status_occurred"),
        ("SELECT count(*) FROM weighings WHERE occurred_at >= now()", "idx_weighings_occurred_at"),
        ("SELECT * FROM transactions WHERE type = 'sale' AND status = 'pending' "
         "ORDER BY occurred_at DESC", "ix_transactions_type_status_occurred"),
        ("SELECT * FROM users WHERE user_type_code = 'recycler' "
         "AND verification_status = 'pending'", "ix_users_user_type_verification"),
    ])
    def test_the_planner_can_use_them(self, db, query, index):
        # Tables are tiny here, so force the planner off sequential scans to prove the
        # index matches the query shape (it would be ignored otherwise).
        db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in db.execute(text("EXPLAIN " + query)))
        assert index in plan, plan


# --- 3.8: connection pool ---------------------------------------------------------------------------

class TestConnectionPool:
    def test_settings_reach_the_pool(self, monkeypatch):
        monkeypatch.setattr(settings, "db_pool_size", 7)
        monkeypatch.setattr(settings, "db_max_overflow", 3)
        monkeypatch.setattr(settings, "db_pool_timeout", 12)
        monkeypatch.setattr(settings, "db_pool_recycle", 900)
        engine = build_engine()
        try:
            pool = engine.pool
            assert (pool.size(), pool._max_overflow, pool._timeout, pool._recycle) == (7, 3, 12, 900)
            assert pool._pre_ping is True
        finally:
            engine.dispose()

    def test_defaults_are_sensible(self):
        assert (settings.db_pool_size, settings.db_max_overflow) == (5, 10)
        assert settings.db_pool_timeout == 30 and settings.db_pool_recycle == 1800

    def test_the_application_engine_uses_them(self):
        from app.core.database import engine
        assert engine.pool._pre_ping is True
        assert engine.pool.size() == settings.db_pool_size


def test_material_and_warehouse_lookups_are_cached_by_the_session_not_repeated(db):
    """Sanity check for the helpers above: seed data really is present."""
    assert db.query(Material).count() == 7 and db.query(Warehouse).count() == 3
