"""Period and material filters on the /stats endpoints (the dashboards and reports of Sprint 8).

With no filter they keep answering for the current month, as before; these tests cover what the filters add.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.domains.transactions.models import Transaction
from app.domains.weighings.models import Weighing
from tests import factories


def _weigh(client_as, eca_admin, recycler, warehouse, material, kg, validate=False):
    r = client_as(eca_admin).post("/weighings", json={
        "recycler_id": str(recycler.id), "material_code": material, "warehouse_id": str(warehouse.id),
        "kg": kg, "price_per_kg": "400"})
    assert r.status_code == 201, r.text
    weighing_id = r.json()["id"]
    if validate:
        assert client_as(eca_admin).patch(f"/weighings/{weighing_id}/status", json={"status": "validated"}).status_code == 200
    return weighing_id


def _move(db, model, row_id, when):
    db.execute(update(model).where(model.id == row_id).values(occurred_at=when))
    db.commit()


def _iso(moment):
    return moment.isoformat()


@pytest.fixture
def history(client_as, eca_admin, recycler, warehouse, db):
    """Plastic 10 kg this month, plastic 20 kg and glass 5 kg two months ago (validated, so they are purchases)."""
    now = datetime.now(timezone.utc)
    two_months_ago = (now.replace(day=1) - timedelta(days=45)).replace(day=10, hour=12)
    _weigh(client_as, eca_admin, recycler, warehouse, "plastic", "10")
    old_plastic = _weigh(client_as, eca_admin, recycler, warehouse, "plastic", "20", validate=True)
    old_glass = _weigh(client_as, eca_admin, recycler, warehouse, "glass", "5", validate=True)
    for weighing_id in (old_plastic, old_glass):
        _move(db, Weighing, weighing_id, two_months_ago)
    for tx in db.query(Transaction).all():
        _move(db, Transaction, tx.id, two_months_ago)
    return two_months_ago


class TestWeighingStats:
    def test_without_filters_it_is_still_this_month(self, client_as, eca_admin, history):
        body = client_as(eca_admin).get("/weighings/stats").json()
        assert body["total_weighings_month"] == 1 and Decimal(body["total_kg_month"]) == Decimal("10")

    def test_a_period_covers_what_was_weighed_in_it(self, client_as, eca_admin, history):
        start, end = history - timedelta(days=1), history + timedelta(days=1)
        body = client_as(eca_admin).get("/weighings/stats", params={"date_from": _iso(start), "date_to": _iso(end)}).json()
        assert body["total_weighings_month"] == 2 and Decimal(body["total_kg_month"]) == Decimal("25")
        assert {m["material"]: m["kg"] for m in body["by_material"]} == {"glass": 5.0, "plastic": 20.0}

    def test_the_end_of_the_period_is_excluded_and_the_start_included(self, client_as, eca_admin, history):
        at_start = client_as(eca_admin).get("/weighings/stats", params={"date_from": _iso(history), "date_to": _iso(history + timedelta(days=1))}).json()
        at_end = client_as(eca_admin).get("/weighings/stats", params={"date_from": _iso(history - timedelta(days=1)), "date_to": _iso(history)}).json()
        assert at_start["total_weighings_month"] == 2 and at_end["total_weighings_month"] == 0

    def test_a_material_narrows_every_figure(self, client_as, eca_admin, history):
        params = {"date_from": _iso(history - timedelta(days=1)), "date_to": _iso(history + timedelta(days=1)),
                  "material_code": "glass"}
        body = client_as(eca_admin).get("/weighings/stats", params=params).json()
        assert body["total_weighings_month"] == 1 and Decimal(body["total_kg_month"]) == Decimal("5")
        assert body["by_material"] == [{"material": "glass", "kg": 5.0}]

    def test_the_material_also_narrows_the_pending_count(self, client_as, eca_admin, history):
        plastic = client_as(eca_admin).get("/weighings/stats", params={"material_code": "plastic"}).json()
        glass = client_as(eca_admin).get("/weighings/stats", params={"material_code": "glass"}).json()
        assert plastic["pending_count"] == 1 and glass["pending_count"] == 0

    def test_a_material_without_weighings_gives_zeros(self, client_as, eca_admin, history):
        body = client_as(eca_admin).get("/weighings/stats", params={"material_code": "metal"}).json()
        assert body["total_weighings_month"] == 0 and Decimal(body["total_kg_month"]) == 0 and body["by_material"] == []

    def test_another_organization_never_sees_them(self, client_as, db, history):
        other = factories.make_organization(db, "eca", legal_name="Otra ECA")
        outsider = factories.make_user(db, "eca", role_code="eca_admin", organization_id=other.id)
        params = {"date_from": _iso(history - timedelta(days=1)), "date_to": _iso(history + timedelta(days=1))}
        assert client_as(outsider).get("/weighings/stats", params=params).json()["total_weighings_month"] == 0

    @pytest.mark.parametrize("endpoint", ["/weighings/stats", "/transactions/stats"])
    def test_the_end_must_come_after_the_start(self, client_as, eca_admin, endpoint):
        now = datetime.now(timezone.utc)
        for start, end in ((now, now), (now, now - timedelta(days=1))):
            r = client_as(eca_admin).get(endpoint, params={"date_from": _iso(start), "date_to": _iso(end)})
            assert r.status_code == 422 and r.json()["code"] == "invalid_period"

    def test_a_date_without_a_time_zone_counts_as_utc(self, client_as, eca_admin, history):
        naive = (history - timedelta(days=1)).replace(tzinfo=None)
        body = client_as(eca_admin).get("/weighings/stats", params={"date_from": naive.isoformat()}).json()
        assert body["total_weighings_month"] == 3


class TestTransactionStats:
    def test_without_filters_it_is_still_this_month(self, client_as, eca_admin, history):
        assert client_as(eca_admin).get("/transactions/stats").json()["total_purchases_month"] == 0

    def test_a_period_and_a_material_narrow_the_purchases(self, client_as, eca_admin, history):
        window = {"date_from": _iso(history - timedelta(days=1)), "date_to": _iso(history + timedelta(days=1))}
        both = client_as(eca_admin).get("/transactions/stats", params=window).json()
        glass = client_as(eca_admin).get("/transactions/stats", params={**window, "material_code": "glass"}).json()
        assert both["total_purchases_month"] == 2 and Decimal(both["total_kg_purchases"]) == Decimal("25")
        assert glass["total_purchases_month"] == 1 and Decimal(glass["total_kg_purchases"]) == Decimal("5")


class TestInventoryStats:
    def test_a_material_narrows_the_stock_figures(self, client_as, eca_admin, warehouse, db):
        factories.stock(db, warehouse, "plastic", kg="100", price_per_kg="500")
        factories.stock(db, warehouse, "glass", kg="30", price_per_kg="100")
        everything = client_as(eca_admin).get("/inventory/stats").json()
        glass = client_as(eca_admin).get("/inventory/stats", params={"material_code": "glass"}).json()
        assert Decimal(everything["total_stock_kg"]) == Decimal("130")
        assert Decimal(glass["total_stock_kg"]) == Decimal("30") and Decimal(glass["total_value"]) == Decimal("3000")
        assert glass["low_stock_count"] == 1  # 30 kg is under the default minimum
