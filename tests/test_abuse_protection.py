"""Rate limiting, account lockout and constant-time login (tasks 2.6, 2.7, 2.8)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.auth import router as auth_router
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from tests import factories
from tests.factories import DEFAULT_PASSWORD


def _login(client, email, password="incorrecta"):
    return client.post("/auth/login", json={"email": email, "password": password})


# --- Rate limiting -----------------------------------------------------------------

@pytest.fixture
def rate_limited(monkeypatch):
    """Turn the limiter on with tiny limits; counters are wiped before and after."""
    monkeypatch.setattr(limiter, "enabled", True)
    monkeypatch.setattr(settings, "rate_limit_login", "3/minute")
    monkeypatch.setattr(settings, "rate_limit_register", "2/minute")
    monkeypatch.setattr(settings, "rate_limit_forgot_password", "2/minute")
    monkeypatch.setattr(settings, "rate_limit_token_flows", "2/minute")
    limiter.reset()
    yield
    limiter.reset()


class TestRateLimiting:
    def test_disabled_in_the_default_test_configuration(self, client):
        assert all(_login(client, "a@test.com").status_code == 401 for _ in range(30))

    def test_login_is_limited_per_ip(self, client, rate_limited):
        statuses = [_login(client, "a@test.com").status_code for _ in range(5)]
        assert statuses == [401, 401, 401, 429, 429]

    def test_the_429_body_and_retry_after_header(self, client, rate_limited):
        for _ in range(3):
            _login(client, "a@test.com")
        r = _login(client, "a@test.com")
        assert r.status_code == 429
        assert r.headers["Retry-After"] == "60"
        assert "Demasiadas solicitudes" in r.json()["detail"]

    def test_a_correct_login_still_counts(self, client, db, rate_limited):
        user = factories.make_user(db, "citizen")
        for _ in range(3):
            assert _login(client, user.email, DEFAULT_PASSWORD).status_code == 200
        assert _login(client, user.email, DEFAULT_PASSWORD).status_code == 429

    def test_register_is_limited(self, client, rate_limited):
        def register(n):
            return client.post("/auth/register", json={
                "user_type_code": "citizen", "email": f"u{n}@test.com", "password": DEFAULT_PASSWORD,
                "full_name": "U", "id_type": "CC", "id_number": f"9000{n}"})

        assert [register(n).status_code for n in range(3)] == [201, 201, 429]

    def test_forgot_password_is_limited(self, client, rate_limited):
        codes = [client.post("/auth/forgot-password", json={"email": "a@test.com"}).status_code
                 for _ in range(3)]
        assert codes == [200, 200, 429]

    def test_token_flows_are_limited(self, client, rate_limited):
        body = {"token": "x" * 43, "password": "Nueva-Clave-2026"}
        codes = [client.post("/auth/activate", json=body).status_code for _ in range(3)]
        assert codes == [400, 400, 429]

    def test_limits_are_independent_per_endpoint(self, client, rate_limited):
        for _ in range(4):
            _login(client, "a@test.com")
        assert client.post("/auth/forgot-password", json={"email": "a@test.com"}).status_code == 200


# --- Account lockout ---------------------------------------------------------------------

def _refresh(db, user):
    db.expire_all()
    return db.get(User, user.id)


class TestLockout:
    def test_locks_after_the_configured_number_of_failures(self, client, db):
        user = factories.make_user(db, "citizen")
        for _ in range(settings.login_max_attempts):
            assert _login(client, user.email).status_code == 401
        assert _refresh(db, user).locked_until is not None

        # Even the right password is refused while locked.
        assert _login(client, user.email, DEFAULT_PASSWORD).status_code == 401

    def test_locked_answer_is_identical_to_an_unknown_account(self, client, db):
        user = factories.make_user(db, "citizen")
        for _ in range(settings.login_max_attempts):
            _login(client, user.email)

        locked = _login(client, user.email, DEFAULT_PASSWORD)
        unknown = _login(client, "nadie@test.com", DEFAULT_PASSWORD)
        assert (locked.status_code, locked.json(), dict(locked.headers)) == (
            unknown.status_code, unknown.json(), dict(unknown.headers))

    def test_login_works_again_once_the_lock_expires_and_counters_reset(self, client, db):
        user = factories.make_user(db, "citizen")
        for _ in range(settings.login_max_attempts):
            _login(client, user.email)
        user = _refresh(db, user)
        user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

        assert _login(client, user.email, DEFAULT_PASSWORD).status_code == 200
        user = _refresh(db, user)
        assert user.failed_login_attempts == 0 and user.locked_until is None

    def test_a_success_before_the_threshold_resets_the_count(self, client, db):
        user = factories.make_user(db, "citizen")
        for _ in range(settings.login_max_attempts - 1):
            _login(client, user.email)
        assert _login(client, user.email, DEFAULT_PASSWORD).status_code == 200
        for _ in range(settings.login_max_attempts - 1):
            _login(client, user.email)
        assert _login(client, user.email, DEFAULT_PASSWORD).status_code == 200

    def test_failures_while_locked_do_not_extend_the_lock(self, client, db):
        user = factories.make_user(db, "citizen")
        for _ in range(settings.login_max_attempts):
            _login(client, user.email)
        locked_until = _refresh(db, user).locked_until
        for _ in range(5):
            _login(client, user.email)
        user = _refresh(db, user)
        assert user.locked_until == locked_until
        assert user.failed_login_attempts == settings.login_max_attempts

    def test_lock_duration_doubles_and_is_capped(self, client, db):
        user = factories.make_user(db, "citizen")

        def lock_minutes_after(attempts_before):
            u = _refresh(db, user)
            u.failed_login_attempts = attempts_before
            u.locked_until = None
            db.commit()
            _login(client, user.email)
            remaining = _refresh(db, user).locked_until - datetime.now(timezone.utc)
            return remaining.total_seconds() / 60

        n = settings.login_max_attempts
        assert lock_minutes_after(n - 1) == pytest.approx(1, abs=0.1)
        assert lock_minutes_after(n) == pytest.approx(2, abs=0.1)
        assert lock_minutes_after(n + 2) == pytest.approx(8, abs=0.1)
        assert lock_minutes_after(n + 30) == pytest.approx(settings.login_lock_max_minutes, abs=0.1)

    def test_unknown_accounts_leave_no_trace(self, client, db):
        before = db.query(User).count()
        for _ in range(10):
            _login(client, "nadie@test.com")
        assert db.query(User).count() == before

    def test_resetting_the_password_clears_the_lock(self, client, db, outbox):
        user = factories.make_user(db, "citizen")
        for _ in range(settings.login_max_attempts):
            _login(client, user.email)
        client.post("/auth/forgot-password", json={"email": user.email})
        token = outbox[0].body.split("token=")[1].split()[0]
        client.post("/auth/reset-password", json={"token": token, "password": "Nueva-Clave-2026"})

        assert _login(client, user.email, "Nueva-Clave-2026").status_code == 200


# --- Constant-time verification ------------------------------------------------------------

class TestConstantTimeLogin:
    @pytest.fixture
    def bcrypt_calls(self, monkeypatch):
        calls = []
        real = auth_router.verify_password

        def spy(plain, hashed):
            calls.append(hashed)
            return real(plain, hashed)

        monkeypatch.setattr(auth_router, "verify_password", spy)
        return calls

    def test_unknown_account_still_runs_one_bcrypt_check(self, client, bcrypt_calls):
        _login(client, "nadie@test.com")
        assert len(bcrypt_calls) == 1

    def test_pending_recycler_without_a_password_still_runs_one_check(self, client, db, bcrypt_calls):
        user = factories.make_user(
            db, "recycler", verification_status=VerificationStatus.pending, password_hash="")
        r = _login(client, user.email, "")
        assert r.status_code == 401
        assert len(bcrypt_calls) == 1

    def test_locked_account_still_runs_one_check(self, client, db, bcrypt_calls):
        user = factories.make_user(db, "citizen")
        for _ in range(settings.login_max_attempts):
            _login(client, user.email)
        bcrypt_calls.clear()
        _login(client, user.email, DEFAULT_PASSWORD)
        assert len(bcrypt_calls) == 1

    def test_wrong_and_right_passwords_each_run_one_check(self, client, db, bcrypt_calls):
        user = factories.make_user(db, "citizen")
        _login(client, user.email)
        _login(client, user.email, DEFAULT_PASSWORD)
        assert len(bcrypt_calls) == 2

    def test_the_dummy_hash_never_authenticates_anyone(self, client, db):
        # A pending recycler has an empty hash: the dummy comparison must not make it valid.
        user = factories.make_user(
            db, "recycler", verification_status=VerificationStatus.verified, password_hash="")
        assert _login(client, user.email, "").status_code == 401
