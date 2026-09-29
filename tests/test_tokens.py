"""JWT hardening, SECRET_KEY validation, refresh tokens, session revocation, cleanup, query count
(tasks 2.9 - 2.12)."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from pydantic import ValidationError
from sqlalchemy import event

from app.core.config import Settings, settings
from app.domains.auth.maintenance import purge_expired
from app.domains.auth.models import OneTimeToken, RefreshToken, RevokedToken
from app.domains.users.enums import VerificationStatus
from tests import factories
from tests.factories import DEFAULT_PASSWORD

STRONG_KEY = "k" * 48


def _login(client, user):
    r = client.post("/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(access):
    return {"Authorization": f"Bearer {access}"}


def _me(client, user, access):
    return client.get(f"/users/{user.id}", headers=_bearer(access))


def _forge(claims, key=None, algorithm="HS256"):
    return jwt.encode(claims, key or settings.secret_key, algorithm=algorithm)


def _claims(user, **overrides):
    now = datetime.now(timezone.utc)
    claims = {"sub": str(user.id), "jti": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=5),
              "aud": "portal"}
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


# --- 2.9 JWT hardening ------------------------------------------------------------------

class TestAccessTokenValidation:
    def test_a_well_formed_token_is_accepted(self, client, db):
        user = factories.make_user(db, "citizen")
        assert _me(client, user, _forge(_claims(user))).status_code == 200

    @pytest.mark.parametrize("missing", ["exp", "iat", "sub", "jti", "aud"])
    def test_tokens_missing_a_required_claim_are_rejected(self, client, db, missing):
        user = factories.make_user(db, "citizen")
        token = _forge(_claims(user, **{missing: None}))
        assert _me(client, user, token).status_code == 401

    def test_expired_token_is_rejected(self, client, db):
        user = factories.make_user(db, "citizen")
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert _me(client, user, _forge(_claims(user, exp=past))).status_code == 401

    def test_token_signed_with_another_key_is_rejected(self, client, db):
        user = factories.make_user(db, "citizen")
        assert _me(client, user, _forge(_claims(user), key="otra-clave" * 5)).status_code == 401

    def test_token_using_another_algorithm_is_rejected(self, client, db):
        user = factories.make_user(db, "citizen")
        token = _forge(_claims(user), key=settings.secret_key, algorithm="HS384")
        assert _me(client, user, token).status_code == 401

    def test_unsigned_alg_none_token_is_rejected(self, client, db):
        user = factories.make_user(db, "citizen")
        token = jwt.encode(_claims(user), key=None, algorithm="none")
        assert _me(client, user, token).status_code == 401

    def test_non_uuid_subject_is_a_401_not_a_500(self, client, db):
        user = factories.make_user(db, "citizen")
        token = _forge(_claims(user, sub="no-es-un-uuid"))
        assert _me(client, user, token).status_code == 401


class TestSecretKeyValidation:
    @staticmethod
    def _settings(**kwargs):
        return Settings(_env_file=None, database_url="postgresql://x", **kwargs)

    @pytest.mark.parametrize("env", ["staging", "prod"])
    def test_short_key_is_refused_outside_dev(self, env):
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            self._settings(secret_key="corta", app_env=env)

    @pytest.mark.parametrize("placeholder", ["dev-secret-key-change-in-production", "changeme"])
    def test_placeholder_key_is_refused_in_prod(self, placeholder):
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            self._settings(secret_key=placeholder, app_env="prod")

    def test_a_strong_key_is_accepted_in_prod(self):
        assert self._settings(secret_key=STRONG_KEY, app_env="prod").app_env == "prod"

    def test_dev_only_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            self._settings(secret_key="corta", app_env="dev")
        assert "SECRET_KEY is weak" in caplog.text

    @pytest.mark.parametrize("algorithm", ["none", "RS256", "ES256"])
    def test_only_hmac_algorithms_are_allowed(self, algorithm):
        with pytest.raises(ValidationError, match="ALGORITHM"):
            self._settings(secret_key=STRONG_KEY, algorithm=algorithm)


# --- 2.10 refresh tokens --------------------------------------------------------------------

class TestRefreshTokens:
    def test_login_returns_a_token_pair(self, client, db):
        body = _login(client, factories.make_user(db, "citizen"))
        assert body["refresh_token"] and body["access_token"]
        assert body["expires_in"] == settings.access_token_expire_minutes * 60

    def test_refresh_returns_a_working_new_pair(self, client, db):
        user = factories.make_user(db, "citizen")
        first = _login(client, user)
        r = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
        assert r.status_code == 200
        second = r.json()
        assert second["refresh_token"] != first["refresh_token"]
        assert _me(client, user, second["access_token"]).status_code == 200

    def test_the_chain_can_keep_rotating(self, client, db):
        current = _login(client, factories.make_user(db, "citizen"))["refresh_token"]
        for _ in range(3):
            r = client.post("/auth/refresh", json={"refresh_token": current})
            assert r.status_code == 200
            current = r.json()["refresh_token"]

    def test_reusing_a_rotated_token_revokes_the_whole_family(self, client, db):
        user = factories.make_user(db, "citizen")
        first = _login(client, user)["refresh_token"]
        newest = client.post("/auth/refresh", json={"refresh_token": first}).json()["refresh_token"]

        replay = client.post("/auth/refresh", json={"refresh_token": first})
        assert replay.status_code == 401
        # The legitimate holder of the newest token is cut off too: the family is compromised.
        assert client.post("/auth/refresh", json={"refresh_token": newest}).status_code == 401

    def test_another_login_is_not_affected_by_the_replay(self, client, db):
        user = factories.make_user(db, "citizen")
        phone = _login(client, user)
        laptop = _login(client, user)
        client.post("/auth/refresh", json={"refresh_token": phone["refresh_token"]})
        client.post("/auth/refresh", json={"refresh_token": phone["refresh_token"]})  # replay

        assert client.post("/auth/refresh", json={
            "refresh_token": laptop["refresh_token"]}).status_code == 200

    def test_unknown_token_is_rejected(self, client):
        assert client.post("/auth/refresh", json={"refresh_token": "x" * 64}).status_code == 401

    def test_expired_token_is_rejected(self, client, db):
        user = factories.make_user(db, "citizen")
        token = _login(client, user)["refresh_token"]
        db.query(RefreshToken).update(
            {RefreshToken.expires_at: datetime.now(timezone.utc) - timedelta(minutes=1)})
        db.commit()
        assert client.post("/auth/refresh", json={"refresh_token": token}).status_code == 401

    def test_only_the_hash_is_stored(self, client, db):
        token = _login(client, factories.make_user(db, "citizen"))["refresh_token"]
        assert db.query(RefreshToken).filter(RefreshToken.token_hash == token).count() == 0
        assert db.query(RefreshToken).count() == 1

    def test_a_deactivated_account_cannot_refresh(self, client, db):
        user = factories.make_user(db, "citizen")
        token = _login(client, user)["refresh_token"]
        user.is_active = False
        db.commit()
        assert client.post("/auth/refresh", json={"refresh_token": token}).status_code == 401

    def test_a_recycler_who_lost_verification_cannot_refresh(self, client, db):
        user = factories.make_user(db, "recycler")
        token = _login(client, user)["refresh_token"]
        user.verification_status = VerificationStatus.rejected
        db.commit()
        assert client.post("/auth/refresh", json={"refresh_token": token}).status_code == 401

    def test_refreshed_access_token_carries_the_current_role(self, client, db):
        staff = factories.make_actor(db, "eca_operator")
        token = _login(client, staff)["refresh_token"]
        staff.role_code = "eca_warehouse"
        db.commit()

        access = client.post("/auth/refresh", json={"refresh_token": token}).json()["access_token"]
        claims = jwt.decode(access, settings.secret_key, algorithms=["HS256"], audience="portal")
        assert claims["role"] == "eca_warehouse"


class TestLogoutEndsTheWholeSession:
    def test_logout_revokes_the_access_and_refresh_tokens(self, client, db):
        user = factories.make_user(db, "citizen")
        pair = _login(client, user)
        assert client.post("/auth/logout", headers=_bearer(pair["access_token"])).status_code == 200

        assert _me(client, user, pair["access_token"]).status_code == 401
        assert client.post("/auth/refresh", json={
            "refresh_token": pair["refresh_token"]}).status_code == 401

    def test_logout_of_one_device_keeps_the_others(self, client, db):
        user = factories.make_user(db, "citizen")
        phone, laptop = _login(client, user), _login(client, user)
        client.post("/auth/logout", headers=_bearer(phone["access_token"]))
        assert _me(client, user, laptop["access_token"]).status_code == 200
        assert client.post("/auth/refresh", json={
            "refresh_token": laptop["refresh_token"]}).status_code == 200

    def test_logout_works_for_tokens_without_a_session_family(self, client_as, db):
        user = factories.make_user(db, "citizen")  # client_as forges a token with no `fid`
        assert client_as(user).post("/auth/logout").status_code == 200


class TestPasswordChangeEndsSessions:
    def test_change_password_invalidates_every_session(self, client, db):
        user = factories.make_user(db, "citizen")
        stolen = _login(client, user)
        mine = _login(client, user)

        r = client.post("/auth/change-password", headers=_bearer(mine["access_token"]), json={
            "current_password": DEFAULT_PASSWORD, "new_password": "Nueva-Clave-2026"})
        assert r.status_code == 200

        for pair in (stolen, mine):
            assert _me(client, user, pair["access_token"]).status_code == 401
            assert client.post("/auth/refresh", json={
                "refresh_token": pair["refresh_token"]}).status_code == 401

        relogin = client.post("/auth/login", json={
            "email": user.email, "password": "Nueva-Clave-2026"})
        assert relogin.status_code == 200
        assert _me(client, user, relogin.json()["access_token"]).status_code == 200

    def test_password_reset_invalidates_every_session(self, client, db, outbox):
        user = factories.make_user(db, "citizen")
        pair = _login(client, user)
        client.post("/auth/forgot-password", json={"email": user.email})
        token = outbox[0].body.split("token=")[1].split()[0]
        client.post("/auth/reset-password", json={"token": token, "password": "Nueva-Clave-2026"})

        assert _me(client, user, pair["access_token"]).status_code == 401
        assert client.post("/auth/refresh", json={
            "refresh_token": pair["refresh_token"]}).status_code == 401

    def test_tokens_issued_before_the_feature_still_work(self, client, db):
        user = factories.make_user(db, "citizen")
        legacy = _forge(_claims(user))  # no `tv` claim, like tokens minted before this release
        assert _me(client, user, legacy).status_code == 200


# --- 2.11 cleanup ---------------------------------------------------------------------------

class TestPurge:
    def test_removes_only_what_can_no_longer_matter(self, db):
        user = factories.make_user(db, "citizen")
        now = datetime.now(timezone.utc)
        old, recent, future = now - timedelta(days=30), now - timedelta(days=1), now + timedelta(days=1)

        db.add_all([
            RevokedToken(jti="expired-jwt", expires_at=recent),
            RevokedToken(jti="live-jwt", expires_at=future),
            OneTimeToken(user_id=user.id, purpose="verify_email", token_hash="a" * 64, expires_at=old),
            OneTimeToken(user_id=user.id, purpose="verify_email", token_hash="b" * 64, expires_at=future,
                         used_at=old),
            OneTimeToken(user_id=user.id, purpose="verify_email", token_hash="c" * 64, expires_at=future),
            RefreshToken(user_id=user.id, family_id=uuid.uuid4(), token_hash="d" * 64, expires_at=old),
            RefreshToken(user_id=user.id, family_id=uuid.uuid4(), token_hash="e" * 64, expires_at=recent),
            RefreshToken(user_id=user.id, family_id=uuid.uuid4(), token_hash="f" * 64, expires_at=future,
                         used_at=recent),
        ])
        db.commit()

        assert purge_expired(db) == {"revoked_tokens": 1, "one_time_tokens": 2, "refresh_tokens": 1}
        assert {t.jti for t in db.query(RevokedToken)} == {"live-jwt"}
        assert {t.token_hash[0] for t in db.query(OneTimeToken)} == {"c"}
        assert {t.token_hash[0] for t in db.query(RefreshToken)} == {"e", "f"}

    def test_is_safe_to_run_on_empty_tables(self, db):
        assert purge_expired(db) == {"revoked_tokens": 0, "one_time_tokens": 0, "refresh_tokens": 0}


# --- 2.12 one query per authenticated request -----------------------------------------------

def test_authentication_costs_a_single_query(client, db):
    user = factories.make_user(db, "citizen")
    access = _login(client, user)["access_token"]
    url = f"/users/{user.id}"  # read before expiring: attribute access would itself run a query
    db.expire_all()

    statements = []
    connection = db.get_bind()

    def record(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(" ".join(statement.split("FROM", 1)[-1].split())[:200])

    event.listen(connection, "before_cursor_execute", record)
    try:
        assert client.get(url, headers=_bearer(access)).status_code == 200
    finally:
        event.remove(connection, "before_cursor_execute", record)

    assert len(statements) == 1, statements
    assert "LEFT OUTER JOIN revoked_tokens" in statements[0]
