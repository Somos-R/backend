"""Characterization tests for /transactions (sales and the purchase lifecycle)."""
from decimal import Decimal

from app.domains.inventory.models import InventoryItem
from tests import factories

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def _sale_payload(warehouse, **overrides):
    payload = {
        "material_code": "plastic",
        "warehouse_id": str(warehouse.id),
        "kg": "40",
        "price_per_kg": "900",
        "buyer_name": "Industrias Verdes",
        "buyer_nit": "901234567-8",
    }
    payload.update(overrides)
    return payload


def _stock_kg(db, warehouse, material="plastic"):
    db.expire_all()
    return db.query(InventoryItem).filter_by(
        material_code=material, warehouse_id=warehouse.id).one().stock_kg


class TestCreateSale:
    def test_subtracts_stock_and_starts_pending(self, client_as, eca_admin, db, warehouse):
        factories.stock(db, warehouse, kg="100")
        r = client_as(eca_admin).post("/transactions", json=_sale_payload(warehouse))
        assert r.status_code == 201
        body = r.json()
        assert body["type"] == "sale" and body["status"] == "pending"
        assert Decimal(body["total_value"]) == Decimal("36000")
        assert _stock_kg(db, warehouse) == Decimal("60")

    def test_insufficient_stock(self, client_as, eca_admin, db, warehouse):
        factories.stock(db, warehouse, kg="10")
        r = client_as(eca_admin).post("/transactions", json=_sale_payload(warehouse))
        assert r.status_code == 400
        assert _stock_kg(db, warehouse) == Decimal("10")

    def test_no_inventory_for_material(self, client_as, eca_admin, warehouse):
        r = client_as(eca_admin).post("/transactions", json=_sale_payload(warehouse))
        assert r.status_code == 404

    def test_unknown_material_and_warehouse(self, client_as, eca_admin, warehouse):
        c = client_as(eca_admin)
        assert c.post("/transactions", json=_sale_payload(
            warehouse, material_code="nope")).status_code == 404
        assert c.post("/transactions", json=_sale_payload(
            warehouse, warehouse_id=MISSING_ID)).status_code == 404

    def test_non_positive_values_are_rejected(self, client_as, eca_admin, warehouse):
        for field in ("kg", "price_per_kg"):
            r = client_as(eca_admin).post(
                "/transactions", json=_sale_payload(warehouse, **{field: "0"}))
            assert r.status_code == 422


class TestReadEndpoints:
    def _make_two(self, c, db, warehouse):
        factories.stock(db, warehouse, kg="100")
        c.post("/transactions", json=_sale_payload(warehouse, kg="10"))
        c.post("/transactions", json=_sale_payload(warehouse, kg="20"))

    def test_list_and_filters(self, client_as, eca_admin, db, warehouse):
        c = client_as(eca_admin)
        self._make_two(c, db, warehouse)
        assert c.get("/transactions").json()["total"] == 2
        assert c.get("/transactions?type=sale").json()["total"] == 2
        assert c.get("/transactions?type=purchase").json()["total"] == 0
        assert c.get("/transactions?status=pending").json()["total"] == 2
        assert c.get("/transactions?material_code=glass").json()["total"] == 0

    def test_the_old_spanish_filters_are_gone(self, client_as, eca_admin):
        c = client_as(eca_admin)
        assert c.get("/transactions?type=venta").status_code == 422
        assert c.get("/transactions?type=compra").status_code == 422
        assert c.get("/transactions?status=pendiente").status_code == 422

    def test_get_one(self, client_as, eca_admin, db, warehouse):
        c = client_as(eca_admin)
        factories.stock(db, warehouse)
        created = c.post("/transactions", json=_sale_payload(warehouse, kg="5")).json()
        assert c.get(f"/transactions/{created['id']}").json()["id"] == created["id"]

    def test_get_not_found(self, client_as, eca_admin):
        assert client_as(eca_admin).get(f"/transactions/{MISSING_ID}").status_code == 404

    def test_stats(self, client_as, eca_admin, db, warehouse):
        c = client_as(eca_admin)
        self._make_two(c, db, warehouse)
        body = c.get("/transactions/stats").json()
        assert body["total_sales_month"] == 2
        assert body["total_purchases_month"] == 0
        assert Decimal(body["total_kg_sales"]) == Decimal("30")
        assert Decimal(body["total_value_sales"]) == Decimal("27000")
        assert body["pending_count"] == 2


