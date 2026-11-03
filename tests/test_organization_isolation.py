"""One organization never sees, edits or reads the trail of another's people.

Two ECAs (A, B) and two Associations (X, Y), each with an admin and an operator, plus people who are
not tied to an organization yet (recyclers, a citizen).
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domains.audit.models import AuditLog
from tests import factories

MISSING = "00000000-0000-0000-0000-000000000000"


def assoc_x_org_id(db):
    """The organization id the world's first association will have: built here, reused by the fixture."""
    return db.info.setdefault("assoc_x_org", factories.make_organization(
        db, "association", legal_name="Asociación Xenón").id)


@pytest.fixture
def world(db):
    def org_staff(org_type, tag, existing=None):
        from app.domains.organizations.models import Organization

        org = db.get(Organization, existing) if existing else factories.make_organization(db, org_type)
        prefix = org_type
        admin = factories.make_user(
            db, org_type, role_code=f"{prefix}_admin", organization_id=org.id, full_name=f"Admin {tag}")
        operator_role = "eca_operator" if org_type == "eca" else "association_operator"
        operator = factories.make_user(
            db, org_type, role_code=operator_role, organization_id=org.id, full_name=f"Operador {tag}")
        return SimpleNamespace(org=org, admin=admin, operator=operator)

    world = SimpleNamespace(
        eca_a=org_staff("eca", "Alfa"), eca_b=org_staff("eca", "Beta"),
        assoc_x=org_staff("association", "Xenón", existing=assoc_x_org_id(db)), assoc_y=org_staff("association", "Yodo"),
        recycler=factories.make_user(
            db, "recycler", full_name="Reciclador Común", organization_id=assoc_x_org_id(db)),
        citizen=factories.make_user(db, "citizen", full_name="Ciudadana Común"),
    )
    # ECA Alfa receives from Asociación Xenón: its recyclers are Alfa's to see and weigh.
    from app.domains.organizations.enums import LinkStatus
    from app.domains.organizations.models import EcaAssociationLink

    db.add(EcaAssociationLink(eca_id=world.eca_a.org.id, association_id=world.assoc_x.org.id, status=LinkStatus.active))
    db.commit()
    return world


def _ids(response):
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _staff_ids(*groups):
    return {str(u.id) for g in groups for u in (g.admin, g.operator)}


class TestListing:
    def test_an_eca_admin_lists_only_their_own_eca_staff(self, client_as, world):
        got = _ids(client_as(world.eca_a.admin).get("/users", params={"user_type_code": "eca"}))
        assert got == _staff_ids(world.eca_a)

    def test_without_a_type_filter_it_is_their_staff_plus_recyclers(self, client_as, world):
        got = _ids(client_as(world.eca_a.admin).get("/users", params={"limit": 500}))
        assert _staff_ids(world.eca_a) <= got and str(world.recycler.id) in got
        assert not got & _staff_ids(world.eca_b, world.assoc_x, world.assoc_y)

    def test_an_association_admin_never_sees_eca_staff_nor_another_associations(self, client_as, world):
        got = _ids(client_as(world.assoc_x.admin).get("/users", params={"limit": 500}))
        assert _staff_ids(world.assoc_x) <= got
        assert not got & _staff_ids(world.assoc_y, world.eca_a, world.eca_b)

    def test_the_association_still_sees_its_recyclers_and_the_other_people_it_saw_before(self, client_as, world):
        got = _ids(client_as(world.assoc_x.admin).get("/users", params={"limit": 500}))
        assert {str(world.recycler.id), str(world.citizen.id)} <= got

    def test_asking_for_another_organizations_type_gives_an_empty_page_not_their_people(self, client_as, world):
        r = client_as(world.assoc_x.admin).get("/users", params={"user_type_code": "eca"})
        assert r.status_code == 200 and r.json()["total"] == 0 and r.json()["items"] == []

    def test_the_total_counts_only_what_is_in_scope(self, client_as, world):
        body = client_as(world.eca_a.admin).get("/users", params={"user_type_code": "eca", "limit": 1}).json()
        assert body["total"] == 2 and len(body["items"]) == 1

    def test_text_search_cannot_reach_another_organizations_people(self, client_as, world):
        c = client_as(world.eca_a.admin)
        assert _ids(c.get("/users", params={"q": "Beta"})) == set()  # Admin Beta / Operador Beta
        assert len(_ids(c.get("/users", params={"q": "Alfa"}))) == 2

    def test_staff_who_only_look_up_recyclers_are_unaffected(self, client_as, world):
        got = _ids(client_as(world.eca_a.operator).get("/users", params={"limit": 500}))
        assert str(world.recycler.id) in got and not got & _staff_ids(world.eca_a, world.eca_b)


