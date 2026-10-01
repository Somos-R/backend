"""GET /inventory?sort=&order= and GET /inventory/export.csv."""
from types import SimpleNamespace

import pytest

from app.domains.audit.actions import Action
from app.domains.audit.models import AuditLog
from app.domains.inventory import service as inventory_service
from tests import factories


@pytest.fixture
def inv(db, warehouse):
    """Plastic 100 kg @500 (available), glass 10 kg @100 (low), paper 40 kg @300 (low)."""
    factories.stock(db, warehouse, "plastic", kg="100", price_per_kg="500")
    factories.stock(db, warehouse, "glass", kg="10", price_per_kg="100")
    factories.stock(db, warehouse, "paper", kg="40", price_per_kg="300")
    return SimpleNamespace(warehouse=warehouse)


def _materials(client_as, user, **params):
    r = client_as(user).get("/inventory", params={"limit": 100, **params})
    assert r.status_code == 200, r.text
    return [i["material_code"] for i in r.json()["items"]]


class TestSort:
    def test_without_sort_the_order_is_by_material_code(self, client_as, eca_admin, inv):
        assert _materials(client_as, eca_admin) == ["glass", "paper", "plastic"]

    def test_by_stock_in_both_directions(self, client_as, eca_admin, inv):
        assert _materials(client_as, eca_admin, sort="stock_kg", order="asc") == ["glass", "paper", "plastic"]
        assert _materials(client_as, eca_admin, sort="stock_kg", order="desc") == ["plastic", "paper", "glass"]

    def test_by_total_value(self, client_as, eca_admin, inv):
        # plastic 50000, paper 12000, glass 1000
        assert _materials(client_as, eca_admin, sort="total_value", order="desc") == ["plastic", "paper", "glass"]

    def test_by_price(self, client_as, eca_admin, inv):
        assert _materials(client_as, eca_admin, sort="price_per_kg") == ["glass", "paper", "plastic"]

    def test_by_material_uses_the_label_people_read(self, client_as, eca_admin, inv):
        items = client_as(eca_admin).get("/inventory", params={"sort": "material"}).json()["items"]
        labels = [i["material"]["label"] for i in items]
        assert labels == sorted(labels)

    def test_by_warehouse_name(self, client_as, eca_admin, db, inv):
        factories.own_all_warehouses(db)
        items = client_as(eca_admin).get("/inventory", params={"sort": "warehouse", "order": "desc"}).json()["items"]
        names = [i["warehouse"]["name"] for i in items]
        assert names == sorted(names, reverse=True)

    def test_by_status(self, client_as, eca_admin, inv):
        # 'available' sorts before 'low_stock'
        assert _materials(client_as, eca_admin, sort="status")[0] == "plastic"

    def test_unknown_column_is_rejected(self, client_as, eca_admin):
        assert client_as(eca_admin).get("/inventory", params={"sort": "price"}).status_code == 422

    def test_pages_do_not_overlap_when_values_tie(self, client_as, eca_admin, inv):
        seen = []
        for offset in (0, 1, 2):
            seen += _materials(client_as, eca_admin, sort="status", limit=1, offset=offset)
        assert sorted(seen) == ["glass", "paper", "plastic"]


class TestExport:
    def test_csv_with_every_row_in_the_requested_order(self, client_as, eca_admin, inv):
        r = client_as(eca_admin).get("/inventory/export.csv", params={"sort": "stock_kg", "order": "desc"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        lines = r.text.lstrip("﻿").strip().splitlines()
        assert lines[0].startswith("material,warehouse,stock_kg")
        assert [line.split(",")[2] for line in lines[1:]] == ["100.00", "40.00", "10.00"]

    def test_respects_the_filters(self, client_as, eca_admin, inv):
        r = client_as(eca_admin).get("/inventory/export.csv", params={"status": "low_stock"})
        assert len(r.text.strip().splitlines()) == 3  # header + glass + paper

    def test_only_what_the_actor_may_read(self, db, client_as, inv):
        other = factories.make_organization(db, "eca", legal_name="ECA Beta")
        stranger = factories.make_user(db, "eca", role_code="eca_admin", organization_id=other.id)
        r = client_as(stranger).get("/inventory/export.csv")
        assert r.status_code == 200
        assert len(r.text.strip().splitlines()) == 1

    def test_roles_that_cannot_read_inventory_cannot_export(self, client_as, recycler):
        assert client_as(recycler).get("/inventory/export.csv").status_code == 403

    def test_too_many_rows_is_refused_not_truncated(self, client_as, eca_admin, inv, monkeypatch):
        monkeypatch.setattr(inventory_service, "MAX_EXPORT_ROWS", 2)
        r = client_as(eca_admin).get("/inventory/export.csv")
        assert r.status_code == 400 and r.json()["code"] == "export_too_large"

    def test_is_audited_without_personal_data(self, db, client_as, eca_admin, inv):
        client_as(eca_admin).get("/inventory/export.csv")
        entry = db.query(AuditLog).filter(AuditLog.action == Action.INVENTORY_EXPORTED).one()
        assert entry.details == {"rows": 3}
