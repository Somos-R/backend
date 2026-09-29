"""Known security gaps, written as the *desired* behavior.

Each test is `xfail(strict=True)`: it fails today because the hole exists. When
the fix lands the test starts passing, strict mode turns that into a failure,
and the fixer must delete the marker (or promote the test to the RBAC suite,
task 1.6). Task IDs refer to the hardening plan.
"""
import pytest

from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from tests import factories

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def known_issue(task: str, why: str):
    return pytest.mark.xfail(strict=True, reason=f"[{task}] {why}")


@known_issue("1.3", "any authenticated user can list every user's personal data")
def test_citizen_cannot_list_users(client_as, citizen):
    assert client_as(citizen).get("/users").status_code == 403


@known_issue("1.5", "any authenticated user can read any other user's profile (IDOR)")
def test_citizen_cannot_read_someone_elses_profile(client_as, citizen, db):
    other = factories.make_user(db, "citizen")
    assert client_as(citizen).get(f"/users/{other.id}").status_code in (403, 404)


@known_issue("1.4", "any authenticated user can edit any other user's profile")
def test_citizen_cannot_edit_someone_elses_profile(client_as, citizen, db):
    other = factories.make_user(db, "citizen")
    r = client_as(citizen).patch(f"/users/{other.id}", json={"phone": "0000000000"})
    assert r.status_code in (403, 404)


@known_issue("1.4", "a user can grant themselves any role through PATCH /users/{id}")
def test_user_cannot_self_assign_role(client_as, citizen, db):
    client_as(citizen).patch(f"/users/{citizen.id}", json={"role_code": "eca_admin"})
    db.expire_all()
    assert db.get(User, citizen.id).role_code is None


@known_issue("1.3", "any authenticated user can verify a recycler")
def test_citizen_cannot_verify_recycler(client_as, citizen, db):
    pending = factories.make_user(
        db, "recycler", verification_status=VerificationStatus.pending, password_hash="")
    r = client_as(citizen).patch(
        f"/users/{pending.id}/verification-status", json={"status": "verified"})
    assert r.status_code == 403


@known_issue("1.3", "any authenticated user can validate weighings and move inventory")
def test_citizen_cannot_validate_weighing(client_as, citizen, eca_admin, recycler, warehouse):
    w = client_as(eca_admin).post("/weighings", json={
        "recycler_id": str(recycler.id), "material_code": "plastico",
        "warehouse_id": str(warehouse.id), "kg": "10", "precio_kg": "100",
    }).json()
    r = client_as(citizen).patch(f"/weighings/{w['id']}/status", json={"status": "validado"})
    assert r.status_code == 403


@known_issue("1.3", "any authenticated user can create sales and drain inventory")
def test_citizen_cannot_create_sale(client_as, citizen, db, warehouse):
    factories.stock(db, warehouse, kg="100")
    r = client_as(citizen).post("/transactions", json={
        "material_code": "plastico", "warehouse_id": str(warehouse.id),
        "kg": "10", "precio_kg": "100",
    })
    assert r.status_code == 403


@known_issue("1.3", "any authenticated user can change inventory prices")
def test_citizen_cannot_change_inventory_prices(client_as, citizen, db, warehouse):
    item = factories.stock(db, warehouse)
    r = client_as(citizen).patch(f"/inventory/{item.id}", json={"precio_kg": "1"})
    assert r.status_code == 403


@known_issue("1.3", "a recycler can read every other recycler's weighings")
def test_recycler_only_sees_own_weighings(client_as, eca_admin, recycler, warehouse, db):
    other = factories.make_user(db, "recycler")
    admin = client_as(eca_admin)
    for r_ in (recycler, other):
        admin.post("/weighings", json={
            "recycler_id": str(r_.id), "material_code": "plastico",
            "warehouse_id": str(warehouse.id), "kg": "10", "precio_kg": "100",
        })
    body = client_as(recycler).get("/weighings").json()
    assert {w["recycler_id"] for w in body["items"]} == {str(recycler.id)}


@known_issue("1.3", "anonymous registration accepts privileged role_code (eca_admin)")
def test_anonymous_cannot_register_privileged_role(client):
    r = client.post("/auth/register", json={
        "user_type_code": "eca", "role_code": "eca_admin",
        "email": "atacante@test.com", "password": "Segura12345",
        "full_name": "Atacante", "id_type": "CC", "id_number": "66666666",
    })
    assert r.status_code in (401, 403, 422)


@known_issue("2.1", "verified recyclers get their national ID number as password")
def test_recycler_initial_password_is_not_their_id_number(client_as, client, association_admin, db):
    pending = factories.make_user(
        db, "recycler", verification_status=VerificationStatus.pending, password_hash="")
    client_as(association_admin).patch(
        f"/users/{pending.id}/verification-status", json={"status": "verified"})
    r = client.post("/auth/login", json={"email": pending.email, "password": pending.id_number})
    assert r.status_code == 401


@known_issue("2.6", "login has no rate limiting or lockout")
def test_login_is_rate_limited(client, citizen):
    codes = {
        client.post("/auth/login", json={"email": citizen.email, "password": "mala"}).status_code
        for _ in range(30)
    }
    assert 429 in codes


@known_issue("2.2", "any password is accepted, including one character")
def test_weak_password_is_rejected(client):
    r = client.post("/auth/register", json={
        "user_type_code": "citizen", "email": "debil@test.com", "password": "1",
        "full_name": "Débil", "id_type": "CC", "id_number": "12121212",
    })
    assert r.status_code == 422


@known_issue("2.2", "email is not validated as an email address")
def test_invalid_email_is_rejected(client):
    r = client.post("/auth/register", json={
        "user_type_code": "citizen", "email": "no-es-un-email", "password": "Segura12345",
        "full_name": "Test", "id_type": "CC", "id_number": "13131313",
    })
    assert r.status_code == 422
