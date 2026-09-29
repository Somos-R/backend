"""The Organization entity, users.organization_id and how staff get their organization."""
import itertools

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domains.organizations.enums import OrganizationStatus, OrganizationType
from app.domains.organizations.models import Organization
from app.domains.users.models import User
from tests import factories

PASSWORD = "Segura12345"
_n = itertools.count(1)


def _staff_payload(user_type="eca", **extra):
    n = next(_n)
    base = {"user_type_code": user_type, "email": f"nuevo{n}@test.com", "password": PASSWORD,
            "full_name": "Persona Nueva", "id_type": "CC", "id_number": f"7{n:07d}"}
    if user_type == "association":
        base.update({"association_nit": "900123456-7", "legal_representative": "Rep Legal"})
    base.update(extra)
    return base


class TestEntity:
    def test_defaults_to_draft(self, db):
        org = Organization(type=OrganizationType.eca, legal_name="ECA Norte")
        db.add(org)
        db.commit()
        db.refresh(org)
        assert org.status == OrganizationStatus.draft and org.approved_at is None
        assert org.created_at is not None and org.id is not None

    def test_the_onboarding_states_exist(self):
        assert [s.value for s in OrganizationStatus] == [
            "draft", "submitted", "in_review", "changes_requested", "approved", "rejected", "suspended"]

    def test_a_tax_id_is_unique_within_a_type(self, db):
        factories.make_organization(db, "association", tax_id="900111222-3")
        with pytest.raises(IntegrityError):
            factories.make_organization(db, "association", tax_id="900111222-3")
        db.rollback()

    def test_the_same_nit_may_exist_as_an_association_and_as_an_eca(self, db):
        factories.make_organization(db, "association", tax_id="900333444-5")
        assert factories.make_organization(db, "eca", tax_id="900333444-5").id is not None

    def test_organizations_without_a_tax_id_may_coexist(self, db):
        factories.make_organization(db, "eca", tax_id=None)
        assert factories.make_organization(db, "eca", tax_id=None).id is not None

    def test_an_organization_with_staff_cannot_be_deleted(self, db):
        org = factories.make_organization(db, "eca")
        factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        with pytest.raises(IntegrityError):
            db.delete(org)
            db.commit()
        db.rollback()

    def test_users_outside_the_staff_have_none(self, db):
        assert factories.make_user(db, "recycler").organization_id is None
        assert factories.make_user(db, "citizen").organization_id is None


class TestStaffInheritTheirAdminsOrganization:
    def test_staff_created_by_an_admin_join_the_admins_organization(self, client_as, db):
        org = factories.make_organization(db, "eca")
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        r = client_as(admin).post("/auth/register", json=_staff_payload("eca", role_code="eca_operator"))
        assert r.status_code == 201, r.text
        assert db.get(User, r.json()["id"]).organization_id == org.id

    def test_the_same_for_an_association_admin(self, client_as, db):
        org = factories.make_organization(db, "association")
        admin = factories.make_user(db, "association", role_code="association_admin", organization_id=org.id)
        r = client_as(admin).post(
            "/auth/register", json=_staff_payload("association", role_code="association_operator"))
        assert r.status_code == 201, r.text
        assert db.get(User, r.json()["id"]).organization_id == org.id

    def test_staff_without_a_role_created_by_an_admin_also_join(self, client_as, db):
        org = factories.make_organization(db, "eca")
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        r = client_as(admin).post("/auth/register", json=_staff_payload("eca"))
        assert db.get(User, r.json()["id"]).organization_id == org.id

    def test_an_admin_registering_another_type_does_not_pass_its_organization_on(self, client_as, db):
        org = factories.make_organization(db, "eca")
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        r = client_as(admin).post("/auth/register", json=_staff_payload("association"))
        assert db.get(User, r.json()["id"]).organization_id is None

    def test_an_anonymous_registration_has_no_organization(self, client, db):
        r = client.post("/auth/register", json=_staff_payload("eca"))
        assert r.status_code == 201
        assert db.get(User, r.json()["id"]).organization_id is None

    def test_a_recycler_registered_by_staff_has_none(self, client_as, db):
        org = factories.make_organization(db, "association")
        admin = factories.make_user(db, "association", role_code="association_admin", organization_id=org.id)
        payload = {"user_type_code": "recycler", "email": "reci@test.com", "full_name": "Reci Clador",
                   "id_type": "CC", "id_number": "5551234"}
        r = client_as(admin).post("/auth/register", json=payload)
        assert r.status_code == 201, r.text
        assert db.get(User, r.json()["id"]).organization_id is None


class TestTheClientCannotChooseIt:
    def test_a_registration_body_cannot_set_it(self, client_as, db):
        mine = factories.make_organization(db, "eca")
        other = factories.make_organization(db, "eca")
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=mine.id)
        r = client_as(admin).post("/auth/register", json=_staff_payload("eca", organization_id=str(other.id)))
        assert db.get(User, r.json()["id"]).organization_id == mine.id

    def test_an_anonymous_body_cannot_set_it(self, client, db):
        org = factories.make_organization(db, "eca")
        r = client.post("/auth/register", json=_staff_payload("eca", organization_id=str(org.id)))
        assert db.get(User, r.json()["id"]).organization_id is None

    def test_a_profile_update_cannot_change_it(self, client_as, db):
        mine = factories.make_organization(db, "eca")
        other = factories.make_organization(db, "eca")
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=mine.id)
        colleague = factories.make_user(db, "eca", role_code="eca_operator", organization_id=mine.id)
        for target in (admin, colleague):
            client_as(admin).patch(
                f"/users/{target.id}", json={"organization_id": str(other.id), "phone": "3000000000"})
            db.refresh(target)
            assert target.organization_id == mine.id and target.phone == "3000000000"


class TestVisibleAsReadOnly:
    def test_me_and_the_user_detail_expose_it(self, client_as, db):
        org = factories.make_organization(db, "eca")
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        assert client_as(admin).get("/auth/me").json()["organization_id"] == str(org.id)
        assert client_as(admin).get(f"/users/{admin.id}").json()["organization_id"] == str(org.id)

    def test_users_without_one_show_null(self, client_as, db):
        recycler = factories.make_user(db, "recycler")
        assert client_as(recycler).get("/auth/me").json()["organization_id"] is None

    def test_the_list_carries_it_too(self, client_as, db):
        org = factories.make_organization(db, "eca")
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        items = client_as(admin).get("/users", params={"user_type_code": "eca"}).json()["items"]
        assert str(org.id) in {i["organization_id"] for i in items}


def test_organizations_are_queryable_by_status(db):
    factories.make_organization(db, "eca", status=OrganizationStatus.submitted)
    factories.make_organization(db, "eca", status=OrganizationStatus.approved)
    rows = db.scalars(select(Organization).where(Organization.status == OrganizationStatus.submitted)).all()
    assert len(rows) >= 1 and all(o.status == OrganizationStatus.submitted for o in rows)