class TestReadingOne:
    def test_own_organization_and_self(self, client_as, world):
        assert client_as(world.eca_a.admin).get(f"/users/{world.eca_a.operator.id}").status_code == 200
        assert client_as(world.eca_a.operator).get(f"/users/{world.eca_a.operator.id}").status_code == 200

    def test_another_organizations_staff_looks_like_a_missing_user(self, client_as, world):
        c = client_as(world.eca_a.admin)
        other = c.get(f"/users/{world.eca_b.operator.id}")
        missing = c.get(f"/users/{MISSING}")
        assert other.status_code == 404 and other.json() == missing.json()
        assert other.json()["code"] == "user_not_found"

    def test_the_same_between_two_associations(self, client_as, world):
        r = client_as(world.assoc_x.admin).get(f"/users/{world.assoc_y.admin.id}")
        assert r.status_code == 404 and r.json()["code"] == "user_not_found"

    def test_an_association_admin_asking_for_eca_staff_is_refused_by_type(self, client_as, world):
        assert client_as(world.assoc_x.admin).get(f"/users/{world.eca_a.admin.id}").status_code in (403, 404)

    def test_a_recycler_is_readable_by_its_association_and_by_eca_staff_but_not_by_another_association(
        self, client_as, world
    ):
        for staff in (world.eca_a.operator, world.assoc_x.operator, world.assoc_x.admin):
            assert client_as(staff).get(f"/users/{world.recycler.id}").status_code == 200
        r = client_as(world.assoc_y.admin).get(f"/users/{world.recycler.id}")
        assert r.status_code == 404 and r.json()["code"] == "user_not_found"

    def test_somos_r_accounts_are_invisible_to_customers(self, client_as, world, db):
        platform = factories.make_user(db, "platform", role_code="platform_admin")
        assert client_as(world.assoc_x.admin).get(f"/users/{platform.id}").status_code == 403
        assert str(platform.id) not in _ids(client_as(world.assoc_x.admin).get("/users", params={"limit": 500}))


class TestEditing:
    def test_an_admin_edits_and_assigns_roles_within_their_organization(self, client_as, world, db):
        r = client_as(world.eca_a.admin).patch(
            f"/users/{world.eca_a.operator.id}", json={"phone": "3111111111", "role_code": "eca_warehouse"})
        assert r.status_code == 200
        db.refresh(world.eca_a.operator)
        assert world.eca_a.operator.role_code == "eca_warehouse"

    def test_another_organizations_staff_cannot_be_edited(self, client_as, world, db):
        before = world.eca_b.operator.phone
        r = client_as(world.eca_a.admin).patch(f"/users/{world.eca_b.operator.id}", json={"phone": "3999999999"})
        assert r.status_code == 404 and r.json()["code"] == "user_not_found"
        db.refresh(world.eca_b.operator)
        assert world.eca_b.operator.phone == before

    def test_nor_can_their_role_be_changed(self, client_as, world, db):
        r = client_as(world.eca_a.admin).patch(
            f"/users/{world.eca_b.operator.id}", json={"role_code": "eca_admin"})
        assert r.status_code == 404
        db.refresh(world.eca_b.operator)
        assert world.eca_b.operator.role_code == "eca_operator"

    def test_the_same_between_two_associations(self, client_as, world, db):
        r = client_as(world.assoc_x.admin).patch(
            f"/users/{world.assoc_y.operator.id}", json={"role_code": "association_admin"})
        assert r.status_code == 404
        db.refresh(world.assoc_y.operator)
        assert world.assoc_y.operator.role_code == "association_operator"

    def test_an_association_admin_still_manages_recyclers(self, client_as, world):
        r = client_as(world.assoc_x.admin).patch(f"/users/{world.recycler.id}", json={"phone": "3222222222"})
        assert r.status_code == 200

    def test_people_can_still_edit_themselves(self, client_as, world):
        assert client_as(world.eca_a.operator).patch(
            f"/users/{world.eca_a.operator.id}", json={"phone": "3333333333"}).status_code == 200


