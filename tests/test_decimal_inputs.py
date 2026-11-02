"""Amounts sent to the API keep the precision of the columns: two decimals and no more digits than fit.

A third decimal used to be accepted and rounded by the database; a value that does not fit used to fail inside it.
Both are now a 422 before anything is written.
"""
from decimal import Decimal

import pytest

from app.domains.inventory.models import InventoryItem
from tests import factories

TOO_PRECISE = ["1.001", "25.125", "0.005"]
TOO_WIDE = ["100000000.00", "123456789012"]


def _sale(client_as, user, warehouse, **overrides):
    payload = {"material_code": "plastic", "warehouse_id": str(warehouse.id), "kg": "10", "price_per_kg": "900", **overrides}
    return client_as(user).post("/transactions", json=payload)


class TestSales:
    @pytest.mark.parametrize("field", ["kg", "price_per_kg"])
    @pytest.mark.parametrize("value", TOO_PRECISE + TOO_WIDE)
    def test_a_sale_refuses_what_the_column_cannot_hold_exactly(self, client_as, eca_admin, warehouse, db, field, value):
        factories.stock(db, warehouse, kg="100")
        assert _sale(client_as, eca_admin, warehouse, **{field: value}).status_code == 422

    @pytest.mark.parametrize("kg, price", [("10", "900"), ("10.5", "900.25"), ("0.01", "0.01"), ("99999999.99", "1")])
    def test_a_sale_accepts_up_to_two_decimals(self, client_as, eca_admin, warehouse, db, kg, price):
        factories.stock(db, warehouse, kg="100000000")  # stock is wider than a sale: Numeric(12, 2)
        r = _sale(client_as, eca_admin, warehouse, kg=kg, price_per_kg=price)
        assert r.status_code == 201, r.text
        assert Decimal(r.json()["kg"]) == Decimal(kg) and Decimal(r.json()["price_per_kg"]) == Decimal(price)


class TestInventoryEdits:
    @pytest.mark.parametrize("field", ["stock_min_kg", "price_per_kg"])
    @pytest.mark.parametrize("value", TOO_PRECISE + ["-1", "-0.01"])
    def test_editing_refuses_extra_decimals_and_negative_values(self, client_as, eca_admin, warehouse, db, field, value):
        item = factories.stock(db, warehouse)
        r = client_as(eca_admin).patch(f"/inventory/{item.id}", json={field: value})
        assert r.status_code == 422
        assert getattr(db.get(InventoryItem, item.id, populate_existing=True), field) == getattr(item, field)

    @pytest.mark.parametrize("field, value", [("stock_min_kg", "123456789012"), ("price_per_kg", "100000000")])
    def test_editing_refuses_a_value_wider_than_the_column(self, client_as, eca_admin, warehouse, db, field, value):
        item = factories.stock(db, warehouse)
        assert client_as(eca_admin).patch(f"/inventory/{item.id}", json={field: value}).status_code == 422

    def test_editing_accepts_two_decimals_and_zero(self, client_as, eca_admin, warehouse, db):
        item = factories.stock(db, warehouse)
        r = client_as(eca_admin).patch(f"/inventory/{item.id}", json={"stock_min_kg": "0", "price_per_kg": "750.55"})
        assert r.status_code == 200, r.text
        assert Decimal(r.json()["stock_min_kg"]) == 0 and Decimal(r.json()["price_per_kg"]) == Decimal("750.55")
