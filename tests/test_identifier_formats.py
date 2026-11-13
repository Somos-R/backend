"""Phone, NIT, document type and buyer email are validated where they enter the API."""
import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from app.core.identifiers import Nit, OptionalNit, OptionalPhone, Phone, nit_check_digit
from app.domains.applications.models import OrganizationApplication
from app.domains.organizations.models import Organization
from app.domains.users.models import User
from tests import factories
from tests.factories import DEFAULT_PASSWORD

phone = TypeAdapter(Phone)
nit = TypeAdapter(Nit)


class TestPhone:
    @pytest.mark.parametrize(("typed", "stored"), [
        ("3001234567", "3001234567"),
        ("+57 300 123 4567", "+573001234567"),
        ("(601) 234-5678", "6012345678"),
        ("300.123.4567", "3001234567"),
    ])
    def test_separators_are_dropped(self, typed, stored):
        assert phone.validate_python(typed) == stored

    @pytest.mark.parametrize("value", ["not-a-phone", "123456", "1" * 16, "57+3001234567", "300 123 456x", "++573001234"])
    def test_anything_but_7_to_15_digits_is_refused(self, value):
        with pytest.raises(ValidationError):
            phone.validate_python(value)


class TestNit:
    @pytest.mark.parametrize(("number", "check_digit"), [
        ("900123456", 8), ("901234567", 7), ("800197268", 4), ("899999068", 1), ("900333444", 0), ("900111222", 1),
    ])
    def test_check_digit(self, number, check_digit):
        assert nit_check_digit(number) == check_digit

    def test_dots_and_spaces_are_dropped(self):
        assert nit.validate_python(" 900.123.456-8 ") == "900123456-8"

    @pytest.mark.parametrize("value", ["900123456-7", "9001234568", "900123456", "90012-8", "ABC123456-8", "900123456-88"])
    def test_a_wrong_check_digit_or_format_is_refused(self, value):
        with pytest.raises(ValidationError):
            nit.validate_python(value)


@pytest.mark.parametrize("optional_type", [OptionalPhone, OptionalNit])
@pytest.mark.parametrize("blank", ["", "   ", None])
def test_optional_fields_take_a_blank_as_no_value(optional_type, blank):
    assert TypeAdapter(optional_type).validate_python(blank) is None


def _b2b_payload(**overrides):
    return {
        "user_type_code": "b2b_client", "email": "compras@industria.co", "password": DEFAULT_PASSWORD,
        "full_name": "Andrés Castillo", "id_type": "CC", "id_number": "1098765432",
        "company_name": "Industrias Verdes S.A.S.", "tax_id": "901234567-7", **overrides,
    }


class TestRegister:
    def test_valid_values_are_stored_normalized(self, client, db):
        r = client.post("/auth/register", json=_b2b_payload(phone="+57 322 345 6789", tax_id="901.234.567-7"))
        assert r.status_code == 201, r.text
        user = db.scalars(select(User).where(User.email == "compras@industria.co")).one()
        assert (user.phone, user.tax_id) == ("+573223456789", "901234567-7")

    @pytest.mark.parametrize("field", [{"phone": "not-a-phone"}, {"tax_id": "901234567-8"}, {"tax_id": "123"}])
    def test_a_malformed_value_is_refused(self, client, db, field):
        r = client.post("/auth/register", json=_b2b_payload(**field))
        assert r.status_code == 422
        assert db.scalars(select(User).where(User.email == "compras@industria.co")).first() is None

    def test_an_unknown_document_type_is_a_422_not_a_conflict(self, client):
        r = client.post("/auth/register", json=_b2b_payload(id_type="XX"))
        assert r.status_code == 422
        assert r.json()["code"] == "invalid_id_type"


class TestUpdateUser:
    def test_a_malformed_phone_or_nit_is_refused(self, client_as, db):
        b2b = factories.make_user(db, "b2b_client")
        c = client_as(b2b)
        assert c.patch(f"/users/{b2b.id}", json={"phone": "not-a-phone"}).status_code == 422
        assert c.patch(f"/users/{b2b.id}", json={"tax_id": "900123456-7"}).status_code == 422
        r = c.patch(f"/users/{b2b.id}", json={"phone": "301 234 5678"})
        assert r.status_code == 200 and r.json()["phone"] == "3012345678"


class TestInvitation:
    def test_a_malformed_phone_is_refused(self, client_as, eca_admin):
        r = client_as(eca_admin).post("/users/invitations", json={
            "email": "nuevo@eca.co", "full_name": "Persona Nueva", "id_type": "CC", "id_number": "1020304050",
            "phone": "abc", "role_code": "eca_operator"})
        assert r.status_code == 422


class TestApplication:
    @pytest.fixture
    def token(self, client, outbox):
        r = client.post("/applications", json={
            "type": "association", "legal_name": "Asociación Esperanza", "applicant_name": "Laura Gómez",
            "applicant_email": "laura@asociacion.org", "consent": True, "tax_id": "900.123.456-8"})
        assert r.status_code == 202, r.text
        return outbox[-1].body.split("token=")[1].split()[0]

    def _organization(self, db) -> Organization:
        application = db.scalars(select(OrganizationApplication)).one()
        db.expire_all()
        return db.get(Organization, application.organization_id)

    def test_starting_stores_the_nit_normalized(self, token, db):
        assert self._organization(db).tax_id == "900123456-8"

    def test_starting_with_a_wrong_check_digit_is_refused(self, client):
        r = client.post("/applications", json={
            "type": "association", "legal_name": "Asociación Esperanza", "applicant_name": "Laura Gómez",
            "applicant_email": "laura@asociacion.org", "consent": True, "tax_id": "900123456-7"})
        assert r.status_code == 422

    @pytest.mark.parametrize("field", [
        {"tax_id": "900123456-7"}, {"contact_phone": "123"}, {"applicant_phone": "no tengo"},
    ])
    def test_editing_with_a_malformed_value_is_refused(self, client, token, field):
        r = client.patch("/applications/current", headers={"X-Application-Token": token}, json=field)
        assert r.status_code == 422

    def test_a_blank_still_clears_the_field(self, client, token, db):
        headers = {"X-Application-Token": token}
        assert client.patch("/applications/current", headers=headers, json={"tax_id": ""}).status_code == 200
        assert self._organization(db).tax_id is None


def _sale(warehouse, **overrides):
    return {"material_code": "plastic", "warehouse_id": str(warehouse.id), "kg": "10", "price_per_kg": "900",
            "buyer_name": "Industrias Verdes", **overrides}


class TestSale:
    @pytest.mark.parametrize("field", [{"buyer_email": "no-es-correo"}, {"buyer_nit": "901234567-8"}])
    def test_a_malformed_buyer_email_or_nit_is_refused(self, client_as, eca_admin, db, warehouse, field):
        factories.stock(db, warehouse, kg="100")
        r = client_as(eca_admin).post("/transactions", json=_sale(warehouse, **field))
        assert r.status_code == 422

    def test_buyer_email_and_nit_are_stored_normalized(self, client_as, eca_admin, db, warehouse):
        factories.stock(db, warehouse, kg="100")
        r = client_as(eca_admin).post("/transactions", json=_sale(
            warehouse, buyer_email=" Compras@Industria.CO ", buyer_nit="901.234.567-7"))
        assert r.status_code == 201, r.text
        assert (r.json()["buyer_email"], r.json()["buyer_nit"]) == ("compras@industria.co", "901234567-7")