class TestAccountsWithoutAnOrganization:
    """Fail closed: someone who belongs to no organization reaches no organization's people."""

    @pytest.fixture
    def orphans(self, db):
        return SimpleNamespace(
            admin=factories.make_user(db, "eca", role_code="eca_admin", organization_id=None, full_name="Huérfano Admin"),
            operator=factories.make_user(db, "eca", role_code="eca_operator", organization_id=None,
                                         full_name="Huérfano Operador"),
        )

    def test_they_see_no_staff_at_all_not_even_each_other(self, client_as, world, orphans):
        got = _ids(client_as(orphans.admin).get("/users", params={"limit": 500}))
        assert str(world.recycler.id) not in got  # no organization: no linked association either
        assert not got & (_staff_ids(world.eca_a, world.eca_b, world.assoc_x) | {str(orphans.operator.id)})

    def test_nobody_else_sees_them(self, client_as, world, orphans):
        got = _ids(client_as(world.eca_a.admin).get("/users", params={"limit": 500}))
        assert str(orphans.admin.id) not in got and str(orphans.operator.id) not in got
        assert client_as(world.eca_a.admin).get(f"/users/{orphans.operator.id}").status_code == 404

    def test_an_admin_without_an_organization_cannot_create_staff(self, client_as, orphans):
        payload = {"user_type_code": "eca", "email": "nuevo@test.com", "password": "Segura12345",
                   "full_name": "Nuevo", "id_type": "CC", "id_number": "8880001"}
        r = client_as(orphans.admin).post("/auth/register", json=payload)
        assert r.status_code == 403 and r.json()["code"] == "no_organization"

    def test_nor_can_they_edit_other_staff(self, client_as, world, orphans):
        r = client_as(orphans.admin).patch(f"/users/{world.eca_a.operator.id}", json={"phone": "3444444444"})
        assert r.status_code == 404

    def test_they_can_still_register_recyclers_and_edit_themselves(self, client_as, world, orphans):
        payload = {"user_type_code": "recycler", "email": "r@test.com", "full_name": "Reci", "id_type": "CC",
                   "id_number": "8880002", "association_id": str(world.assoc_x.org.id)}
        assert client_as(orphans.admin).post("/auth/register", json=payload).status_code == 201
        assert client_as(orphans.admin).patch(
            f"/users/{orphans.admin.id}", json={"phone": "3555555555"}).status_code == 200


