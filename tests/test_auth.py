"""Characterization tests for /auth and /catalogs: they pin current behavior."""
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from tests import factories
from tests.factories import DEFAULT_PASSWORD


def _citizen_payload(**overrides):
    payload = {
        "user_type_code": "citizen",
        "email": "juan@test.com",
        "password": DEFAULT_PASSWORD,
        "full_name": "Juan Pérez",
        "id_type": "CC",
        "id_number": "1023456789",
    }
    payload.update(overrides)
    return payload


class TestRegister:
    def test_citizen(self, client):
        r = client.post("/auth/register", json=_citizen_payload())
        assert r.status_code == 201
        body = r.json()
        assert body["email"] == "juan@test.com"
        assert body["user_type_code"] == "citizen"
        assert "password" not in body and "password_hash" not in body

    def test_recycler_needs_no_password_and_starts_pending(self, client, db):
        association = factories.make_organization(db, "association")
        r = client.post("/auth/register", json={
            "user_type_code": "recycler",
            "email": "reciclador@test.com",
            "full_name": "Carlos Mendoza",
            "id_type": "CC",
            "id_number": "80234567",
            "association_id": str(association.id),
        })
        assert r.status_code == 201
        user = db.get(User, r.json()["id"])
        assert user.verification_status == VerificationStatus.pending
        assert user.password_hash == ""

    def test_duplicate_email_conflicts(self, client):
        client.post("/auth/register", json=_citizen_payload())
        r = client.post("/auth/register", json=_citizen_payload(id_number="999"))
        assert r.status_code == 409

    def test_duplicate_id_number_conflicts(self, client):
        client.post("/auth/register", json=_citizen_payload())
        r = client.post("/auth/register", json=_citizen_payload(email="otro@test.com"))
        assert r.status_code == 409

    def test_unknown_user_type_is_rejected(self, client):
        r = client.post("/auth/register", json=_citizen_payload(user_type_code="alien"))
        assert r.status_code == 422

    def test_missing_required_field_is_rejected(self, client):
        payload = _citizen_payload()
        del payload["full_name"]
        assert client.post("/auth/register", json=payload).status_code == 422

    def test_building_requires_its_own_fields(self, client):
        r = client.post("/auth/register", json=_citizen_payload(user_type_code="building"))
        assert r.status_code == 422

    def test_admin_can_register_staff_with_a_valid_role(self, client_as, eca_admin):
        r = client_as(eca_admin).post("/auth/register", json=_citizen_payload(
            user_type_code="eca", role_code="eca_operator"))
        assert r.status_code == 201
        assert r.json()["role_code"] == "eca_operator"

    def test_invalid_role_code_is_rejected(self, client_as, eca_admin):
        r = client_as(eca_admin).post("/auth/register", json=_citizen_payload(
            user_type_code="eca", role_code="superadmin"))
        assert r.status_code == 422


class TestLogin:
    def test_success_returns_bearer_token(self, client, db):
        user = factories.make_user(db, "citizen")
        r = client.post("/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD})
        assert r.status_code == 200
        assert r.json()["token_type"] == "bearer"
        assert r.json()["access_token"]

    def test_wrong_password(self, client, db):
        user = factories.make_user(db, "citizen")
        r = client.post("/auth/login", json={"email": user.email, "password": "incorrecta"})
        assert r.status_code == 401

    def test_unknown_email(self, client):
        r = client.post("/auth/login", json={"email": "nadie@test.com", "password": "x"})
        assert r.status_code == 401

    def test_pending_recycler_cannot_log_in(self, client, db):
        user = factories.make_user(
            db, "recycler", verification_status=VerificationStatus.pending, password_hash="")
        r = client.post("/auth/login", json={"email": user.email, "password": ""})
        assert r.status_code == 401  # empty hash never verifies

    def test_unverified_recycler_with_password_is_forbidden(self, client, db):
        user = factories.make_user(
            db, "recycler", verification_status=VerificationStatus.rejected)
        r = client.post("/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD})
        assert r.status_code == 403


class TestSession:
    def test_token_grants_access(self, client, db):
        user = factories.make_user(db, "citizen")
        token = client.post(
            "/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD}
        ).json()["access_token"]
        r = client.get(f"/users/{user.id}", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_missing_token_is_rejected(self, client):
        assert client.get("/users").status_code in (401, 403)

    def test_garbage_token_is_rejected(self, client):
        r = client.get("/users", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

    def test_token_for_deleted_user_is_rejected(self, client_as, db):
        user = factories.make_user(db, "citizen")
        c = client_as(user)
        db.delete(user)
        db.commit()
        assert c.get("/users").status_code == 401

    def test_logout_revokes_the_token(self, client, db):
        user = factories.make_user(db, "citizen")
        token = client.post(
            "/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        assert client.post("/auth/logout", headers=headers).status_code == 200
        r = client.get("/users", headers=headers)
        assert r.status_code == 401
        assert "cerrada" in r.json()["detail"]


class TestCatalogs:
    def test_document_types(self, client):
        r = client.get("/catalogs/document-types")
        assert r.status_code == 200
        assert {"CC", "NIT"} <= {d["code"] for d in r.json()}

    def test_roles(self, client):
        r = client.get("/catalogs/roles")
        assert r.status_code == 200
        assert "eca_admin" in {d["code"] for d in r.json()}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