class TestStatusTransitions:
    def _sale(self, c, db, warehouse, kg="40"):
        factories.stock(db, warehouse, kg="100")
        return c.post("/transactions", json=_sale_payload(warehouse, kg=kg)).json()

    def test_cancel_sale_restores_stock(self, client_as, eca_admin, db, warehouse):
        c = client_as(eca_admin)
        tx = self._sale(c, db, warehouse)
        assert _stock_kg(db, warehouse) == Decimal("60")
        r = c.patch(f"/transactions/{tx['id']}/status", json={"status": "cancelled"})
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert _stock_kg(db, warehouse) == Decimal("100")

    def test_cancel_twice_is_rejected(self, client_as, eca_admin, db, warehouse):
        c = client_as(eca_admin)
        tx = self._sale(c, db, warehouse)
        c.patch(f"/transactions/{tx['id']}/status", json={"status": "cancelled"})
        r = c.patch(f"/transactions/{tx['id']}/status", json={"status": "cancelled"})
        assert r.status_code == 400
        assert _stock_kg(db, warehouse) == Decimal("100")  # not restored twice

    def test_mark_sale_delivered(self, client_as, eca_admin, db, warehouse):
        c = client_as(eca_admin)
        tx = self._sale(c, db, warehouse)
        r = c.patch(f"/transactions/{tx['id']}/status", json={"status": "delivered"})
        assert r.status_code == 200 and r.json()["status"] == "delivered"
        # A delivered sale can no longer be cancelled.
        assert c.patch(f"/transactions/{tx['id']}/status",
                       json={"status": "cancelled"}).status_code == 400

    def test_sale_cannot_be_marked_paid(self, client_as, eca_admin, db, warehouse):
        c = client_as(eca_admin)
        tx = self._sale(c, db, warehouse)
        r = c.patch(f"/transactions/{tx['id']}/status", json={"status": "paid"})
        assert r.status_code == 400

    def test_purchase_lifecycle_from_validated_weighing(
        self, client_as, eca_admin, recycler, warehouse
    ):
        c = client_as(eca_admin)
        w = c.post("/weighings", json={
            "recycler_id": str(recycler.id), "material_code": "plastic",
            "warehouse_id": str(warehouse.id), "kg": "20", "price_per_kg": "300",
        }).json()
        c.patch(f"/weighings/{w['id']}/status", json={"status": "validated"})

        purchase = c.get("/transactions?type=purchase").json()["items"][0]
        assert purchase["weighing_id"] == w["id"]

        # A purchase cannot be marked as delivered, only paid.
        assert c.patch(f"/transactions/{purchase['id']}/status",
                       json={"status": "delivered"}).status_code == 400
        r = c.patch(f"/transactions/{purchase['id']}/status", json={"status": "paid"})
        assert r.status_code == 200 and r.json()["status"] == "paid"

    def test_unsupported_status(self, client_as, eca_admin, db, warehouse):
        c = client_as(eca_admin)
        tx = self._sale(c, db, warehouse)
        r = c.patch(f"/transactions/{tx['id']}/status", json={"status": "pending"})
        assert r.status_code == 400

    def test_not_found(self, client_as, eca_admin):
        r = client_as(eca_admin).patch(
            f"/transactions/{MISSING_ID}/status", json={"status": "cancelled"})
        assert r.status_code == 404