class TestAuditTrail:
    def _event(self, db, actor=None, target=None, target_type="user", action="auth.login", minute=0):
        db.add(AuditLog(
            action=action, outcome="success", actor_id=actor.id if actor else None,
            target_type=target_type, target_id=str(target.id) if target else None,
            occurred_at=datetime(2026, 4, 1, 12, minute, tzinfo=timezone.utc)))
        db.commit()

    def _trail(self, client_as, admin):
        r = client_as(admin).get("/audit-log", params={"limit": 200})
        assert r.status_code == 200
        return r.json()

    def test_an_association_reads_only_its_own_peoples_events(self, client_as, world, db):
        self._event(db, world.assoc_x.operator, world.assoc_x.operator, minute=1)
        self._event(db, world.assoc_y.operator, world.assoc_y.operator, minute=2)
        self._event(db, world.eca_a.admin, world.eca_a.admin, minute=3)
        trail = self._trail(client_as, world.assoc_x.admin)
        assert {i["actor_id"] for i in trail["items"]} == {str(world.assoc_x.operator.id)}
        assert trail["total"] == 1

    def test_an_attempt_against_its_accounts_is_visible_even_without_an_actor(self, client_as, world, db):
        self._event(db, None, world.assoc_x.operator, action="auth.login_failed", minute=1)
        self._event(db, None, world.assoc_y.operator, action="auth.login_failed", minute=2)
        items = self._trail(client_as, world.assoc_x.admin)["items"]
        assert [i["target_id"] for i in items] == [str(world.assoc_x.operator.id)]

    def test_events_with_no_actor_and_no_account_belong_to_nobody(self, client_as, world, db):
        self._event(db, None, None, target_type="weighing", action="weighing.created")
        assert self._trail(client_as, world.assoc_x.admin)["total"] == 0

    def test_an_admin_without_an_organization_reads_nothing(self, client_as, world, db):
        orphan = factories.make_user(db, "association", role_code="association_admin", organization_id=None)
        self._event(db, world.assoc_x.operator, world.assoc_x.operator)
        self._event(db, orphan, orphan)
        assert self._trail(client_as, orphan)["total"] == 0

    def test_filters_cannot_widen_the_scope(self, client_as, world, db):
        self._event(db, world.assoc_y.operator, world.assoc_y.operator)
        c = client_as(world.assoc_x.admin)
        assert c.get("/audit-log", params={"actor_id": str(world.assoc_y.operator.id)}).json()["total"] == 0
        assert c.get("/audit-log", params={"target_id": str(world.assoc_y.operator.id)}).json()["total"] == 0


class TestRegistrationStaysInside:
    def test_a_new_person_lands_in_the_creators_organization_and_is_visible_only_there(self, client_as, world, db):
        payload = {"user_type_code": "eca", "email": "nueva@test.com", "password": "Segura12345",
                   "full_name": "Persona Nueva Alfa", "id_type": "CC", "id_number": "8881111",
                   "role_code": "eca_operator"}
        created = client_as(world.eca_a.admin).post("/auth/register", json=payload).json()
        assert created["id"] in _ids(client_as(world.eca_a.admin).get("/users", params={"user_type_code": "eca"}))
        assert client_as(world.eca_b.admin).get(f"/users/{created['id']}").status_code == 404


class TestCollectionRequestsStayWithTheirRequester:
    """Reading and cancelling a pickup request is the requester's alone: no ECA or Association reaches them
    there, and one citizen never reaches another's."""

    def test_no_organization_reaches_a_citizens_request(self, client_as, world, db):
        created = client_as(world.citizen).post("/collections", json={
            "address": "Calle 45 # 12-30", "latitude": "4.6", "longitude": "-74.1", "materials": ["paper"]}).json()
        for staff in (world.eca_a.admin, world.eca_b.admin, world.assoc_x.admin, world.assoc_y.operator):
            c = client_as(staff)
            assert c.get("/collections").status_code == 403
            assert c.get(f"/collections/{created['id']}").status_code == 403
            assert c.patch(f"/collections/{created['id']}/cancel").status_code == 403

        neighbour = client_as(factories.make_user(db, "citizen"))
        assert neighbour.get(f"/collections/{created['id']}").status_code == 404
        assert neighbour.get("/collections").json()["total"] == 0


class TestCollectionRequestsReachOnlyTheirAssociation:
    """A request assigned to one association is rejected only by it; for any other it does not exist."""

    def test_another_association_cannot_reject_it_and_an_eca_is_refused(self, client_as, world, db):
        from app.domains.collections.models import Collection, CollectionStatus

        created = client_as(world.citizen).post("/collections", json={
            "address": "Calle 45 # 12-30", "latitude": "4.6", "longitude": "-74.1", "materials": ["paper"]}).json()
        db.get(Collection, uuid.UUID(created["id"])).association_id = world.assoc_x.org.id
        db.commit()
        body = {"reason_code": "duplicate_request"}

        for outsider in (world.assoc_y.admin, world.assoc_y.operator):
            r = client_as(outsider).post(f"/collections/{created['id']}/reject", json=body)
            assert r.status_code == 404 and r.json()["code"] == "collection_not_found"
        for eca_staff in (world.eca_a.admin, world.eca_a.operator):
            assert client_as(eca_staff).post(f"/collections/{created['id']}/reject", json=body).status_code == 403
        assert db.get(Collection, uuid.UUID(created["id"])).status == CollectionStatus.pending_assignment

        r = client_as(world.assoc_x.operator).post(f"/collections/{created['id']}/reject", json=body)
        assert r.status_code == 200 and r.json()["status"] == "rejected"


