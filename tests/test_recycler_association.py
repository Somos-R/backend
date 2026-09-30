"""A recycler belongs to an association: chosen at registration, and it is the one that verifies them."""
import itertools
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.organizations.enums import OrganizationStatus
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from tests import factories

_n = itertools.count(1)


def _payload(**extra):
    n = next(_n)
    base = {"user_type_code": "recycler", "email": f"reci{n}@test.com", "full_name": "Reci Clador",
            "id_type": "CC", "id_number": f"4{n:07d}"}
    base.update(extra)
    return base


@pytest.fixture
def w(db):
    def assoc(name, city):
        org = factories.make_organization(db, "association", legal_name=name, city=city)
        return SimpleNamespace(
            org=org,
            admin=factories.make_user(db, "association", role_code="association_admin", organization_id=org.id),
            operator=factories.make_user(db, "association", role_code="association_operator", organization_id=org.id),
        )

    eca = factories.make_organization(db, "eca")
    return SimpleNamespace(
        x=assoc("Asociación Xenón", "Medellín"), y=assoc("Asociación Ñandú", "Cúcuta"),
        eca=eca, eca_admin=factories.make_user(db, "eca", role_code="eca_admin", organization_id=eca.id),
    )


class TestRegistering:
    def test_association_staff_register_recyclers_into_their_own_association(self, client_as, db, w):
        r = client_as(w.x.operator).post("/auth/register", json=_payload())
        assert r.status_code == 201, r.text
        user = db.get(User, r.json()["id"])
        assert user.organization_id == w.x.org.id and user.verification_status == VerificationStatus.pending
        assert user.password_hash == ""

    def test_naming_your_own_association_is_fine(self, client_as, db, w):
        r = client_as(w.x.admin).post("/auth/register", json=_payload(association_id=str(w.x.org.id)))
        assert r.status_code == 201

    def test_association_staff_cannot_register_into_another_association(self, client_as, w):
        r = client_as(w.x.admin).post("/auth/register", json=_payload(association_id=str(w.y.org.id)))
        assert r.status_code == 422 and r.json()["code"] == "invalid_association"

    def test_anyone_else_must_choose_an_association(self, client, client_as, w):
        for caller in (client, client_as(w.eca_admin)):
            r = caller.post("/auth/register", json=_payload())
            assert r.status_code == 422 and r.json()["code"] == "association_required"

    def test_a_self_registering_recycler_chooses_one_from_the_directory(self, client, db, w):
        r = client.post("/auth/register", json=_payload(association_id=str(w.y.org.id)))
        assert r.status_code == 201
        assert db.get(User, r.json()["id"]).organization_id == w.y.org.id

    def test_a_recycler_registered_by_an_eca_admin_also_needs_to_choose(self, client_as, db, w):
        r = client_as(w.eca_admin).post("/auth/register", json=_payload(association_id=str(w.x.org.id)))
        assert r.status_code == 201 and db.get(User, r.json()["id"]).organization_id == w.x.org.id

    @pytest.mark.parametrize("bad", ["missing", "eca", "draft", "rejected", "suspended"])
    def test_the_association_must_exist_and_be_approved(self, client, db, w, bad):
        if bad == "missing":
            chosen = "00000000-0000-0000-0000-000000000000"
        elif bad == "eca":
            chosen = str(w.eca.id)
        else:
            chosen = str(factories.make_organization(db, "association", status=OrganizationStatus(bad)).id)
        r = client.post("/auth/register", json=_payload(association_id=chosen))
        assert r.status_code == 422 and r.json()["code"] == "invalid_association"

    def test_a_malformed_id_is_a_validation_error(self, client):
        assert client.post("/auth/register", json=_payload(association_id="no-es-uuid")).status_code == 422

    def test_the_legacy_column_is_not_filled(self, client, db, w):
        r = client.post("/auth/register", json=_payload(association_id=str(w.x.org.id)))
        assert db.get(User, r.json()["id"]).association_id is None

    def test_a_failed_registration_leaves_no_recycler(self, client, db):
        payload = _payload()
        client.post("/auth/register", json=payload)
        assert db.scalars(select(User).where(User.email == payload["email"])).first() is None

    def test_other_actor_types_still_register_without_an_association(self, client):
        payload = {"user_type_code": "citizen", "email": "ciud@test.com", "password": "Segura12345",
                   "full_name": "Ciud Adano", "id_type": "CC", "id_number": "5550100"}
        assert client.post("/auth/register", json=payload).status_code == 201


