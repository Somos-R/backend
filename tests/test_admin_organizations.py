"""Organizations from the backoffice (6.19): find them, see their staff and their links. Read-only."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import permissions
from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.audit.models import AuditLog
from app.domains.inventory.models import Warehouse
from app.domains.organizations.enums import LinkStatus, OrganizationStatus
from app.domains.organizations.models import EcaAssociationLink
from app.main import app
from tests import factories


@pytest.fixture
def admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


@pytest.fixture
def bo(client, admin):
    return TestClient(app, headers=factories.backoffice_headers(admin))


@pytest.fixture
def w(db):
    eca = factories.make_organization(db, "eca", legal_name="ECA Ñandú", tax_id="900111222-1", city="Cali")
    other_eca = factories.make_organization(db, "eca", legal_name="ECA Beta", tax_id="900111333-1")
    assoc = factories.make_organization(db, "association", legal_name="Asociación Xenón", tax_id="900222333-1",
                                        city="Bogotá", legal_representative="Rep Legal")
    pending = factories.make_organization(db, "association", legal_name="Asociación Nueva", tax_id="900444555-1",
                                          status=OrganizationStatus.submitted)
    warehouse = db.query(Warehouse).order_by(Warehouse.name).first()
    warehouse.organization_id = eca.id
    db.add_all([
        EcaAssociationLink(eca_id=eca.id, association_id=assoc.id, status=LinkStatus.active),
        EcaAssociationLink(eca_id=other_eca.id, association_id=assoc.id, status=LinkStatus.requested),
    ])
    db.commit()
    return SimpleNamespace(
        eca=eca, other_eca=other_eca, assoc=assoc, pending=pending, warehouse=warehouse,
        eca_admin=factories.make_user(db, "eca", role_code="eca_admin", organization_id=eca.id, full_name="Ana Álvarez"),
        eca_op=factories.make_user(db, "eca", role_code="eca_operator", organization_id=eca.id, full_name="Beto Boyacá"),
        assoc_admin=factories.make_user(db, "association", role_code="association_admin", organization_id=assoc.id),
        recycler=factories.make_user(db, "recycler", organization_id=assoc.id),
    )


def _ids(response):
    assert response.status_code == 200, response.text
    return {i["id"] for i in response.json()["items"]}


class TestListing:
    def test_it_lists_every_organization_of_every_type_and_status(self, bo, w):
        ids = _ids(bo.get("/admin/organizations", params={"limit": 200}))
        assert {str(o.id) for o in (w.eca, w.other_eca, w.assoc, w.pending)} <= ids

    def test_each_row_says_how_many_people_and_active_links(self, bo, w):
        rows = {i["id"]: i for i in bo.get("/admin/organizations", params={"limit": 200}).json()["items"]}
        assert rows[str(w.eca.id)]["staff_count"] == 2 and rows[str(w.eca.id)]["active_links"] == 1
        # the association has one active link (not the requested one) and one admin; its recycler is not staff
        assert rows[str(w.assoc.id)]["staff_count"] == 1 and rows[str(w.assoc.id)]["active_links"] == 1
        assert rows[str(w.other_eca.id)]["active_links"] == 0
        assert rows[str(w.pending.id)]["staff_count"] == 0

    def test_filters_by_type_and_status(self, bo, w):
        assert str(w.eca.id) in _ids(bo.get("/admin/organizations", params={"type": "eca", "limit": 200}))
        assert str(w.assoc.id) not in _ids(bo.get("/admin/organizations", params={"type": "eca", "limit": 200}))
        submitted = _ids(bo.get("/admin/organizations", params={"status": "submitted", "limit": 200}))
        assert str(w.pending.id) in submitted and str(w.assoc.id) not in submitted

    @pytest.mark.parametrize("q", ["ñandú", "NANDU", "nandu", "900111222"])
    def test_the_search_ignores_case_and_accents_and_looks_at_name_and_tax_id(self, bo, w, q):
        assert _ids(bo.get("/admin/organizations", params={"q": q, "limit": 200})) == {str(w.eca.id)}

    def test_the_search_also_covers_the_representative(self, bo, w):
        assert _ids(bo.get("/admin/organizations", params={"q": "rep legal"})) == {str(w.assoc.id)}

    def test_wildcards_are_plain_text_and_a_short_query_is_ignored(self, bo, w):
        assert bo.get("/admin/organizations", params={"q": "%%"}).json()["total"] == 0
        assert bo.get("/admin/organizations", params={"q": "a", "limit": 200}).json()["total"] >= 4

    def test_pagination_is_stable(self, bo, w):
        first = bo.get("/admin/organizations", params={"limit": 2, "offset": 0}).json()
        second = bo.get("/admin/organizations", params={"limit": 2, "offset": 2}).json()
        assert first["total"] == second["total"] >= 4
        assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


class TestDetail:
    def test_an_eca_shows_its_staff_links_and_warehouses(self, bo, w):
        body = bo.get(f"/admin/organizations/{w.eca.id}").json()
        assert body["legal_name"] == "ECA Ñandú" and body["tax_id"] == "900111222-1" and body["city"] == "Cali"
        assert {s["id"] for s in body["staff"]} == {str(w.eca_admin.id), str(w.eca_op.id)}
        assert [(l["status"], l["other"]["legal_name"], l["other"]["type"]) for l in body["links"]] == [
            ("active", "Asociación Xenón", "association")]
        assert str(w.warehouse.id) in {x["id"] for x in body["warehouses"]}
        assert body["recyclers_count"] is None and body["active_links"] == 1

    def test_an_association_shows_its_eca_links_and_its_recyclers_count(self, bo, w):
        body = bo.get(f"/admin/organizations/{w.assoc.id}").json()
        assert {(l["status"], l["other"]["legal_name"]) for l in body["links"]} == {
            ("active", "ECA Ñandú"), ("requested", "ECA Beta")}
        assert body["recyclers_count"] == 1 and body["warehouses"] is None and body["active_links"] == 1
        assert {s["id"] for s in body["staff"]} == {str(w.assoc_admin.id)}  # the recycler is not staff

    def test_staff_rows_carry_no_secrets(self, bo, w):
        text = bo.get(f"/admin/organizations/{w.eca.id}").text
        assert "password" not in text and "token" not in text

    def test_a_missing_organization_is_404(self, bo):
        r = bo.get("/admin/organizations/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404 and r.json()["code"] == "organization_not_found"

    def test_opening_it_is_audited_with_who_and_which(self, bo, db, admin, w):
        bo.get(f"/admin/organizations/{w.eca.id}")
        (entry,) = db.scalars(select(AuditLog).where(
            AuditLog.action == "admin.organization_viewed", AuditLog.target_id == str(w.eca.id))).all()
        assert entry.actor_id == admin.id

    def test_listing_is_not_audited(self, bo, db, w):
        bo.get("/admin/organizations")
        assert db.scalars(select(AuditLog).where(AuditLog.action == "admin.organization_viewed")).all() == []


class TestWhoMayUseIt:
    ENDPOINTS = [("get", "/admin/organizations"), ("get", "/admin/organizations/{id}")]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_customers_and_anonymous_callers_are_refused(self, client_as, client, w, method, path):
        url = path.replace("{id}", str(w.eca.id))
        for user in (w.eca_admin, w.assoc_admin):
            assert client_as(user).request(method.upper(), url).status_code == 401
        assert client.request(method.upper(), url).status_code in (401, 403)

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_the_capability_decides(self, bo, w, monkeypatch, method, path):
        monkeypatch.setitem(permissions.PLATFORM_CAPABILITIES, "organizations.review", frozenset())
        r = bo.request(method.upper(), path.replace("{id}", str(w.eca.id)))
        assert r.status_code == 403 and r.json()["code"] == "forbidden"

    def test_a_platform_users_portal_token_is_not_enough(self, client_as, admin):
        assert client_as(admin).get("/admin/organizations").status_code == 401

    def test_the_network_restriction_applies(self, client, admin, monkeypatch):
        monkeypatch.setattr(settings, "admin_allowed_cidrs", "203.0.113.0/24")
        headers = factories.backoffice_headers(admin)
        assert TestClient(app, headers=headers, client=("192.0.2.1", 1)).get("/admin/organizations").status_code == 403
        assert TestClient(app, headers=headers, client=("203.0.113.5", 1)).get("/admin/organizations").status_code == 200

    def test_the_rate_limit_applies(self, bo, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_admin", "3/minute")
        limiter.reset()
        try:
            assert [bo.get("/admin/organizations").status_code for _ in range(5)] == [200, 200, 200, 429, 429]
        finally:
            limiter.reset()