class TestIdempotencyKeysBelongToTheirAccount:
    """A stored response is replayed only to the account that created it: another ECA reusing the same
    `Idempotency-Key` gets its own record, never the other ECA's weighing."""

    def test_two_ecas_with_the_same_key_each_create_their_own_weighing(self, client_as, world, db):
        from app.domains.inventory.models import Warehouse

        warehouse_a, warehouse_b = db.query(Warehouse).order_by(Warehouse.name).limit(2).all()
        warehouse_a.organization_id, warehouse_b.organization_id = world.eca_a.org.id, world.eca_b.org.id
        db.commit()
        headers = {"Idempotency-Key": str(uuid.uuid4())}

        def weigh(eca, warehouse):
            return client_as(eca.admin).post("/weighings", headers=headers, json={
                "seller": {"full_name": "Persona Sin Registro", "id_type": "CC", "id_number": "5550001"},
                "material_code": "plastic", "warehouse_id": str(warehouse.id), "kg": "5", "price_per_kg": "300"})

        from_a, from_b = weigh(world.eca_a, warehouse_a), weigh(world.eca_b, warehouse_b)
        assert from_a.status_code == from_b.status_code == 201, from_b.text
        assert from_a.json()["id"] != from_b.json()["id"]
        assert client_as(world.eca_b.admin).get(f"/weighings/{from_a.json()['id']}").status_code == 404


class TestStatsStayInScopeWithFilters:
    """The period and material filters rewrite the WHERE of three aggregates: the scope must survive them."""

    FILTERS = {"date_from": "2020-01-01T00:00:00Z", "date_to": "2999-01-01T00:00:00Z", "material_code": "plastic"}

    @pytest.fixture
    def data(self, client_as, db, world):
        from app.domains.inventory.models import Warehouse

        warehouse = db.query(Warehouse).order_by(Warehouse.name).first()
        warehouse.organization_id = world.eca_a.org.id
        db.commit()
        c = client_as(world.eca_a.admin)
        weighing_id = c.post("/weighings", json={
            "recycler_id": str(world.recycler.id), "material_code": "plastic", "warehouse_id": str(warehouse.id),
            "kg": "10", "price_per_kg": "400"}).json()["id"]
        assert c.patch(f"/weighings/{weighing_id}/status", json={"status": "validated"}).status_code == 200

    def test_the_owning_eca_sees_its_figures(self, client_as, world, data):
        c = client_as(world.eca_a.admin)
        assert c.get("/weighings/stats", params=self.FILTERS).json()["total_weighings_month"] == 1
        assert c.get("/transactions/stats", params=self.FILTERS).json()["total_purchases_month"] == 1
        assert float(c.get("/inventory/stats", params={"material_code": "plastic"}).json()["total_stock_kg"]) == 10

    @pytest.mark.parametrize("who", ["eca_b", "assoc_y"])
    def test_another_eca_or_an_unlinked_association_sees_zeros(self, client_as, world, data, who):
        c = client_as(getattr(world, who).admin)
        assert c.get("/weighings/stats", params=self.FILTERS).json()["total_weighings_month"] == 0
        assert c.get("/weighings/stats", params=self.FILTERS).json()["by_material"] == []
        assert c.get("/inventory/stats", params={"material_code": "plastic"}).json()["total_stock_kg"] in ("0", "0.00")
        if who == "eca_b":  # an association never reads transactions of an ECA it is not linked to either
            assert c.get("/transactions/stats", params=self.FILTERS).json()["total_purchases_month"] == 0


