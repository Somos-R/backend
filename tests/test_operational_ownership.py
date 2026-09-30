"""Warehouses, inventory, weighings and transactions belong to the ECA that owns the warehouse.

Two ECAs (A, B) and two associations (X, Y). A is linked to X, B is linked to Y. Recycler r1 belongs to X
and r2 to Y.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import permissions
from app.domains.audit.models import AuditLog
from app.domains.inventory.models import Warehouse
from app.domains.organizations.enums import LinkStatus, OrganizationStatus
from app.domains.organizations.models import EcaAssociationLink
from app.main import app
from tests import factories

MISSING = "00000000-0000-0000-0000-000000000000"


def _party(db, org_type, name):
    org = factories.make_organization(db, org_type, legal_name=name)
    if org_type == "eca":
        roles = ("eca_admin", "eca_operator", "eca_warehouse")
    else:
        roles = ("association_admin", "association_operator", "route_manager")
    return SimpleNamespace(
        org=org, **{
            key: factories.make_user(db, org_type, role_code=role, organization_id=org.id)
            for key, role in zip(("admin", "operator", "extra"), roles)})


def _link(db, eca, assoc, status=LinkStatus.active):
    link = EcaAssociationLink(eca_id=eca.org.id, association_id=assoc.org.id, status=status)
    db.add(link)
    db.commit()
    return link


@pytest.fixture
def w(db):
    eca_a, eca_b = _party(db, "eca", "ECA Alfa"), _party(db, "eca", "ECA Beta")
    x, y = _party(db, "association", "Asociación Xenón"), _party(db, "association", "Asociación Yodo")
    wh = db.query(Warehouse).order_by(Warehouse.name).all()
    wh[0].organization_id, wh[1].organization_id = eca_a.org.id, eca_b.org.id
    wh[2].organization_id = None  # a warehouse nobody owns yet
    db.commit()
    return SimpleNamespace(
        a=eca_a, b=eca_b, x=x, y=y, wa=wh[0], wb=wh[1], orphan_wh=wh[2],
        link_ax=_link(db, eca_a, x), link_by=_link(db, eca_b, y),
        r1=factories.make_user(db, "recycler", organization_id=x.org.id),
        r2=factories.make_user(db, "recycler", organization_id=y.org.id),
    )


def _weigh(client_as, eca, recycler, warehouse, kg="10"):
    return client_as(eca.operator).post("/weighings", json={
        "recycler_id": str(recycler.id), "material_code": "plastic", "warehouse_id": str(warehouse.id),
        "kg": kg, "price_per_kg": "400"})


def _weighing(client_as, eca, recycler, warehouse):
    r = _weigh(client_as, eca, recycler, warehouse)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ids(response):
    assert response.status_code == 200, response.text
    body = response.json()
    return {i["id"] for i in (body["items"] if isinstance(body, dict) else body)}


class TestWarehouses:
    def test_an_eca_sees_only_its_own(self, client_as, w):
        assert _ids(client_as(w.a.admin).get("/inventory/warehouses")) == {str(w.wa.id)}
        assert _ids(client_as(w.b.admin).get("/inventory/warehouses")) == {str(w.wb.id)}

    def test_an_association_sees_the_warehouses_of_the_ecas_it_is_linked_to(self, client_as, w):
        assert _ids(client_as(w.x.admin).get("/inventory/warehouses")) == {str(w.wa.id)}
        assert _ids(client_as(w.y.operator).get("/inventory/warehouses")) == {str(w.wb.id)}

    def test_a_warehouse_nobody_owns_is_invisible_to_everyone(self, client_as, w):
        for user in (w.a.admin, w.b.admin, w.x.admin, w.y.admin):
            assert str(w.orphan_wh.id) not in _ids(client_as(user).get("/inventory/warehouses"))

    def test_an_unlinked_association_sees_none(self, client_as, db, w):
        z = _party(db, "association", "Asociación Zeta")
        assert _ids(client_as(z.admin).get("/inventory/warehouses")) == set()

    def test_ending_a_link_takes_the_view_away(self, client_as, db, w):
        w.link_ax.status = LinkStatus.removed
        db.commit()
        assert _ids(client_as(w.x.admin).get("/inventory/warehouses")) == set()

    def test_a_link_that_is_only_requested_gives_nothing(self, client_as, db, w):
        z = _party(db, "association", "Asociación Zeta")
        _link(db, w.a, z, LinkStatus.requested)
        assert _ids(client_as(z.admin).get("/inventory/warehouses")) == set()

    def test_staff_without_an_organization_see_nothing(self, client_as, db, w):
        orphan = factories.make_user(db, "eca", role_code="eca_admin", organization_id=None)
        assert _ids(client_as(orphan).get("/inventory/warehouses")) == set()
        assert client_as(orphan).get("/inventory").json()["total"] == 0


class TestCreatingAWarehouse:
    def test_an_eca_admin_creates_one_of_its_own(self, client_as, db, w):
        r = client_as(w.a.admin).post("/inventory/warehouses", json={"name": "Bodega Norte", "address": "Calle 1"})
        assert r.status_code == 201, r.text
        created = r.json()["id"]
        assert db.get(Warehouse, created).organization_id == w.a.org.id
        assert created in _ids(client_as(w.a.operator).get("/inventory/warehouses"))
        assert created not in _ids(client_as(w.b.admin).get("/inventory/warehouses"))
        assert created in _ids(client_as(w.x.admin).get("/inventory/warehouses"))  # linked association reads it

    def test_it_is_audited(self, client_as, db, w):
        wid = client_as(w.a.admin).post("/inventory/warehouses", json={"name": "Bodega Norte"}).json()["id"]
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "warehouse.created")).one()
        assert entry.target_id == wid and entry.actor_id == w.a.admin.id

    @pytest.mark.parametrize("who", ["a.operator", "a.extra", "x.admin", "x.operator"])
    def test_only_an_eca_admin_may(self, client_as, w, who):
        group, attr = who.split(".")
        assert client_as(getattr(getattr(w, group), attr)).post(
            "/inventory/warehouses", json={"name": "Bodega X"}).status_code == 403

    def test_the_eca_must_be_approved_and_the_admin_must_have_one(self, client_as, db, w):
        pending = factories.make_organization(db, "eca", status=OrganizationStatus.submitted)
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=pending.id)
        assert client_as(admin).post("/inventory/warehouses", json={"name": "Bodega X"}).json()["code"] == "organization_not_active"
        orphan = factories.make_user(db, "eca", role_code="eca_admin", organization_id=None)
        assert client_as(orphan).post("/inventory/warehouses", json={"name": "Bodega X"}).json()["code"] == "no_organization"

    @pytest.mark.parametrize("bad", [{"name": "A"}, {"name": ""}, {"name": "x" * 101}, {}])
    def test_validation(self, client_as, w, bad):
        assert client_as(w.a.admin).post("/inventory/warehouses", json=bad).status_code == 422


class TestInventory:
    def _stock(self, db, w):
        factories.stock(db, w.wa, "plastic", kg="100", price_per_kg="500")
        factories.stock(db, w.wb, "plastic", kg="7", price_per_kg="100")

    def test_lists_are_scoped_and_totals_exact(self, client_as, db, w):
        self._stock(db, w)
        body = client_as(w.a.admin).get("/inventory").json()
        assert body["total"] == 1 and body["items"][0]["warehouse"]["id"] == str(w.wa.id)
        assert client_as(w.x.admin).get("/inventory").json()["total"] == 1  # linked to A
        assert client_as(w.y.admin).get("/inventory").json()["items"][0]["warehouse"]["id"] == str(w.wb.id)

    def test_filtering_by_another_ecas_warehouse_finds_nothing(self, client_as, db, w):
        self._stock(db, w)
        assert client_as(w.a.admin).get("/inventory", params={"warehouse_id": str(w.wb.id)}).json()["total"] == 0

    def test_statistics_count_only_what_is_visible(self, client_as, db, w):
        self._stock(db, w)
        a = client_as(w.a.admin).get("/inventory/stats").json()
        b = client_as(w.b.admin).get("/inventory/stats").json()
        assert float(a["total_stock_kg"]) == 100 and float(b["total_stock_kg"]) == 7
        assert float(a["total_value"]) == 50000 and float(b["total_value"]) == 700

    def test_another_ecas_item_looks_missing(self, client_as, db, w):
        self._stock(db, w)
        item_b = client_as(w.b.admin).get("/inventory").json()["items"][0]["id"]
        r = client_as(w.a.admin).get(f"/inventory/{item_b}")
        missing = client_as(w.a.admin).get(f"/inventory/{MISSING}")
        assert r.status_code == 404 and r.json() == missing.json()

    def test_another_ecas_item_cannot_be_repriced(self, client_as, db, w):
        self._stock(db, w)
        item_b = client_as(w.b.admin).get("/inventory").json()["items"][0]
        r = client_as(w.a.admin).patch(f"/inventory/{item_b['id']}", json={"price_per_kg": "9999"})
        assert r.status_code == 404
        assert client_as(w.b.admin).get(f"/inventory/{item_b['id']}").json()["price_per_kg"] == item_b["price_per_kg"]

    def test_a_linked_association_reads_but_never_writes(self, client_as, db, w):
        self._stock(db, w)
        item_a = client_as(w.a.admin).get("/inventory").json()["items"][0]["id"]
        assert client_as(w.x.admin).get(f"/inventory/{item_a}").status_code == 200
        assert client_as(w.x.admin).patch(f"/inventory/{item_a}", json={"price_per_kg": "1"}).status_code == 403

    def test_an_unlinked_association_gets_a_404(self, client_as, db, w):
        self._stock(db, w)
        item_a = client_as(w.a.admin).get("/inventory").json()["items"][0]["id"]
        assert client_as(w.y.admin).get(f"/inventory/{item_a}").status_code == 404

    def test_an_eca_still_edits_its_own(self, client_as, db, w):
        self._stock(db, w)
        item_a = client_as(w.a.admin).get("/inventory").json()["items"][0]["id"]
        assert client_as(w.a.admin).patch(f"/inventory/{item_a}", json={"price_per_kg": "600"}).status_code == 200


class TestWeighing:
    def test_an_eca_weighs_a_recycler_of_a_linked_association_in_its_own_warehouse(self, client_as, w):
        assert _weigh(client_as, w.a, w.r1, w.wa).status_code == 201
        assert _weigh(client_as, w.b, w.r2, w.wb).status_code == 201

    def test_not_in_another_ecas_warehouse(self, client_as, w):
        r = _weigh(client_as, w.a, w.r1, w.wb)
        assert r.status_code == 404 and r.json()["code"] == "warehouse_not_found"

    def test_not_in_a_warehouse_nobody_owns(self, client_as, w):
        assert _weigh(client_as, w.a, w.r1, w.orphan_wh).json()["code"] == "warehouse_not_found"

    def test_a_recycler_of_an_association_that_is_not_linked_is_received_too(self, client_as, w):
        r = _weigh(client_as, w.a, w.r2, w.wa)
        assert r.status_code == 201 and r.json()["affiliation_status"] == "unlinked_association"

    def test_a_recycler_without_an_association_is_received_as_independent(self, client_as, db, w):
        loose = factories.make_user(db, "recycler", organization_id=None)
        r = _weigh(client_as, w.a, loose, w.wa)
        assert r.status_code == 201 and r.json()["affiliation_status"] == "independent"

    def test_the_affiliation_follows_the_link_at_that_moment(self, client_as, db, w):
        assert _weigh(client_as, w.a, w.r1, w.wa).json()["affiliation_status"] == "linked"
        w.link_ax.status = LinkStatus.removed
        db.commit()
        assert _weigh(client_as, w.a, w.r1, w.wa).json()["affiliation_status"] == "unlinked_association"
        w.link_ax.status = LinkStatus.requested
        db.commit()
        assert _weigh(client_as, w.a, w.r1, w.wa).json()["affiliation_status"] == "unlinked_association"
        w.link_ax.status = LinkStatus.active
        db.commit()
        assert _weigh(client_as, w.a, w.r1, w.wa).json()["affiliation_status"] == "linked"

    def test_a_refused_weighing_leaves_nothing_behind(self, client_as, db, w):
        from app.domains.weighings.models import Weighing

        _weigh(client_as, w.a, w.r1, w.wb)  # another ECA's warehouse
        assert db.scalars(select(Weighing)).all() == []

    def test_an_unlinked_recycler_is_not_browsable_in_the_user_directory_but_can_be_looked_up(self, client_as, w):
        assert client_as(w.a.admin).get(f"/users/{w.r2.id}").status_code == 404
        assert client_as(w.a.admin).get(f"/users/{w.r1.id}").status_code == 200
        found = client_as(w.a.admin).get("/recyclers/lookup", params={"document": w.r2.id_number})
        assert found.status_code == 200 and found.json()["affiliation"] == "unlinked_association"


class TestReadingWeighings:
    @pytest.fixture
    def two(self, client_as, w):
        return SimpleNamespace(a=_weighing(client_as, w.a, w.r1, w.wa), b=_weighing(client_as, w.b, w.r2, w.wb))

    def test_an_eca_sees_only_what_happened_in_its_warehouses(self, client_as, w, two):
        assert _ids(client_as(w.a.admin).get("/weighings")) == {two.a}
        assert _ids(client_as(w.b.operator).get("/weighings")) == {two.b}

    def test_an_association_sees_the_weighings_of_its_own_recyclers(self, client_as, w, two):
        assert _ids(client_as(w.x.admin).get("/weighings")) == {two.a}
        assert _ids(client_as(w.y.operator).get("/weighings")) == {two.b}

    def test_its_recyclers_weighings_stay_visible_after_the_link_ends(self, client_as, db, w, two):
        w.link_ax.status = LinkStatus.removed
        db.commit()
        assert _ids(client_as(w.x.admin).get("/weighings")) == {two.a}
        assert _ids(client_as(w.a.admin).get("/weighings")) == {two.a}  # history stays with the warehouse

    def test_the_recycler_sees_their_own_as_before(self, client_as, w, two):
        assert _ids(client_as(w.r1).get("/weighings")) == {two.a}

    def test_the_total_and_the_filters_cannot_widen_the_view(self, client_as, w, two):
        c = client_as(w.a.admin)
        assert c.get("/weighings", params={"limit": 1}).json()["total"] == 1
        assert c.get("/weighings", params={"recycler_id": str(w.r2.id)}).json()["total"] == 0
        assert c.get("/weighings", params={"warehouse_id": str(w.wb.id)}).json()["total"] == 0

    def test_someone_elses_weighing_looks_missing(self, client_as, w, two):
        for user in (w.b.admin, w.y.admin, w.y.operator):
            other = client_as(user).get(f"/weighings/{two.a}")
            missing = client_as(user).get(f"/weighings/{MISSING}")
            assert other.status_code == 404 and other.json() == missing.json()

    def test_statistics_count_only_what_is_visible(self, client_as, w, two):
        _weighing(client_as, w.a, w.r1, w.wa)
        assert client_as(w.a.admin).get("/weighings/stats").json()["total_weighings_month"] == 2
        assert client_as(w.b.admin).get("/weighings/stats").json()["total_weighings_month"] == 1
        assert client_as(w.x.admin).get("/weighings/stats").json()["pending_count"] == 2
        assert client_as(w.y.admin).get("/weighings/stats").json()["pending_count"] == 1

    def test_staff_without_an_organization_see_nothing(self, client_as, db, w, two):
        orphan = factories.make_user(db, "eca", role_code="eca_operator", organization_id=None)
        assert client_as(orphan).get("/weighings").json()["total"] == 0
        assert client_as(orphan).get("/weighings/stats").json()["total_weighings_month"] == 0


class TestChangingAWeighing:
    def test_another_eca_cannot_validate_reject_or_pay_it(self, client_as, db, w):
        wid = _weighing(client_as, w.a, w.r1, w.wa)
        for body in ({"status": "validated"}, {"status": "rejected", "rejection_reason": "x"}, {"status": "paid"}):
            r = client_as(w.b.admin).patch(f"/weighings/{wid}/status", json=body)
            assert r.status_code == 404 and r.json()["code"] == "weighing_not_found"
        assert client_as(w.a.admin).get(f"/weighings/{wid}").json()["status"] == "pending_validation"

    def test_another_association_cannot_either(self, client_as, w):
        wid = _weighing(client_as, w.a, w.r1, w.wa)
        assert client_as(w.y.admin).patch(f"/weighings/{wid}/status", json={"status": "validated"}).status_code == 404

    def test_its_recyclers_association_and_its_eca_can(self, client_as, w):
        first, second = _weighing(client_as, w.a, w.r1, w.wa), _weighing(client_as, w.a, w.r1, w.wa)
        assert client_as(w.x.operator).patch(f"/weighings/{first}/status", json={"status": "validated"}).status_code == 200
        assert client_as(w.a.operator).patch(f"/weighings/{second}/status", json={"status": "validated"}).status_code == 200

    def test_validating_feeds_the_ecas_own_inventory_and_only_that(self, client_as, w):
        wid = _weighing(client_as, w.a, w.r1, w.wa)
        client_as(w.a.admin).patch(f"/weighings/{wid}/status", json={"status": "validated"})
        assert client_as(w.a.admin).get("/inventory").json()["total"] == 1
        assert client_as(w.b.admin).get("/inventory").json()["total"] == 0


class TestTransactions:
    @pytest.fixture
    def purchases(self, client_as, w):
        ids = SimpleNamespace(a=_weighing(client_as, w.a, w.r1, w.wa), b=_weighing(client_as, w.b, w.r2, w.wb))
        client_as(w.a.admin).patch(f"/weighings/{ids.a}/status", json={"status": "validated"})
        client_as(w.b.admin).patch(f"/weighings/{ids.b}/status", json={"status": "validated"})
        return ids

    def _tx(self, client_as, user):
        return {t["type"] + ":" + t["warehouse"]["id"]: t["id"] for t in
                client_as(user).get("/transactions", params={"limit": 100}).json()["items"]}

    def test_an_eca_sees_its_own_and_an_association_its_recyclers_purchases(self, client_as, w, purchases):
        assert len(client_as(w.a.admin).get("/transactions").json()["items"]) == 1
        assert len(client_as(w.b.admin).get("/transactions").json()["items"]) == 1
        x = client_as(w.x.admin).get("/transactions").json()["items"]
        assert len(x) == 1 and x[0]["warehouse"]["id"] == str(w.wa.id)

    def test_an_association_never_sees_sales(self, client_as, db, w, purchases):
        factories.stock(db, w.wa, "plastic", kg="500")
        assert client_as(w.a.admin).post("/transactions", json={
            "material_code": "plastic", "warehouse_id": str(w.wa.id), "kg": "10", "price_per_kg": "700"}).status_code == 201
        assert len(client_as(w.a.admin).get("/transactions").json()["items"]) == 2
        assert all(t["type"] == "purchase" for t in client_as(w.x.admin).get("/transactions").json()["items"])

    def test_a_sale_only_leaves_an_own_warehouse(self, client_as, db, w):
        factories.stock(db, w.wb, "plastic", kg="500")
        r = client_as(w.a.admin).post("/transactions", json={
            "material_code": "plastic", "warehouse_id": str(w.wb.id), "kg": "10", "price_per_kg": "700"})
        assert r.status_code == 404 and r.json()["code"] == "warehouse_not_found"
        r = client_as(w.a.admin).post("/transactions", json={
            "material_code": "plastic", "warehouse_id": str(w.orphan_wh.id), "kg": "10", "price_per_kg": "700"})
        assert r.status_code == 404

    def test_statistics_are_scoped(self, client_as, w, purchases):
        assert client_as(w.a.admin).get("/transactions/stats").json()["total_purchases_month"] == 1
        assert client_as(w.x.admin).get("/transactions/stats").json()["total_purchases_month"] == 1
        assert client_as(w.y.admin).get("/transactions/stats").json()["total_purchases_month"] == 1

    def test_another_organizations_transaction_looks_missing(self, client_as, w, purchases):
        tx_a = next(iter(self._tx(client_as, w.a.admin).values()))
        for user in (w.b.admin, w.y.admin):
            r = client_as(user).get(f"/transactions/{tx_a}")
            assert r.status_code == 404 and r.json() == client_as(user).get(f"/transactions/{MISSING}").json()
        assert client_as(w.b.admin).patch(f"/transactions/{tx_a}/status", json={"status": "paid"}).status_code == 404
        assert client_as(w.y.admin).patch(f"/transactions/{tx_a}/status", json={"status": "paid"}).status_code == 404

    def test_the_recyclers_association_may_pay_its_purchase_and_the_eca_too(self, client_as, w, purchases):
        tx_a = next(iter(self._tx(client_as, w.a.admin).values()))
        assert client_as(w.x.admin).patch(f"/transactions/{tx_a}/status", json={"status": "paid"}).status_code == 200

    def test_cancelling_a_sale_of_another_eca_is_refused(self, client_as, db, w):
        factories.stock(db, w.wb, "plastic", kg="500")
        tx = client_as(w.b.admin).post("/transactions", json={
            "material_code": "plastic", "warehouse_id": str(w.wb.id), "kg": "10", "price_per_kg": "700"}).json()["id"]
        assert client_as(w.a.admin).patch(f"/transactions/{tx}/status", json={"status": "cancelled"}).status_code == 404
        assert client_as(w.b.admin).patch(f"/transactions/{tx}/status", json={"status": "cancelled"}).status_code == 200


class TestBackofficeAssignsOwners:
    @pytest.fixture
    def bo(self, client, db):
        platform = factories.make_user(db, "platform", role_code="platform_admin")
        return TestClient(app, headers=factories.backoffice_headers(platform))

    def test_it_lists_every_warehouse_and_finds_the_ones_without_an_owner(self, bo, w):
        everything = bo.get("/admin/warehouses", params={"limit": 200}).json()
        assert {i["id"] for i in everything["items"]} >= {str(w.wa.id), str(w.wb.id), str(w.orphan_wh.id)}
        loose = bo.get("/admin/warehouses", params={"unassigned": "true"}).json()
        assert [i["id"] for i in loose["items"]] == [str(w.orphan_wh.id)]
        mine = bo.get("/admin/warehouses", params={"organization_id": str(w.a.org.id)}).json()
        assert [i["id"] for i in mine["items"]] == [str(w.wa.id)]

    def test_giving_it_an_owner_makes_it_visible_to_that_eca_and_its_linked_associations(self, bo, client_as, w):
        r = bo.put(f"/admin/warehouses/{w.orphan_wh.id}/organization", json={"organization_id": str(w.a.org.id)})
        assert r.status_code == 200 and r.json()["organization_id"] == str(w.a.org.id)
        assert str(w.orphan_wh.id) in _ids(client_as(w.a.admin).get("/inventory/warehouses"))
        assert str(w.orphan_wh.id) in _ids(client_as(w.x.admin).get("/inventory/warehouses"))
        assert str(w.orphan_wh.id) not in _ids(client_as(w.b.admin).get("/inventory/warehouses"))

    def test_an_owner_cannot_be_changed(self, bo, w):
        r = bo.put(f"/admin/warehouses/{w.wa.id}/organization", json={"organization_id": str(w.b.org.id)})
        assert r.status_code == 409 and r.json()["code"] == "already_owned"

    def test_the_owner_must_be_an_approved_eca(self, bo, db, w):
        assert bo.put(f"/admin/warehouses/{w.orphan_wh.id}/organization",
                      json={"organization_id": str(w.x.org.id)}).json()["code"] == "organization_type_mismatch"
        pending = factories.make_organization(db, "eca", status=OrganizationStatus.submitted)
        assert bo.put(f"/admin/warehouses/{w.orphan_wh.id}/organization",
                      json={"organization_id": str(pending.id)}).json()["code"] == "organization_not_active"
        assert bo.put(f"/admin/warehouses/{w.orphan_wh.id}/organization",
                      json={"organization_id": MISSING}).json()["code"] == "organization_not_found"
        assert bo.put(f"/admin/warehouses/{MISSING}/organization",
                      json={"organization_id": str(w.a.org.id)}).json()["code"] == "warehouse_not_found"

    def test_it_is_audited(self, bo, db, w):
        bo.put(f"/admin/warehouses/{w.orphan_wh.id}/organization", json={"organization_id": str(w.a.org.id)})
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "warehouse.organization_assigned")).one()
        assert entry.target_id == str(w.orphan_wh.id) and entry.details == {"organization_id": str(w.a.org.id)}

    def test_customers_cannot_use_it(self, client_as, w):
        for user in (w.a.admin, w.x.admin):
            assert client_as(user).get("/admin/warehouses").status_code == 401

    def test_the_capability_decides(self, bo, w, monkeypatch):
        monkeypatch.setitem(permissions.PLATFORM_CAPABILITIES, "catalogs.manage", frozenset())
        assert bo.get("/admin/warehouses").status_code == 403
