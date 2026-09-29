"""Known security gaps, written as the *desired* behavior.

Each test is `xfail(strict=True)`: it fails today because the hole exists. When
the fix lands the test starts passing, strict mode turns that into a failure,
and the fixer must delete the marker (or promote the test to a regular suite). Task IDs refer to the hardening plan.
"""
import pytest

from app.domains.users.enums import VerificationStatus
from tests import factories


def known_issue(task: str, why: str):
    return pytest.mark.xfail(strict=True, reason=f"[{task}] {why}")


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