class TestWeighingVerificationScope:
    """`GET /weighings/{id}/verify` is read by whoever may read the weighing, and nobody else."""

    @pytest.fixture
    def weighing_id(self, client_as, db, world):
        from app.domains.inventory.models import Warehouse

        warehouse = db.query(Warehouse).order_by(Warehouse.name).first()
        warehouse.organization_id = world.eca_a.org.id
        db.commit()
        r = client_as(world.eca_a.admin).post("/weighings", json={
            "recycler_id": str(world.recycler.id), "material_code": "plastic", "warehouse_id": str(warehouse.id),
            "kg": "10", "price_per_kg": "400"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_the_owning_eca_verifies_it(self, client_as, world, weighing_id):
        assert client_as(world.eca_a.admin).get(f"/weighings/{weighing_id}/verify").json()["valid"] is True

    def test_another_eca_gets_the_answer_of_a_missing_weighing(self, client_as, world, weighing_id):
        other = client_as(world.eca_b.admin).get(f"/weighings/{weighing_id}/verify")
        missing = client_as(world.eca_b.admin).get(f"/weighings/{MISSING}/verify")
        assert other.status_code == 404 and other.json() == missing.json()
        assert other.json()["code"] == "weighing_not_found"

    def test_an_association_that_is_not_linked_does_not_see_it(self, client_as, world, weighing_id):
        r = client_as(world.assoc_y.admin).get(f"/weighings/{weighing_id}/verify")
        assert r.status_code == 404 and r.json()["code"] == "weighing_not_found"

    def test_the_linked_association_reaches_it_only_when_it_was_delivered_through_the_link(
        self, client_as, db, world, weighing_id,
    ):
        from app.domains.weighings.models import AffiliationStatus, Weighing

        weighing = db.get(Weighing, weighing_id)
        r = client_as(world.assoc_x.admin).get(f"/weighings/{weighing_id}/verify")
        assert (r.status_code == 200) == (weighing.affiliation_status == AffiliationStatus.linked)
        weighing.affiliation_status = AffiliationStatus.unlinked_association
        db.commit()
        assert client_as(world.assoc_x.admin).get(f"/weighings/{weighing_id}/verify").status_code == 404


class TestFullWeighingListScope:
    """`GET /weighings/all` returns everything the actor may read, and nothing of anyone else's."""

    @pytest.fixture
    def delivered(self, client_as, db, world):
        from app.domains.inventory.models import Warehouse
        from app.domains.weighings.models import AffiliationStatus, Weighing

        warehouse = db.query(Warehouse).order_by(Warehouse.name).first()
        warehouse.organization_id = world.eca_a.org.id
        db.commit()
        c = client_as(world.eca_a.admin)
        ids = [
            c.post("/weighings", json={
                "recycler_id": str(world.recycler.id), "material_code": "plastic", "warehouse_id": str(warehouse.id),
                "kg": "10", "price_per_kg": "400"}).json()["id"]
            for _ in range(2)]
        # One of them reached the association through the link; the other did not.
        for weighing_id, affiliation in zip(ids, (AffiliationStatus.linked, AffiliationStatus.unlinked_association)):
            db.get(Weighing, weighing_id).affiliation_status = affiliation
        db.commit()
        return SimpleNamespace(ids=ids, linked=ids[0], unlinked=ids[1])

    @staticmethod
    def _all(client_as, user):
        r = client_as(user).get("/weighings/all")
        assert r.status_code == 200, r.text
        return {item["id"] for item in r.json()["items"]}, r.json()

    def test_the_owning_eca_gets_all_of_them(self, client_as, world, delivered):
        got, body = self._all(client_as, world.eca_a.admin)
        assert got == set(delivered.ids) and body["total"] == 2 and body["truncated"] is False

    def test_another_eca_and_an_unlinked_association_get_an_empty_list(self, client_as, world, delivered):
        for user in (world.eca_b.admin, world.assoc_y.admin):
            got, body = self._all(client_as, user)
            assert got == set() and body["total"] == 0

    def test_the_linked_association_gets_only_what_arrived_through_the_link(self, client_as, world, delivered):
        got, body = self._all(client_as, world.assoc_x.admin)
        assert got == {delivered.linked} and body["total"] == 1

    def test_a_recycler_gets_only_their_own(self, client_as, world, delivered, db):
        other_recycler = factories.make_user(db, "recycler", organization_id=world.assoc_x.org.id)
        assert self._all(client_as, other_recycler)[0] == set()
        assert self._all(client_as, world.recycler)[0] == set(delivered.ids)
