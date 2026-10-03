"""6.11: an ECA or an Association is not self-service. Anonymous, or any account that is not the administrator of
that same type of organization, cannot create one through POST /auth/register."""
import pytest
from sqlalchemy import select

from app.domains.users.models import User
from tests import factories

PASSWORD = factories.DEFAULT_PASSWORD


def _payload(user_type_code, **extra):
    base = {"user_type_code": user_type_code, "email": f"{user_type_code}@nuevo.com", "password": PASSWORD,
            "full_name": "Persona Nueva", "id_type": "CC", "id_number": "70707070"}
    if user_type_code == "association":
        base.update(association_nit="900111222-3", legal_representative="Rep Legal")
    base.update(extra)
    return base


def _users(db, email):
    return db.scalars(select(User).where(User.email == email)).all()


class TestWhoIsRefused:
    @pytest.mark.parametrize("kind", ["eca", "association"])
    def test_an_anonymous_caller_cannot_create_either_type(self, client, db, kind):
        r = client.post("/auth/register", json=_payload(kind))
        assert r.status_code == 403 and r.json()["code"] == "registration_closed"
        assert _users(db, f"{kind}@nuevo.com") == []

    @pytest.mark.parametrize("kind", ["eca", "association"])
    def test_a_role_does_not_get_it_through(self, client, db, kind):
        role = "eca_admin" if kind == "eca" else "association_admin"
        r = client.post("/auth/register", json=_payload(kind, role_code=role))
        assert r.status_code == 403 and _users(db, f"{kind}@nuevo.com") == []

    def test_a_citizen_or_a_recycler_account_cannot_either(self, client_as, db):
        for actor in (factories.make_user(db, "citizen"), factories.make_user(db, "recycler")):
            r = client_as(actor).post("/auth/register", json=_payload("eca"))
            assert r.status_code == 403 and r.json()["code"] == "registration_closed"
        assert _users(db, "eca@nuevo.com") == []

    def test_staff_without_admin_rights_cannot(self, client_as, db):
        org = factories.make_organization(db, "eca")
        operator = factories.make_user(db, "eca", role_code="eca_operator", organization_id=org.id)
        r = client_as(operator).post("/auth/register", json=_payload("eca"))
        assert r.status_code == 403 and r.json()["code"] == "registration_closed"

    def test_an_admin_of_the_other_type_cannot(self, client_as, db):
        org = factories.make_organization(db, "association")
        admin = factories.make_user(db, "association", role_code="association_admin", organization_id=org.id)
        r = client_as(admin).post("/auth/register", json=_payload("eca"))
        assert r.status_code == 403 and r.json()["code"] == "registration_closed"

    def test_a_somos_r_account_cannot_use_this_endpoint(self, client_as, db):
        platform = factories.make_user(db, "platform", role_code="platform_admin")
        assert client_as(platform).post("/auth/register", json=_payload("eca")).status_code in (401, 403)
        assert _users(db, "eca@nuevo.com") == []


class TestWhatStillWorks:
    def test_the_admin_of_that_same_type_still_adds_staff(self, client_as, db):
        org = factories.make_organization(db, "eca")
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        r = client_as(admin).post("/auth/register", json=_payload("eca", role_code="eca_operator"))
        assert r.status_code == 201, r.text
        assert db.get(User, r.json()["id"]).organization_id == org.id

    @pytest.mark.parametrize("kind", ["citizen", "b2b_client", "building"])
    def test_other_types_register_openly_as_before(self, client, kind):
        extra = {"b2b_client": {"company_name": "Empresa", "tax_id": "900999000-1"},
                 "building": {"building_name": "Torre Norte", "num_units": 40}}.get(kind, {})
        r = client.post("/auth/register", json=_payload(kind, **extra))
        assert r.status_code == 201, r.text

    def test_the_way_in_is_the_application_and_its_approval(self, client, outbox):
        r = client.post("/applications", json={
            "type": "eca", "legal_name": "ECA Nueva", "applicant_name": "Laura Gómez",
            "applicant_email": "laura@eca-nueva.org", "consent": True})
        assert r.status_code == 202


def test_the_message_points_to_the_application(client):
    r = client.post("/auth/register", json=_payload("eca"))
    assert "solicitud" in r.json()["detail"]
