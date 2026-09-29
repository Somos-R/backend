"""The backoffice sign-in: password, then a mandatory second factor, with its own token audience."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import mfa
from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.security import BACKOFFICE, BACKOFFICE_MFA, create_access_token
from app.domains.audit.models import AuditLog
from app.domains.auth.models import MfaCredential, RecoveryCode, RefreshToken
from app.main import app
from tests import factories

PASSWORD = factories.DEFAULT_PASSWORD


class Clock:
    """A controllable clock for the TOTP checks (codes are only valid for their own 30 seconds)."""

    def __init__(self):
        self.now = 1_800_000_000.0

    def advance(self, seconds=30):
        self.now += seconds

    def code(self, secret, offset=0):
        return mfa._code_at(secret, mfa.current_step(self.now) + offset)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(mfa.time, "time", lambda: c.now)
    return c


@pytest.fixture
def admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


@pytest.fixture
def other_admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


def _login(client, user, password=PASSWORD):
    return client.post("/admin/auth/login", json={"email": user.email, "password": password})


def _challenge(client, user):
    r = _login(client, user)
    assert r.status_code == 200, r.text
    return r.json()["mfa_token"]


def _enroll(client, user, clock):
    """Full first sign-in. Returns (secret, session body)."""
    token = _challenge(client, user)
    secret = client.post("/admin/auth/mfa/enroll", json={"mfa_token": token}).json()["secret"]
    r = client.post("/admin/auth/mfa/enroll/confirm", json={"mfa_token": token, "code": clock.code(secret)})
    assert r.status_code == 200, r.text
    return secret, r.json()


def _signin(client, user, secret, clock):
    """A later sign-in with the authenticator. Moves the clock so the code is a new one."""
    clock.advance()
    token = _challenge(client, user)
    r = client.post("/admin/auth/mfa/verify", json={"mfa_token": token, "code": clock.code(secret)})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _actions(db, user):
    rows = db.scalars(select(AuditLog).where(AuditLog.target_id == str(user.id)).order_by(AuditLog.occurred_at)).all()
    return [(r.action, r.outcome, r.details) for r in rows]


class TestPasswordStep:
    def test_a_first_sign_in_must_enroll_a_second_factor(self, client, admin):
        r = _login(client, admin)
        assert r.status_code == 200
        body = r.json()
        assert body["mfa_status"] == "enrollment_required" and body["expires_in"] == settings.mfa_challenge_minutes * 60
        assert "access_token" not in body and "refresh_token" not in body

    def test_after_enrolling_the_code_is_asked_for(self, client, admin, clock):
        _enroll(client, admin, clock)
        assert _login(client, admin).json()["mfa_status"] == "code_required"

    def test_the_password_alone_never_opens_the_backoffice(self, client, admin):
        token = _challenge(client, admin)
        assert client.get("/admin/me", headers=_bearer(token)).status_code == 401

    def test_wrong_password_unknown_account_and_customers_answer_identically(self, client, admin, eca_admin):
        wrong = _login(client, admin, "incorrecta-1234")
        unknown = client.post("/admin/auth/login", json={"email": "nadie@test.com", "password": "x" * 12})
        customer = _login(client, eca_admin)  # correct password, but not a Somos R account
        assert wrong.status_code == unknown.status_code == customer.status_code == 401
        assert wrong.json() == unknown.json() == customer.json()
        assert wrong.json()["code"] == "invalid_credentials"

    def test_the_real_reasons_are_in_the_audit_trail(self, client, db, admin, eca_admin):
        _login(client, admin, "incorrecta-1234")
        _login(client, eca_admin)
        assert ("admin.login_failed", "failure", {"reason": "bad_password"}) in _actions(db, admin)
        reasons = [d["reason"] for a, o, d in db.execute(
            select(AuditLog.action, AuditLog.outcome, AuditLog.details).where(AuditLog.action == "admin.login_failed")
        ).all() if d]
        assert "not_platform" in reasons

    def test_repeated_failures_lock_the_account(self, client, db, admin):
        for _ in range(settings.login_max_attempts):
            _login(client, admin, "incorrecta-1234")
        locked = _login(client, admin)  # even the right password
        assert locked.status_code == 401 and locked.json()["code"] == "invalid_credentials"
        db.refresh(admin)
        assert admin.locked_until is not None

    def test_a_deactivated_account_cannot_sign_in(self, client, db, admin):
        admin.is_active = False
        db.commit()
        assert _login(client, admin).json()["code"] == "invalid_credentials"


class TestEnrollment:
    def test_returns_a_secret_and_the_link_for_the_authenticator(self, client, admin):
        token = _challenge(client, admin)
        body = client.post("/admin/auth/mfa/enroll", json={"mfa_token": token}).json()
        assert body["otpauth_uri"].startswith("otpauth://totp/") and body["secret"] in body["otpauth_uri"]

    def test_the_secret_is_stored_encrypted(self, client, db, admin):
        token = _challenge(client, admin)
        secret = client.post("/admin/auth/mfa/enroll", json={"mfa_token": token}).json()["secret"]
        stored = db.get(MfaCredential, admin.id).secret_encrypted
        assert secret not in stored and mfa.decrypt_secret(stored) == secret

    def test_confirming_opens_the_session_and_shows_ten_recovery_codes_once(self, client, db, admin, clock):
        secret, session = _enroll(client, admin, clock)
        assert len(session["recovery_codes"]) == 10 and session["access_token"] and session["refresh_token"]
        assert db.get(MfaCredential, admin.id).enabled_at is not None
        stored = db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == admin.id)).all()
        assert len(stored) == 10
        assert not {c.code_hash for c in stored} & set(session["recovery_codes"])  # only hashes are kept

    def test_a_wrong_code_does_not_enroll_and_counts_as_a_failure(self, client, db, admin, clock):
        token = _challenge(client, admin)
        client.post("/admin/auth/mfa/enroll", json={"mfa_token": token})
        r = client.post("/admin/auth/mfa/enroll/confirm", json={"mfa_token": token, "code": "000000"})
        assert r.status_code == 401 and r.json()["code"] == "invalid_mfa_code"
        assert db.get(MfaCredential, admin.id).enabled_at is None
        db.refresh(admin)
        assert admin.failed_login_attempts == 1

    def test_confirming_without_asking_for_a_secret_is_refused(self, client, admin):
        r = client.post("/admin/auth/mfa/enroll/confirm", json={"mfa_token": _challenge(client, admin), "code": "123456"})
        assert r.status_code == 400 and r.json()["code"] == "mfa_not_started"

    def test_asking_again_before_confirming_starts_over(self, client, admin, clock):
        token = _challenge(client, admin)
        first = client.post("/admin/auth/mfa/enroll", json={"mfa_token": token}).json()["secret"]
        second = client.post("/admin/auth/mfa/enroll", json={"mfa_token": token}).json()["secret"]
        assert first != second
        stale = client.post("/admin/auth/mfa/enroll/confirm", json={"mfa_token": token, "code": clock.code(first)})
        assert stale.status_code == 401

    def test_it_cannot_be_redone_once_enabled(self, client, admin, clock):
        _enroll(client, admin, clock)
        r = client.post("/admin/auth/mfa/enroll", json={"mfa_token": _challenge(client, admin)})
        assert r.status_code == 409 and r.json()["code"] == "mfa_already_enrolled"

    def test_enrollment_is_audited(self, client, db, admin, clock):
        _enroll(client, admin, clock)
        assert [a for a, _, _ in _actions(db, admin)] == ["admin.mfa_enrolled", "admin.login"]


class TestSecondFactorStep:
    def test_a_correct_code_opens_the_session(self, client, admin, clock):
        secret, _ = _enroll(client, admin, clock)
        session = _signin(client, admin, secret, clock)
        assert session["expires_in"] == settings.backoffice_access_token_minutes * 60
        assert client.get("/admin/me", headers=_bearer(session["access_token"])).status_code == 200

    def test_a_wrong_code_is_refused_and_counts_towards_the_lockout(self, client, db, admin, clock):
        secret, _ = _enroll(client, admin, clock)
        token = _challenge(client, admin)
        for _ in range(settings.login_max_attempts):
            r = client.post("/admin/auth/mfa/verify", json={"mfa_token": token, "code": "000000"})
            assert r.status_code == 401 and r.json()["code"] == "invalid_mfa_code"
        # locked now: not even the right code gets in
        clock.advance()
        r = client.post("/admin/auth/mfa/verify", json={"mfa_token": token, "code": clock.code(secret)})
        assert r.status_code == 401
        db.refresh(admin)
        assert admin.locked_until is not None

    def test_a_code_cannot_be_used_twice(self, client, admin, clock):
        secret, _ = _enroll(client, admin, clock)
        clock.advance()
        code = clock.code(secret)
        first = client.post("/admin/auth/mfa/verify", json={"mfa_token": _challenge(client, admin), "code": code})
        second = client.post("/admin/auth/mfa/verify", json={"mfa_token": _challenge(client, admin), "code": code})
        assert first.status_code == 200 and second.status_code == 401

    def test_the_challenge_token_is_single_use(self, client, admin, clock):
        secret, _ = _enroll(client, admin, clock)
        clock.advance()
        token = _challenge(client, admin)
        assert client.post("/admin/auth/mfa/verify", json={"mfa_token": token, "code": clock.code(secret)}).status_code == 200
        clock.advance()
        again = client.post("/admin/auth/mfa/verify", json={"mfa_token": token, "code": clock.code(secret)})
        assert again.status_code == 401 and again.json()["code"] == "mfa_session_expired"

    def test_an_expired_challenge_is_refused(self, client, admin, clock):
        secret, _ = _enroll(client, admin, clock)
        token = create_access_token({"sub": str(admin.id), "tv": admin.token_version}, audience=BACKOFFICE_MFA, minutes=-1)
        r = client.post("/admin/auth/mfa/verify", json={"mfa_token": token, "code": clock.code(secret)})
        assert r.status_code == 401 and r.json()["code"] == "mfa_session_expired"

    def test_a_password_change_invalidates_a_pending_challenge(self, client, db, admin, clock):
        secret, _ = _enroll(client, admin, clock)
        clock.advance()
        token = _challenge(client, admin)
        admin.token_version += 1
        db.commit()
        r = client.post("/admin/auth/mfa/verify", json={"mfa_token": token, "code": clock.code(secret)})
        assert r.status_code == 401 and r.json()["code"] == "mfa_session_expired"

    @pytest.mark.parametrize("body", [{}, {"code": "123456", "recovery_code": "abcde-fghjk"}])
    def test_exactly_one_of_code_or_recovery_code(self, client, admin, body):
        r = client.post("/admin/auth/mfa/verify", json={"mfa_token": "x", **body})
        assert r.status_code == 422

    def test_verifying_before_enrolling_is_refused(self, client, admin):
        r = client.post("/admin/auth/mfa/verify", json={"mfa_token": _challenge(client, admin), "code": "123456"})
        assert r.status_code == 400 and r.json()["code"] == "mfa_not_enrolled"

    def test_failures_are_audited(self, client, db, admin, clock):
        _enroll(client, admin, clock)
        client.post("/admin/auth/mfa/verify", json={"mfa_token": _challenge(client, admin), "code": "000000"})
        assert ("admin.mfa_failed", "failure", {"reason": "bad_code"}) in _actions(db, admin)


class TestRecoveryCodes:
    def test_a_recovery_code_replaces_the_authenticator_once(self, client, db, admin, clock):
        _, session = _enroll(client, admin, clock)
        code = session["recovery_codes"][0]
        r = client.post("/admin/auth/mfa/verify", json={"mfa_token": _challenge(client, admin), "recovery_code": code.upper()})
        assert r.status_code == 200 and r.json()["recovery_codes_remaining"] == 9
        again = client.post("/admin/auth/mfa/verify", json={"mfa_token": _challenge(client, admin), "recovery_code": code})
        assert again.status_code == 401
        assert "admin.recovery_code_used" in [a for a, _, _ in _actions(db, admin)]

    def test_a_wrong_recovery_code_is_refused(self, client, admin, clock):
        _enroll(client, admin, clock)
        r = client.post("/admin/auth/mfa/verify", json={"mfa_token": _challenge(client, admin), "recovery_code": "aaaaa-bbbbb"})
        assert r.status_code == 401

    def test_regenerating_needs_a_fresh_code_and_retires_the_old_ones(self, client, admin, clock):
        secret, session = _enroll(client, admin, clock)
        clock.advance()
        headers = _bearer(session["access_token"])
        assert client.post("/admin/auth/mfa/recovery-codes", json={"code": "000000"}, headers=headers).status_code == 401
        r = client.post("/admin/auth/mfa/recovery-codes", json={"code": clock.code(secret)}, headers=headers)
        assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
        old = client.post("/admin/auth/mfa/verify", json={
            "mfa_token": _challenge(client, admin), "recovery_code": session["recovery_codes"][1]})
        assert old.status_code == 401
        new = client.post("/admin/auth/mfa/verify", json={
            "mfa_token": _challenge(client, admin), "recovery_code": r.json()["recovery_codes"][0]})
        assert new.status_code == 200


class TestAudienceSeparation:
    def test_a_backoffice_token_is_useless_in_the_customer_api(self, client, admin, clock):
        _, session = _enroll(client, admin, clock)
        headers = _bearer(session["access_token"])
        for path in ("/auth/me", "/users", f"/users/{admin.id}", "/inventory"):
            r = client.get(path, headers=headers)
            assert r.status_code == 401 and r.json()["code"] == "invalid_token", path

    def test_a_customer_token_is_useless_in_the_backoffice(self, client, eca_admin, admin):
        assert client.get("/admin/me", headers=factories.auth_headers(eca_admin)).status_code == 401
        assert client.get("/admin/me", headers=factories.auth_headers(admin)).status_code == 401  # even a platform user's

    def test_a_backoffice_token_of_a_customer_account_is_refused(self, client, eca_admin):
        forged = create_access_token({"sub": str(eca_admin.id), "tv": 0}, audience=BACKOFFICE)
        assert client.get("/admin/me", headers=_bearer(forged)).status_code == 401

    def test_the_challenge_token_is_not_a_session(self, client, admin):
        token = _challenge(client, admin)
        assert client.get("/admin/me", headers=_bearer(token)).status_code == 401

    def test_the_backoffice_token_says_who_it_is_for(self, client, admin, clock):
        _, session = _enroll(client, admin, clock)
        claims = jwt.decode(session["access_token"], settings.secret_key, algorithms=["HS256"], audience=BACKOFFICE)
        assert claims["aud"] == "backoffice" and claims["user_type"] == "platform" and claims["role"] == "platform_admin"

    def test_refresh_tokens_only_renew_their_own_audience(self, client, db, admin, eca_admin, clock):
        _, session = _enroll(client, admin, clock)
        assert client.post("/auth/refresh", json={"refresh_token": session["refresh_token"]}).status_code == 401
        portal = client.post("/auth/login", json={"email": eca_admin.email, "password": PASSWORD}).json()
        assert client.post("/admin/auth/refresh", json={"refresh_token": portal["refresh_token"]}).status_code == 401


class TestSession:
    def test_the_backoffice_session_is_shorter_than_a_customers(self, client, db, admin, clock):
        _, session = _enroll(client, admin, clock)
        row = db.scalars(select(RefreshToken).where(RefreshToken.user_id == admin.id)).one()
        assert row.audience == "backoffice"
        hours = (row.expires_at - datetime.now(timezone.utc)).total_seconds() / 3600
        assert hours <= settings.backoffice_refresh_token_hours
        assert settings.backoffice_access_token_minutes < settings.access_token_expire_minutes
        assert session["expires_in"] < settings.access_token_expire_minutes * 60

    def test_refresh_rotates_and_a_reused_token_kills_the_family(self, client, admin, clock):
        _, session = _enroll(client, admin, clock)
        first = client.post("/admin/auth/refresh", json={"refresh_token": session["refresh_token"]})
        assert first.status_code == 200 and first.json()["refresh_token"] != session["refresh_token"]
        assert client.get("/admin/me", headers=_bearer(first.json()["access_token"])).status_code == 200
        assert client.post("/admin/auth/refresh", json={"refresh_token": session["refresh_token"]}).status_code == 401
        assert client.post("/admin/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}).status_code == 401

    def test_logout_ends_the_access_and_refresh_tokens(self, client, db, admin, clock):
        _, session = _enroll(client, admin, clock)
        headers = _bearer(session["access_token"])
        assert client.post("/admin/auth/logout", headers=headers).status_code == 200
        assert client.get("/admin/me", headers=headers).status_code == 401
        assert client.post("/admin/auth/refresh", json={"refresh_token": session["refresh_token"]}).status_code == 401
        assert "admin.logout" in [a for a, _, _ in _actions(db, admin)]

    def test_a_deactivated_account_loses_its_session(self, client, db, admin, clock):
        _, session = _enroll(client, admin, clock)
        admin.is_active = False
        db.commit()
        assert client.get("/admin/me", headers=_bearer(session["access_token"])).status_code == 401
        assert client.post("/admin/auth/refresh", json={"refresh_token": session["refresh_token"]}).status_code == 401

    def test_me_reports_the_capabilities_and_the_state_of_the_second_factor(self, client, admin, clock):
        _, session = _enroll(client, admin, clock)
        body = client.get("/admin/me", headers=_bearer(session["access_token"])).json()
        assert body["id"] == str(admin.id) and body["role_code"] == "platform_admin"
        assert body["capabilities"] == ["audit.read", "catalogs.manage", "organizations.review", "users.manage"]
        assert body["mfa_enabled"] is True and body["recovery_codes_remaining"] == 10


class TestResettingSomeoneElsesSecondFactor:
    def test_the_other_person_can_reset_it(self, client, db, admin, other_admin, clock):
        secret_a, session_a = _enroll(client, admin, clock)
        clock.advance()
        secret_b, session_b = _enroll(client, other_admin, clock)
        r = client.post(f"/admin/users/{other_admin.id}/mfa/reset", headers=_bearer(session_a["access_token"]))
        assert r.status_code == 200
        assert db.get(MfaCredential, other_admin.id) is None
        assert db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == other_admin.id)).all() == []
        assert client.get("/admin/me", headers=_bearer(session_b["access_token"])).status_code == 401  # sessions ended
        assert _login(client, other_admin).json()["mfa_status"] == "enrollment_required"
        assert ("admin.mfa_reset", "success", {}) in _actions(db, other_admin)
        assert secret_a and secret_b

    def test_nobody_resets_their_own(self, client, admin, clock):
        _, session = _enroll(client, admin, clock)
        r = client.post(f"/admin/users/{admin.id}/mfa/reset", headers=_bearer(session["access_token"]))
        assert r.status_code == 403 and r.json()["code"] == "cannot_reset_own_mfa"

    def test_only_somos_r_accounts_can_be_the_target(self, client, admin, eca_admin, clock):
        _, session = _enroll(client, admin, clock)
        r = client.post(f"/admin/users/{eca_admin.id}/mfa/reset", headers=_bearer(session["access_token"]))
        assert r.status_code == 404

    def test_a_customer_cannot_use_it(self, client, eca_admin, admin):
        r = client.post(f"/admin/users/{admin.id}/mfa/reset", headers=factories.auth_headers(eca_admin))
        assert r.status_code == 401


class TestNetworkRestriction:
    def _from(self, host):
        return TestClient(app, client=(host, 50000))

    def test_open_when_no_networks_are_configured(self, client, admin):
        assert _login(self._from("198.51.100.9"), admin).status_code == 200

    def test_only_the_listed_networks_get_in(self, client, monkeypatch, admin):
        monkeypatch.setattr(settings, "admin_allowed_cidrs", "203.0.113.0/24, 198.51.100.7/32")
        assert _login(self._from("203.0.113.55"), admin).status_code == 200
        assert _login(self._from("198.51.100.7"), admin).status_code == 200
        denied = _login(self._from("198.51.100.8"), admin)
        assert denied.status_code == 403 and denied.json()["code"] == "admin_network_denied"

    def test_every_admin_route_is_covered(self, client, monkeypatch, admin):
        monkeypatch.setattr(settings, "admin_allowed_cidrs", "203.0.113.0/24")
        outside = self._from("192.0.2.1")
        for method, path in [("post", "/admin/auth/login"), ("get", "/admin/me"),
                             ("post", "/admin/auth/refresh"), ("post", f"/admin/users/{admin.id}/mfa/reset")]:
            assert outside.request(method.upper(), path).status_code == 403, path

    def test_the_customer_api_is_not_affected(self, client, monkeypatch):
        monkeypatch.setattr(settings, "admin_allowed_cidrs", "203.0.113.0/24")
        assert self._from("192.0.2.1").get("/health/live").status_code == 200


class TestStricterLimits:
    @pytest.fixture
    def limited(self, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_admin_auth", "3/minute")
        limiter.reset()
        yield
        limiter.reset()

    def test_login_is_limited_per_address(self, client, admin, limited):
        statuses = [_login(client, admin, "incorrecta-1234").status_code for _ in range(5)]
        assert statuses == [401, 401, 401, 429, 429]

    def test_the_second_factor_step_is_limited_too(self, client, limited):
        statuses = [client.post("/admin/auth/mfa/verify", json={"mfa_token": "x", "code": "123456"}).status_code
                    for _ in range(5)]
        assert statuses == [401, 401, 401, 429, 429]

    def test_the_defaults_are_stricter_than_the_customer_login(self):
        assert settings.rate_limit_admin_auth == "5/minute" and settings.rate_limit_login == "10/minute"


def test_the_challenge_lifetime_is_short():
    assert settings.mfa_challenge_minutes <= 5
    token = create_access_token({"sub": "x"}, audience=BACKOFFICE_MFA, minutes=settings.mfa_challenge_minutes)
    claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"], audience=BACKOFFICE_MFA)
    assert claims["exp"] - claims["iat"] <= timedelta(minutes=5).total_seconds()


class TestLastResortScript:
    def test_it_removes_the_second_factor_and_ends_the_sessions(self, client, db, admin, clock):
        from scripts.reset_platform_mfa import reset_platform_mfa

        _, session = _enroll(client, admin, clock)
        reset_platform_mfa(db, admin.email.upper())
        assert db.get(MfaCredential, admin.id) is None
        assert client.get("/admin/me", headers=_bearer(session["access_token"])).status_code == 401
        assert _login(client, admin).json()["mfa_status"] == "enrollment_required"
        assert ("admin.mfa_reset", "success", {"via": "script"}) in _actions(db, admin)

    def test_it_refuses_accounts_that_are_not_somos_r(self, db, eca_admin):
        from scripts.reset_platform_mfa import reset_platform_mfa

        with pytest.raises(ValueError):
            reset_platform_mfa(db, eca_admin.email)
        with pytest.raises(ValueError):
            reset_platform_mfa(db, "nadie@test.com")
