"""Characterization tests for /weighings, including the validation side effects."""
from decimal import Decimal

from app.domains.inventory.models import InventoryItem
from app.domains.transactions.models import (
    Transaction,
    TransactionStatus,
    TransactionType,
)
from tests import factories

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def _payload(recycler, warehouse, **overrides):
    payload = {
        "recycler_id": str(recycler.id),
        "material_code": "plastico",
        "warehouse_id": str(warehouse.id),
        "kg": "25.5",
        "precio_kg": "400",
    }
    payload.update(overrides)
    return payload


def _create(client, recycler, warehouse, **overrides):
    r = client.post("/weighings", json=_payload(recycler, warehouse, **overrides))
    assert r.status_code == 201, r.text
    return r.json()


class TestCreate:
    def test_creates_pending_weighing(self, client_as, eca_admin, recycler, warehouse):
        body = _create(client_as(eca_admin), recycler, warehouse)
        assert body["estado"] == "pendiente"
        assert Decimal(body["total_value"]) == Decimal("10200")
        assert body["recycler"]["id"] == str(recycler.id)

    def test_recycler_must_exist_and_be_a_recycler(self, client_as, eca_admin, citizen, warehouse):
        r = client_as(eca_admin).post("/weighings", json=_payload(citizen, warehouse))
        assert r.status_code == 404

    def test_unknown_material(self, client_as, eca_admin, recycler, warehouse):
        r = client_as(eca_admin).post(
            "/weighings", json=_payload(recycler, warehouse, material_code="unobtanium"))
        assert r.status_code == 404

    def test_unknown_warehouse(self, client_as, eca_admin, recycler, warehouse):
        r = client_as(eca_admin).post(
            "/weighings", json=_payload(recycler, warehouse, warehouse_id=MISSING_ID))
        assert r.status_code == 404

    def test_non_positive_values_are_rejected(self, client_as, eca_admin, recycler, warehouse):
        for field in ("kg", "precio_kg"):
            for bad in ("0", "-5"):
                r = client_as(eca_admin).post(
                    "/weighings", json=_payload(recycler, warehouse, **{field: bad}))
                assert r.status_code == 422


class TestReadEndpoints:
    def test_list_and_filters(self, client_as, eca_admin, recycler, warehouse, db):
        c = client_as(eca_admin)
        _create(c, recycler, warehouse, material_code="plastico")
        _create(c, recycler, warehouse, material_code="vidrio")

        assert c.get("/weighings").json()["total"] == 2
        r = c.get("/weighings?material_code=vidrio")
        assert [w["material_code"] for w in r.json()["items"]] == ["vidrio"]
        assert c.get("/weighings?estado=validado").json()["total"] == 0
        other = factories.make_user(db, "recycler")
        assert c.get(f"/weighings?recycler_id={other.id}").json()["total"] == 0

    def test_get_one(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        created = _create(c, recycler, warehouse)
        assert c.get(f"/weighings/{created['id']}").json()["id"] == created["id"]

    def test_get_not_found(self, client_as, eca_admin):
        assert client_as(eca_admin).get(f"/weighings/{MISSING_ID}").status_code == 404

    def test_stats(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        _create(c, recycler, warehouse, kg="10")
        _create(c, recycler, warehouse, kg="5", material_code="vidrio")
        body = c.get("/weighings/stats").json()
        assert body["total_weighings_month"] == 2
        assert Decimal(body["total_kg_month"]) == Decimal("15")
        assert body["pending_count"] == 2
        assert {m["material"] for m in body["by_material"]} == {"plastico", "vidrio"}


class TestStatusTransitions:
    def test_validate_adds_stock_and_creates_purchase(
        self, client_as, eca_admin, recycler, warehouse, db
    ):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse, kg="30", precio_kg="400")

        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "validado"})
        assert r.status_code == 200
        assert r.json()["estado"] == "validado"
        assert r.json()["validated_by"] == str(eca_admin.id)

        item = db.query(InventoryItem).filter_by(
            material_code="plastico", warehouse_id=warehouse.id).one()
        assert item.stock_kg == Decimal("30")
        assert item.precio_kg == Decimal("400")

        tx = db.query(Transaction).filter_by(weighing_id=w["id"]).one()
        assert tx.type == TransactionType.compra
        assert tx.status == TransactionStatus.pendiente
        assert tx.recycler_id == recycler.id

    def test_validate_twice_is_rejected(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        c.patch(f"/weighings/{w['id']}/status", json={"status": "validado"})
        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "validado"})
        assert r.status_code == 400

    def test_reject_requires_reason(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "rechazado"})
        assert r.status_code == 400

    def test_reject_with_reason_does_not_touch_stock(
        self, client_as, eca_admin, recycler, warehouse, db
    ):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        r = c.patch(
            f"/weighings/{w['id']}/status",
            json={"status": "rechazado", "rejection_reason": "Material contaminado"})
        assert r.status_code == 200
        assert r.json()["estado"] == "rechazado"
        assert db.query(InventoryItem).count() == 0

    def test_pay_requires_validated(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        assert c.patch(f"/weighings/{w['id']}/status", json={"status": "pagado"}).status_code == 400

    def test_full_happy_path_to_paid(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        c.patch(f"/weighings/{w['id']}/status", json={"status": "validado"})
        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "pagado"})
        assert r.status_code == 200
        assert r.json()["estado"] == "pagado"

    def test_cannot_go_back_to_pending(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "pendiente"})
        assert r.status_code == 400

    def test_not_found(self, client_as, eca_admin):
        r = client_as(eca_admin).patch(
            f"/weighings/{MISSING_ID}/status", json={"status": "validado"})
        assert r.status_code == 404
