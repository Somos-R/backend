"""Catalogs from the backoffice (6.14): materials, document types and warehouses. Never deleted, only retired:
retiring stops new use and leaves what exists alone."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import permissions
from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.audit.models import AuditLog
from app.domains.inventory.models import Material, Warehouse
from app.main import app
from tests import factories


@pytest.fixture
def admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


@pytest.fixture
def bo(client, admin):
    return TestClient(app, headers=factories.backoffice_headers(admin))


@pytest.fixture
def eca(db):
    org = factories.make_organization(db, "eca", legal_name="ECA Norte")
    wh = db.query(Warehouse).order_by(Warehouse.name).first()
    wh.organization_id = org.id
    db.commit()
    return type("Eca", (), {
        "org": org, "wh": wh,
        "admin": factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id),
        "operator": factories.make_user(db, "eca", role_code="eca_operator", organization_id=org.id),
    })


def _audit(db, action, target_id):
    return db.scalars(select(AuditLog).where(AuditLog.action == action, AuditLog.target_id == str(target_id))).all()


def _weigh(client_as, user, warehouse, material="plastic", **extra):
    body = {"material_code": material, "warehouse_id": str(warehouse.id), "kg": "10", "price_per_kg": "400",
            "seller": {"full_name": "Ana Vendedora", "id_type": "CC", "id_number": "52123456"}, **extra}
    return client_as(user).post("/weighings", json=body)


class TestMaterials:
    def test_create_list_and_rename(self, bo):
        r = bo.post("/admin/catalogs/materials", json={"code": "glass_clear", "label": "Vidrio transparente"})
        assert r.status_code == 201, r.text
        assert r.json() == {"code": "glass_clear", "label": "Vidrio transparente", "unit": "kg", "is_active": True}
        assert "glass_clear" in {m["code"] for m in bo.get("/admin/catalogs/materials").json()}
        r = bo.patch("/admin/catalogs/materials/glass_clear", json={"label": "Vidrio claro"})
        assert r.status_code == 200 and r.json()["label"] == "Vidrio claro"

    @pytest.mark.parametrize("code", ["Glass", "1glass", "g", "gl ass", "vidrio-claro", "x" * 31])
    def test_the_code_has_a_fixed_shape(self, bo, code):
        r = bo.post("/admin/catalogs/materials", json={"code": code, "label": "Algo"})
        assert r.status_code == 422

    def test_a_code_cannot_be_reused(self, bo):
        r = bo.post("/admin/catalogs/materials", json={"code": "plastic", "label": "Otro"})
        assert r.status_code == 409 and r.json()["code"] == "code_already_exists"

    def test_a_missing_material_is_404_and_an_empty_change_is_422(self, bo):
        assert bo.patch("/admin/catalogs/materials/nope", json={"label": "x"}).status_code == 404
        assert bo.patch("/admin/catalogs/materials/plastic", json={}).status_code == 422

    def test_the_public_list_only_shows_active_ones_and_the_backoffice_shows_all(self, bo, client_as, eca):
        bo.patch("/admin/catalogs/materials/plastic", json={"is_active": False})
        public = {m["code"] for m in client_as(eca.admin).get("/inventory/materials").json()}
        assert "plastic" not in public
        assert "plastic" in {m["code"] for m in bo.get("/admin/catalogs/materials").json()}
        inactive = bo.get("/admin/catalogs/materials", params={"is_active": "false"}).json()
        assert {m["code"] for m in inactive} == {"plastic"}

    def test_it_is_audited_with_the_change(self, bo, db, admin):
        bo.post("/admin/catalogs/materials", json={"code": "wood", "label": "Madera"})
        bo.patch("/admin/catalogs/materials/wood", json={"label": "Madera tratada", "is_active": False})
        bo.patch("/admin/catalogs/materials/wood", json={"label": "Madera tratada"})  # no change: nothing recorded
        assert len(_audit(db, "catalog.created", "wood")) == 1
        (update,) = _audit(db, "catalog.updated", "wood")
        assert update.actor_id == admin.id
        assert update.details["changes"] == {"label": ["Madera", "Madera tratada"], "is_active": [True, False]}


class TestRetiringAMaterialStopsNewUseOnly:
    def test_a_new_weighing_is_refused_but_an_existing_one_still_validates(self, bo, client_as, eca):
        before = _weigh(client_as, eca.operator, eca.wh).json()["id"]
        bo.patch("/admin/catalogs/materials/plastic", json={"is_active": False})
        r = _weigh(client_as, eca.operator, eca.wh)
        assert r.status_code == 400 and r.json()["code"] == "material_inactive"
        assert client_as(eca.admin).patch(f"/weighings/{before}/status", json={"status": "validated"}).status_code == 200

    def test_a_new_sale_is_refused(self, bo, client_as, eca):
        bo.patch("/admin/catalogs/materials/plastic", json={"is_active": False})
        r = client_as(eca.admin).post("/transactions", json={
            "material_code": "plastic", "warehouse_id": str(eca.wh.id), "kg": "1", "price_per_kg": "100"})
        assert r.status_code == 400 and r.json()["code"] == "material_inactive"

    def test_reactivating_restores_it(self, bo, client_as, eca):
        bo.patch("/admin/catalogs/materials/plastic", json={"is_active": False})
        bo.patch("/admin/catalogs/materials/plastic", json={"is_active": True})
        assert _weigh(client_as, eca.operator, eca.wh).status_code == 201

    def test_an_unknown_material_is_still_404(self, client_as, eca):
        assert _weigh(client_as, eca.operator, eca.wh, material="nope").status_code == 404


class TestDocumentTypes:
    def test_create_rename_and_the_code_shape(self, bo):
        r = bo.post("/admin/catalogs/document-types", json={"code": "SIC", "label": "Carnet SIC"})
        assert r.status_code == 201 and r.json() == {"code": "SIC", "label": "Carnet SIC", "is_active": True}
        assert bo.patch("/admin/catalogs/document-types/SIC", json={"label": "Carnet SIC ampliado"}).status_code == 200
        assert bo.post("/admin/catalogs/document-types", json={"code": "ppt", "label": "x"}).status_code == 422
        assert bo.post("/admin/catalogs/document-types", json={"code": "CC", "label": "x"}).status_code == 409

    def test_a_retired_type_leaves_the_public_list_and_is_refused_for_new_sellers_and_staff(
            self, bo, client, client_as, eca):
        bo.patch("/admin/catalogs/document-types/TI", json={"is_active": False})
        assert "TI" not in {t["code"] for t in client.get("/catalogs/document-types").json()}
        seller = {"seller": {"full_name": "Ana", "id_type": "TI", "id_number": "1234567"}}
        r = _weigh(client_as, eca.operator, eca.wh, **seller)
        assert r.status_code == 422 and r.json()["code"] == "invalid_id_type"
        r = client_as(eca.admin).post("/users/invitations", json={
            "email": "nueva@test.com", "full_name": "Nueva Persona", "id_type": "TI", "id_number": "7777777",
            "phone": "3150000000", "role_code": "eca_operator"})
        assert r.status_code == 422 and r.json()["code"] == "invalid_id_type"

    def test_it_is_audited(self, bo, db):
        bo.post("/admin/catalogs/document-types", json={"code": "SIC", "label": "Carnet SIC"})
        bo.patch("/admin/catalogs/document-types/SIC", json={"is_active": False})
        assert len(_audit(db, "catalog.created", "SIC")) == 1 and len(_audit(db, "catalog.updated", "SIC")) == 1


class TestWarehouses:
    def test_rename_change_the_address_and_clear_it(self, bo, eca):
        url = f"/admin/warehouses/{eca.wh.id}"
        r = bo.patch(url, json={"name": "  Bodega Central  ", "address": "Calle 1 # 2-3"})
        assert r.status_code == 200 and r.json()["name"] == "Bodega Central" and r.json()["address"] == "Calle 1 # 2-3"
        assert bo.patch(url, json={"address": None}).json()["address"] is None
        assert bo.patch(url, json={}).status_code == 422

    def test_a_deactivated_warehouse_is_hidden_and_refused_for_new_work_but_keeps_its_history(
            self, bo, client_as, eca):
        before = _weigh(client_as, eca.operator, eca.wh).json()["id"]
        r = bo.patch(f"/admin/warehouses/{eca.wh.id}", json={"is_active": False})
        assert r.status_code == 200 and r.json()["is_active"] is False
        assert str(eca.wh.id) not in {w["id"] for w in client_as(eca.admin).get("/inventory/warehouses").json()}
        r = _weigh(client_as, eca.operator, eca.wh)
        assert r.status_code == 400 and r.json()["code"] == "warehouse_inactive"
        assert client_as(eca.admin).get(f"/weighings/{before}").status_code == 200

    def test_a_missing_warehouse_is_404(self, bo):
        assert bo.patch("/admin/warehouses/00000000-0000-0000-0000-000000000000", json={"name": "x"}).status_code == 404

    def test_it_is_audited(self, bo, db, eca):
        bo.patch(f"/admin/warehouses/{eca.wh.id}", json={"name": "Nueva"})
        (entry,) = _audit(db, "warehouse.updated", eca.wh.id)
        assert list(entry.details["changes"]) == ["name"]


class TestWhoMayUseIt:
    ENDPOINTS = [
        ("get", "/admin/catalogs/materials"), ("post", "/admin/catalogs/materials"),
        ("patch", "/admin/catalogs/materials/plastic"), ("get", "/admin/catalogs/document-types"),
        ("post", "/admin/catalogs/document-types"), ("patch", "/admin/catalogs/document-types/CC"),
        ("patch", "/admin/warehouses/{id}"),
    ]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_customers_and_anonymous_callers_are_refused(self, client_as, client, eca, method, path):
        url = path.replace("{id}", str(eca.wh.id))
        for user in (eca.admin, eca.operator):
            assert client_as(user).request(method.upper(), url, json={}).status_code == 401
        assert client.request(method.upper(), url, json={}).status_code in (401, 403)

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_the_capability_decides(self, bo, eca, monkeypatch, method, path):
        monkeypatch.setitem(permissions.PLATFORM_CAPABILITIES, "catalogs.manage", frozenset())
        r = bo.request(method.upper(), path.replace("{id}", str(eca.wh.id)), json={})
        assert r.status_code == 403 and r.json()["code"] == "forbidden"

    def test_the_network_restriction_applies(self, client, admin, monkeypatch):
        monkeypatch.setattr(settings, "admin_allowed_cidrs", "203.0.113.0/24")
        headers = factories.backoffice_headers(admin)
        assert TestClient(app, headers=headers, client=("192.0.2.1", 1)).get("/admin/catalogs/materials").status_code == 403
        assert TestClient(app, headers=headers, client=("203.0.113.5", 1)).get("/admin/catalogs/materials").status_code == 200

    def test_the_rate_limit_applies(self, bo, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_admin", "3/minute")
        limiter.reset()
        try:
            assert [bo.get("/admin/catalogs/materials").status_code for _ in range(5)] == [200, 200, 200, 429, 429]
        finally:
            limiter.reset()


def test_materials_in_the_database_are_what_the_catalog_reports(bo, db):
    assert {m["code"] for m in bo.get("/admin/catalogs/materials").json()} == {m.code for m in db.scalars(select(Material))}
