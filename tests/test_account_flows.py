"""Account lifecycle: activation, email verification, password reset/change, input validation."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domains.auth.models import OneTimeToken
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from tests import factories
from tests.factories import DEFAULT_PASSWORD

NEW_PASSWORD = "Nueva-Clave-2026"


def token_from(message) -> str:
    return message.body.split("token=")[1].split()[0]


def _register(client, **overrides):
    payload = {
        "user_type_code": "citizen", "email": "ana@test.com", "password": DEFAULT_PASSWORD,
        "full_name": "Ana Gómez", "id_type": "CC", "id_number": "1098765432",
    }
    payload.update(overrides)
    return client.post("/auth/register", json=payload)


def _verified_recycler_token(client_as, association_admin, db, outbox):
    recycler = factories.make_user(
        db, "recycler", verification_status=VerificationStatus.pending, password_hash="")
    client_as(association_admin).patch(
        f"/users/{recycler.id}/verification-status", json={"status": "verified"})
    return recycler, token_from(outbox[-1])


# --- Registration sends a verification email ------------------------------------

class TestEmailVerification:
    def test_register_sends_a_verification_link(self, client, outbox):
        assert _register(client).status_code == 201
        assert [m.to for m in outbox] == ["ana@test.com"]
        assert "verify-email?token=" in outbox[0].body

    def test_recycler_registration_sends_nothing(self, client, outbox):
        r = client.post("/auth/register", json={
            "user_type_code": "recycler", "email": "rec@test.com", "full_name": "Rec Icla",
            "id_type": "CC", "id_number": "80234567"})
        assert r.status_code == 201
        assert outbox == []

    def test_link_confirms_the_email_once(self, client, db, outbox):
        user_id = _register(client).json()["id"]
        token = token_from(outbox[0])
        assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
        db.expire_all()
        assert db.get(User, user_id).email_verified_at is not None
        assert client.post("/auth/verify-email", json={"token": token}).status_code == 400

    def test_resend_replaces_the_previous_link(self, client, client_as, db, outbox):
        user_id = _register(client).json()["id"]
        first = token_from(outbox[0])
        user = db.get(User, user_id)
        assert client_as(user).post("/auth/resend-verification").status_code == 200
        second = token_from(outbox[1])

        assert client.post("/auth/verify-email", json={"token": first}).status_code == 400
        assert client.post("/auth/verify-email", json={"token": second}).status_code == 200

    def test_resend_does_nothing_when_already_verified(self, client_as, db, outbox):
        user = factories.make_user(db, "citizen", email_verified_at=datetime.now(timezone.utc))
        assert client_as(user).post("/auth/resend-verification").status_code == 200
        assert outbox == []


# --- Recycler activation -----------------------------------------------------------

class TestActivation:
    def test_activation_sets_the_password_and_confirms_the_email(
        self, client, client_as, association_admin, db, outbox
    ):
        recycler, token = _verified_recycler_token(client_as, association_admin, db, outbox)
        r = client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD})
        assert r.status_code == 200
        db.refresh(recycler)
        assert recycler.email_verified_at is not None
        assert client.post("/auth/login", json={
            "email": recycler.email, "password": NEW_PASSWORD}).status_code == 200

    def test_link_works_only_once(self, client, client_as, association_admin, db, outbox):
        _, token = _verified_recycler_token(client_as, association_admin, db, outbox)
        assert client.post("/auth/activate", json={
            "token": token, "password": NEW_PASSWORD}).status_code == 200
        again = client.post("/auth/activate", json={"token": token, "password": "Otra-Clave-2026"})  # gitleaks:allow  (fake test password)
        assert again.status_code == 400

    def test_weak_password_does_not_burn_the_link(
        self, client, client_as, association_admin, db, outbox
    ):
        _, token = _verified_recycler_token(client_as, association_admin, db, outbox)
        assert client.post("/auth/activate", json={
            "token": token, "password": "corta"}).status_code == 422
        assert client.post("/auth/activate", json={
            "token": token, "password": NEW_PASSWORD}).status_code == 200

    def test_expired_link_is_rejected(self, client, client_as, association_admin, db, outbox):
        _, token = _verified_recycler_token(client_as, association_admin, db, outbox)
        db.query(OneTimeToken).update(
            {OneTimeToken.expires_at: datetime.now(timezone.utc) - timedelta(minutes=1)})
        db.commit()
        assert client.post("/auth/activate", json={
            "token": token, "password": NEW_PASSWORD}).status_code == 400

    def test_garbage_token_is_rejected(self, client):
        r = client.post("/auth/activate", json={"token": "x" * 43, "password": NEW_PASSWORD})
        assert r.status_code == 400

    def test_a_token_of_another_purpose_is_rejected(self, client, outbox):
        _register(client)
        verification_token = token_from(outbox[0])
        r = client.post("/auth/activate", json={"token": verification_token, "password": NEW_PASSWORD})
        assert r.status_code == 400

    def test_tokens_are_stored_hashed(self, client_as, association_admin, db, outbox):
        _, token = _verified_recycler_token(client_as, association_admin, db, outbox)
        assert db.query(OneTimeToken).filter(OneTimeToken.token_hash == token).count() == 0
        assert db.query(OneTimeToken).count() == 1

    def test_reverifying_a_recycler_with_a_password_sends_no_new_link(
        self, client_as, association_admin, db, outbox
    ):
        recycler = factories.make_user(db, "recycler")  # already has a password
        client_as(association_admin).patch(
            f"/users/{recycler.id}/verification-status", json={"status": "verified"})
        assert outbox == []


# --- Forgot / reset / change password --------------------------------------------------

class TestPasswordReset:
    def test_full_flow(self, client, db, outbox):
        user = factories.make_user(db, "citizen")
        r = client.post("/auth/forgot-password", json={"email": user.email})
        assert r.status_code == 200
        assert "reset-password?token=" in outbox[0].body

        token = token_from(outbox[0])
        assert client.post("/auth/reset-password", json={
            "token": token, "password": NEW_PASSWORD}).status_code == 200
        assert client.post("/auth/login", json={
            "email": user.email, "password": NEW_PASSWORD}).status_code == 200
        assert client.post("/auth/login", json={
            "email": user.email, "password": DEFAULT_PASSWORD}).status_code == 401

    def test_link_works_only_once(self, client, db, outbox):
        user = factories.make_user(db, "citizen")
        client.post("/auth/forgot-password", json={"email": user.email})
        token = token_from(outbox[0])
        client.post("/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})
        assert client.post("/auth/reset-password", json={
            "token": token, "password": "Otra-Clave-2026"}).status_code == 400  # gitleaks:allow  (fake test password)

    def test_unknown_email_gets_the_same_answer_and_no_email(self, client, db, outbox):
        known = factories.make_user(db, "citizen")
        a = client.post("/auth/forgot-password", json={"email": known.email})
        b = client.post("/auth/forgot-password", json={"email": "nadie@test.com"})
        assert (a.status_code, a.json()) == (b.status_code, b.json())
        assert len(outbox) == 1

    def test_pending_recycler_gets_no_reset_email(self, client, db, outbox):
        pending = factories.make_user(
            db, "recycler", verification_status=VerificationStatus.pending, password_hash="")
        assert client.post("/auth/forgot-password", json={"email": pending.email}).status_code == 200
        assert outbox == []

    def test_inactive_account_gets_no_reset_email(self, client, db, outbox):
        user = factories.make_user(db, "citizen", is_active=False)
        client.post("/auth/forgot-password", json={"email": user.email})
        assert outbox == []

    def test_a_new_request_invalidates_the_previous_link(self, client, db, outbox):
        user = factories.make_user(db, "citizen")
        client.post("/auth/forgot-password", json={"email": user.email})
        client.post("/auth/forgot-password", json={"email": user.email})
        first, second = token_from(outbox[0]), token_from(outbox[1])
        assert client.post("/auth/reset-password", json={
            "token": first, "password": NEW_PASSWORD}).status_code == 400
        assert client.post("/auth/reset-password", json={
            "token": second, "password": NEW_PASSWORD}).status_code == 200

    def test_email_lookup_ignores_case(self, client, db, outbox):
        user = factories.make_user(db, "citizen")
        client.post("/auth/forgot-password", json={"email": user.email.upper()})
        assert len(outbox) == 1


class TestChangePassword:
    def test_changes_with_the_current_password(self, client, client_as, db):
        user = factories.make_user(db, "citizen")
        r = client_as(user).post("/auth/change-password", json={
            "current_password": DEFAULT_PASSWORD, "new_password": NEW_PASSWORD})
        assert r.status_code == 200
        assert client.post("/auth/login", json={
            "email": user.email, "password": NEW_PASSWORD}).status_code == 200

    def test_wrong_current_password(self, client_as, db):
        user = factories.make_user(db, "citizen")
        r = client_as(user).post("/auth/change-password", json={
            "current_password": "incorrecta", "new_password": NEW_PASSWORD})
        assert r.status_code == 400

    def test_new_password_must_meet_the_policy(self, client_as, db):
        user = factories.make_user(db, "citizen")
        r = client_as(user).post("/auth/change-password", json={
            "current_password": DEFAULT_PASSWORD, "new_password": "corta"})
        assert r.status_code == 422

    def test_requires_authentication(self, client):
        r = client.post("/auth/change-password", json={
            "current_password": "x", "new_password": NEW_PASSWORD})
        assert r.status_code in (401, 403)


# --- Input validation (2.2) --------------------------------------------------------------

class TestPasswordPolicy:
    @pytest.mark.parametrize("password", [
        "corta1", "1234567890", "aaaaaaaaaaaa", "password123", "x" * 80,
    ])
    def test_weak_passwords_are_rejected_on_register(self, client, password):
        assert _register(client, password=password).status_code == 422

    def test_a_reasonable_passphrase_is_accepted(self, client):
        assert _register(client, password="una frase larga y única").status_code == 201


class TestInputValidation:
    def test_invalid_email_is_rejected(self, client):
        assert _register(client, email="no-es-un-email").status_code == 422

    def test_email_is_normalized_to_lowercase(self, client):
        r = _register(client, email="Ana.Gomez@TEST.com")
        assert r.status_code == 201
        assert r.json()["email"] == "ana.gomez@test.com"

    def test_login_ignores_email_case(self, client, db):
        user = factories.make_user(db, "citizen")
        r = client.post("/auth/login", json={
            "email": user.email.upper(), "password": DEFAULT_PASSWORD})
        assert r.status_code == 200

    @pytest.mark.parametrize("field,value", [
        ("full_name", "x" * 300), ("phone", "1" * 40), ("id_number", "1" * 40),
        ("id_type", "X" * 20), ("latitude", 123.0), ("longitude", -500.0),
    ])
    def test_oversized_or_out_of_range_input_is_a_422_not_a_500(self, client, field, value):
        assert _register(client, **{field: value}).status_code == 422

    def test_profile_update_is_bounded_too(self, client_as, citizen):
        r = client_as(citizen).patch(f"/users/{citizen.id}", json={"phone": "1" * 40})
        assert r.status_code == 422

    def test_absurdly_long_login_password_is_rejected(self, client):
        r = client.post("/auth/login", json={"email": "a@test.com", "password": "x" * 500})
        assert r.status_code == 422


# --- Deactivated accounts -------------------------------------------------------------------

class TestInactiveAccounts:
    def test_cannot_log_in(self, client, db):
        user = factories.make_user(db, "citizen", is_active=False)
        r = client.post("/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD})
        assert r.status_code == 403

    def test_existing_token_stops_working(self, client_as, db):
        user = factories.make_user(db, "citizen")
        c = client_as(user)
        assert c.get(f"/users/{user.id}").status_code == 200
        user.is_active = False
        db.commit()
        assert c.get(f"/users/{user.id}").status_code == 401