class TestPublicDirectory:
    def test_it_lists_approved_associations_with_name_and_city_only(self, client, db, w):
        hidden = factories.make_organization(db, "association", status=OrganizationStatus.submitted, legal_name="Oculta")
        items = {i["id"]: i for i in client.get("/catalogs/associations").json()}
        assert set(items[str(w.x.org.id)]) == {"id", "legal_name", "city"}
        assert items[str(w.x.org.id)]["city"] == "Medellín"
        assert str(hidden.id) not in items and str(w.eca.id) not in items

    def test_no_sign_in_is_needed(self, client, w):
        assert client.get("/catalogs/associations").status_code == 200

    def test_search_ignores_case_and_accents(self, client, w):
        for q in ("xenon", "XENÓN", "nandu"):
            assert len(client.get("/catalogs/associations", params={"q": q}).json()) == 1
        assert client.get("/catalogs/associations", params={"q": "zzzz"}).json() == []

    def test_a_short_query_is_ignored(self, client, w):
        assert len(client.get("/catalogs/associations", params={"q": "x"}).json()) >= 2

    def test_alphabetical_and_paginated(self, client, w):
        everything = [i["id"] for i in client.get("/catalogs/associations", params={"limit": 100}).json()]
        assert everything == [i["id"] for i in client.get("/catalogs/associations", params={"limit": 100}).json()]
        first = client.get("/catalogs/associations", params={"limit": 1, "offset": 0}).json()
        second = client.get("/catalogs/associations", params={"limit": 1, "offset": 1}).json()
        assert first[0]["id"] != second[0]["id"]

    def test_limits_are_validated(self, client):
        assert client.get("/catalogs/associations", params={"limit": 101}).status_code == 422
        assert client.get("/catalogs/associations", params={"limit": 0}).status_code == 422

    def test_it_is_rate_limited(self, client, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_register", "2/minute")
        limiter.reset()
        try:
            assert [client.get("/catalogs/associations").status_code for _ in range(3)] == [200, 200, 429]
        finally:
            limiter.reset()


class TestOnlyItsAssociationReachesTheRecycler:
    @pytest.fixture
    def recycler(self, db, w):
        return factories.make_user(db, "recycler", organization_id=w.x.org.id,
                                   verification_status=VerificationStatus.pending)

    def test_listing_shows_only_the_associations_own_recyclers(self, client_as, db, w, recycler):
        other = factories.make_user(db, "recycler", organization_id=w.y.org.id)
        got = {i["id"] for i in client_as(w.x.admin).get("/users", params={"user_type_code": "recycler", "limit": 500}).json()["items"]}
        assert str(recycler.id) in got and str(other.id) not in got

    def test_the_total_and_the_search_respect_it(self, client_as, db, w, recycler):
        factories.make_user(db, "recycler", organization_id=w.y.org.id, full_name="Ajeno Del Otro")
        body = client_as(w.x.admin).get("/users", params={"user_type_code": "recycler", "limit": 1}).json()
        assert body["total"] == 1
        assert client_as(w.x.admin).get("/users", params={"q": "Ajeno"}).json()["total"] == 0

    def test_another_association_gets_a_404_reading_or_editing(self, client_as, w, recycler):
        c = client_as(w.y.admin)
        for r in (c.get(f"/users/{recycler.id}"), c.patch(f"/users/{recycler.id}", json={"phone": "3000000000"})):
            assert r.status_code == 404 and r.json()["code"] == "user_not_found"

    def test_only_its_association_verifies_them(self, client_as, db, w, recycler):
        other = client_as(w.y.operator).patch(f"/users/{recycler.id}/verification-status", json={"status": "verified"})
        assert other.status_code == 404 and other.json()["code"] == "user_not_found"
        db.refresh(recycler)
        assert recycler.verification_status == VerificationStatus.pending
        ok = client_as(w.x.operator).patch(f"/users/{recycler.id}/verification-status", json={"status": "verified"})
        assert ok.status_code == 200 and ok.json()["verification_status"] == "verified"

    def test_rejecting_is_scoped_the_same_way(self, client_as, w, recycler):
        body = {"status": "rejected", "rejection_reason": "Documento ilegible"}
        assert client_as(w.y.admin).patch(f"/users/{recycler.id}/verification-status", json=body).status_code == 404
        assert client_as(w.x.admin).patch(f"/users/{recycler.id}/verification-status", json=body).status_code == 200

    def test_a_recycler_without_an_association_is_out_of_reach_of_every_association(self, client_as, db, w):
        loose = factories.make_user(db, "recycler", organization_id=None, verification_status=VerificationStatus.pending)
        for staff in (w.x.admin, w.y.admin):
            assert client_as(staff).get(f"/users/{loose.id}").status_code == 404
            assert client_as(staff).patch(
                f"/users/{loose.id}/verification-status", json={"status": "verified"}).status_code == 404
        got = {i["id"] for i in client_as(w.x.admin).get("/users", params={"limit": 500}).json()["items"]}
        assert str(loose.id) not in got

    def test_eca_staff_reach_only_the_recyclers_of_the_associations_linked_to_their_eca(
        self, client_as, db, w, recycler
    ):
        from app.domains.organizations.enums import LinkStatus
        from app.domains.organizations.models import EcaAssociationLink

        other = factories.make_user(db, "recycler", organization_id=w.y.org.id)
        db.add(EcaAssociationLink(eca_id=w.eca.id, association_id=w.x.org.id, status=LinkStatus.active))
        db.commit()
        c = client_as(w.eca_admin)
        got = {i["id"] for i in c.get("/users", params={"user_type_code": "recycler", "limit": 500}).json()["items"]}
        assert str(recycler.id) in got and str(other.id) not in got
        assert c.get(f"/users/{recycler.id}").status_code == 200
        assert c.get(f"/users/{other.id}").status_code == 404

    def test_recyclers_reach_themselves(self, client_as, recycler):
        assert client_as(recycler).get(f"/users/{recycler.id}").status_code == 200
        assert client_as(recycler).patch(f"/users/{recycler.id}", json={"phone": "3111111111"}).status_code == 200

    def test_the_recycler_sees_which_association_they_belong_to(self, client_as, w, recycler):
        assert client_as(recycler).get("/auth/me").json()["organization_id"] == str(w.x.org.id)

    def test_the_backoffice_can_move_a_recycler_between_associations(self, client, w, recycler, db):
        # Moving is refused for staff (it would hand them another organization's data); a recycler is
        # assigned once, from the backoffice, when they have none.
        from fastapi.testclient import TestClient

        from app.main import app

        platform = factories.make_user(db, "platform", role_code="platform_admin")
        bo = TestClient(app, headers=factories.backoffice_headers(platform))
        r = bo.put(f"/admin/users/{recycler.id}/organization", json={"organization_id": str(w.y.org.id)})
        assert r.status_code == 409 and r.json()["code"] == "already_in_organization"
