"""PATCH /users/{id}/status: an organization's admin deactivates or reactivates their own staff."""
import pytest
from sqlalchemy import select

from app.domains.audit.models import AuditLog
from tests import factories
from tests.factories import DEFAULT_PASSWORD


@pytest.fixture
def org(db):
    organization = factories.make_organization(db, "eca", legal_name="ECA Norte")
    return organization, factories.make_user(db, "eca", role_code="eca_admin", organization_id=organization.id)


def _staff(db, organization, role="eca_operator"):
    return factories.make_user(db, "eca", role_code=role, organization_id=organization.id)


def _set(client_as, actor, user_id, active, **extra):
    return client_as(actor).patch(f"/users/{user_id}/status", json={"is_active": active, **extra})


class TestDeactivatingAndReactivating:
    def test_the_admin_deactivates_and_reactivates_their_staff(self, client_as, db, org):
        organization, admin = org
        person = _staff(db, organization)
        r = _set(client_as, admin, person.id, False, reason="Ya no trabaja aquí")
        assert r.status_code == 200, r.text
        assert r.json()["is_active"] is False
        assert _set(client_as, admin, person.id, True).json()["is_active"] is True

    def test_deactivating_signs_them_out_everywhere(self, client, client_as, db, org):
        organization, admin = org
        person = _staff(db, organization)
        token = client.post("/auth/login", json={"email": person.email, "password": DEFAULT_PASSWORD}).json()
        assert _set(client_as, admin, person.id, False).status_code == 200
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"}).status_code == 401
        assert client.post("/auth/refresh", json={"refresh_token": token["refresh_token"]}).status_code == 401
        assert client.post("/auth/login", json={"email": person.email, "password": DEFAULT_PASSWORD}).status_code == 403
        _set(client_as, admin, person.id, True)
        assert client.post("/auth/login", json={"email": person.email, "password": DEFAULT_PASSWORD}).status_code == 200

    def test_it_is_audited_with_who_and_whom(self, client_as, db, org):
        organization, admin = org
        person = _staff(db, organization)
        _set(client_as, admin, person.id, False, reason="Baja")
        _set(client_as, admin, person.id, True)
        entries = db.scalars(select(AuditLog).where(AuditLog.target_id == str(person.id))).all()
        assert {e.action for e in entries} == {"user.deactivated", "user.activated"}
        assert all(e.actor_id == admin.id for e in entries)

    def test_repeating_the_same_state_records_nothing(self, client_as, db, org):
        organization, admin = org
        person = _staff(db, organization)
        assert _set(client_as, admin, person.id, True).status_code == 200
        assert db.scalars(select(AuditLog).where(AuditLog.target_id == str(person.id))).all() == []


class TestLimits:
    def test_nobody_changes_their_own_status(self, client_as, org):
        r = _set(client_as, org[1], org[1].id, False)
        assert r.status_code == 403 and r.json()["code"] == "cannot_change_own_status"

    def test_only_admins_may(self, client_as, db, org):
        organization, _ = org
        operator, person = _staff(db, organization), _staff(db, organization)
        r = _set(client_as, operator, person.id, False)
        assert r.status_code == 403

    def test_a_recycler_is_not_managed_from_here(self, client_as, db):
        association = factories.make_organization(db, "association")
        admin = factories.make_user(db, "association", role_code="association_admin", organization_id=association.id)
        recycler = factories.make_user(db, "recycler", organization_id=association.id)
        r = _set(client_as, admin, recycler.id, False)
        assert r.status_code == 404 and r.json()["code"] == "user_not_found"

    def test_the_reason_is_limited(self, client_as, db, org):
        organization, admin = org
        assert _set(client_as, admin, _staff(db, organization).id, False, reason="x" * 201).status_code == 422


class TestOrganizationIsolation:
    def test_another_organizations_staff_looks_like_a_missing_user(self, client_as, db, org):
        _, admin = org
        other = factories.make_organization(db, "eca", legal_name="ECA Sur")
        person = _staff(db, other)
        r = _set(client_as, admin, person.id, False)
        missing = _set(client_as, admin, "00000000-0000-0000-0000-000000000000", False)
        assert r.status_code == 404 and r.json() == missing.json()
        db.refresh(person)
        assert person.is_active is True

    def test_an_association_admin_cannot_reach_eca_staff(self, client_as, db, org):
        organization, _ = org
        association = factories.make_organization(db, "association")
        admin = factories.make_user(db, "association", role_code="association_admin", organization_id=association.id)
        assert _set(client_as, admin, _staff(db, organization).id, False).status_code == 404

    def test_an_admin_without_an_organization_reaches_nobody(self, client_as, db, org):
        organization, _ = org
        orphan = factories.make_user(db, "eca", role_code="eca_admin", organization_id=None)
        assert _set(client_as, orphan, _staff(db, organization).id, False).status_code == 404
