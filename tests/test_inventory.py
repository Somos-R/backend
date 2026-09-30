"""Characterization tests for /inventory."""
from decimal import Decimal

from tests import factories

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def test_materials_and_warehouses_are_seeded(client_as, eca_admin, db):
    factories.own_all_warehouses(db)
    c = client_as(eca_admin)
    materials = c.get("/inventory/materials")
    warehouses = c.get("/inventory/warehouses")
    assert materials.status_code == 200 and warehouses.status_code == 200
    assert "plastic" in {m["code"] for m in materials.json()}
    assert len(warehouses.json()) == 3


class TestList:
    def test_lists_items_with_computed_state(self, client_as, eca_admin, db, warehouse):
        factories.stock(db, warehouse, "plastic", kg="100")
        factories.stock(db, warehouse, "glass", kg="10")  # below default min of 50
        r = client_as(eca_admin).get("/inventory")
        assert r.status_code == 200
        by_material = {i["material_code"]: i for i in r.json()["items"]}
        assert by_material["plastic"]["status"] == "available"
        assert by_material["glass"]["status"] == "low_stock"

    def test_filter_by_material(self, client_as, eca_admin, db, warehouse):
        factories.stock(db, warehouse, "plastic")
        factories.stock(db, warehouse, "glass")
        r = client_as(eca_admin).get("/inventory?material_code=glass")
        assert [i["material_code"] for i in r.json()["items"]] == ["glass"]

    def test_filter_by_status(self, client_as, eca_admin, db, warehouse):
        factories.stock(db, warehouse, "plastic", kg="100")
        factories.stock(db, warehouse, "glass", kg="10")
        r = client_as(eca_admin).get("/inventory?status=low_stock")
        assert [i["material_code"] for i in r.json()["items"]] == ["glass"]
        assert r.json()["total"] == 1

    def test_pagination(self, client_as, eca_admin, db, warehouse):
        for material in ("plastic", "glass", "metal"):
            factories.stock(db, warehouse, material)
        r = client_as(eca_admin).get("/inventory?limit=2&offset=2")
        assert r.json()["total"] == 3
        assert len(r.json()["items"]) == 1


def test_stats(client_as, eca_admin, db, warehouse):
    factories.stock(db, warehouse, "plastic", kg="100", price_per_kg="500")
    factories.stock(db, warehouse, "glass", kg="10", price_per_kg="100")
    r = client_as(eca_admin).get("/inventory/stats")
    assert r.status_code == 200
    body = r.json()
    assert Decimal(body["total_stock_kg"]) == Decimal("110")
    assert Decimal(body["total_value"]) == Decimal("51000")
    assert body["available_count"] == 1
    assert body["low_stock_count"] == 1


class TestItem:
    def test_get(self, client_as, eca_admin, db, warehouse):
        item = factories.stock(db, warehouse)
        r = client_as(eca_admin).get(f"/inventory/{item.id}")
        assert r.status_code == 200
        assert r.json()["material"]["code"] == "plastic"

    def test_get_not_found(self, client_as, eca_admin):
        assert client_as(eca_admin).get(f"/inventory/{MISSING_ID}").status_code == 404

    def test_patch_updates_min_stock_and_price(self, client_as, eca_admin, db, warehouse):
        item = factories.stock(db, warehouse)
        r = client_as(eca_admin).patch(
            f"/inventory/{item.id}", json={"stock_min_kg": "200", "price_per_kg": "750"})
        assert r.status_code == 200
        assert Decimal(r.json()["stock_min_kg"]) == 200
        assert Decimal(r.json()["price_per_kg"]) == 750
        assert r.json()["status"] == "low_stock"  # 100 kg < new min of 200

    def test_patch_not_found(self, client_as, eca_admin):
        r = client_as(eca_admin).patch(f"/inventory/{MISSING_ID}", json={"price_per_kg": "1"})
        assert r.status_code == 404


def test_the_old_spanish_status_and_material_codes_are_gone(client_as, eca_admin, db, warehouse):
    factories.stock(db, warehouse, "plastic", kg="100")
    c = client_as(eca_admin)
    assert c.get("/inventory?status=disponible").json()["items"] == []
    assert c.get("/inventory?material_code=plastico").json()["items"] == []
    codes = {m["code"] for m in c.get("/inventory/materials").json()}
    assert codes == {"paper", "plastic", "glass", "metal", "cardboard", "electronic", "organic"}
