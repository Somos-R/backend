"""Somos R as an actor: the `platform` user type and its `platform_admin` role."""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import get_db
from app.core.permissions import (
    PLATFORM_CAPABILITIES,
    ROLE_USER_TYPE,
    has_capability,
)
from app.core.security import require_capability
from app.domains.audit.models import AuditLog
from app.domains.catalogs.models import Role, UserType
from app.domains.users.models import User
from scripts.create_platform_admin import MIN_LENGTH, create_platform_admin
from tests import factories

GOOD_PASSWORD = "Una-clave-larga-2026"


@pytest.fixture
def platform_admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


class TestCatalog:
    def test_the_type_and_role_exist(self, db):
        assert db.get(UserType, "platform") is not None
        assert db.get(Role, "platform_admin") is not None
        assert ROLE_USER_TYPE["platform_admin"] == "platform"

    def test_the_role_is_not_in_the_public_catalog(self, client):
        codes = {r["code"] for r in client.get("/catalogs/roles").json()}
        assert "platform_admin" not in codes and "eca_admin" in codes


class TestNoWayIn:
    def test_anonymous_registration_of_a_platform_user_is_refused(self, client):
        r = client.post("/auth/register", json={
            "user_type_code": "platform", "email": "intruso@test.com", "password": GOOD_PASSWORD,
            "full_name": "Intruso", "id_type": "CC", "id_number": "999", "role_code": "platform_admin"})
        assert r.status_code == 422

    def test_a_customer_admin_cannot_hand_out_the_platform_role(self, client_as, eca_admin):
        r = client_as(eca_admin).post("/auth/register", json={
            "user_type_code": "eca", "email": "otro@test.com", "password": GOOD_PASSWORD,
            "full_name": "Otro", "id_type": "CC", "id_number": "998", "role_code": "platform_admin"})
        assert r.status_code == 422 and r.json()["code"] == "invalid_role"

    def test_a_customer_admin_cannot_promote_a_user_to_the_platform_role(self, client_as, eca_admin, db):
        colleague = factories.make_user(db, "eca", role_code="eca_operator")
        r = client_as(eca_admin).patch(f"/users/{colleague.id}", json={"role_code": "platform_admin"})
        assert r.status_code == 422 and r.json()["code"] == "invalid_role"


class TestNoAccessToCustomerEndpoints:
    """Being a platform admin gives nothing on the customer API: it has its own endpoints."""

    @pytest.mark.parametrize("path", ["/users", "/weighings", "/inventory", "/transactions", "/audit-log"])
    def test_customer_endpoints_answer_403(self, client_as, platform_admin, path):
        assert client_as(platform_admin).get(path).status_code == 403

    def test_the_profile_endpoint_works_and_announces_no_customer_capabilities(self, client_as, platform_admin):
        body = client_as(platform_admin).get("/auth/me").json()
        assert body["user_type_code"] == "platform" and body["capabilities"] == []


class TestPublicLoginIsClosed:
    def _login(self, client, user, password=factories.DEFAULT_PASSWORD):
        return client.post("/auth/login", json={"email": user.email, "password": password})

    def test_correct_credentials_do_not_open_a_session(self, client, platform_admin):
        r = self._login(client, platform_admin)
        assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"

    def test_the_answer_is_the_same_as_for_an_unknown_account(self, client, platform_admin):
        known = self._login(client, platform_admin)
        unknown = client.post("/auth/login", json={"email": "nadie@test.com", "password": "x" * 12})
        assert known.json() == unknown.json()

    def test_the_reason_is_recorded_in_the_audit_trail(self, client, platform_admin, db):
        self._login(client, platform_admin)
        entry = db.scalars(select(AuditLog).where(
            AuditLog.action == "auth.login_failed", AuditLog.target_id == str(platform_admin.id))).one()
        assert entry.details == {"reason": "platform_account"}

    def test_guessing_at_the_public_endpoint_cannot_lock_the_admin_out(self, client, platform_admin, db):
        for _ in range(12):
            self._login(client, platform_admin, "incorrecta-1234")
        db.refresh(platform_admin)
        assert platform_admin.failed_login_attempts == 0 and platform_admin.locked_until is None

    def test_password_recovery_by_public_email_link_is_not_offered(self, client, platform_admin, outbox):
        r = client.post("/auth/forgot-password", json={"email": platform_admin.email})
        assert r.status_code == 200 and outbox == []


class TestCapabilities:
    def test_platform_admin_holds_every_backoffice_capability(self, platform_admin):
        assert all(has_capability(platform_admin, c) for c in PLATFORM_CAPABILITIES)

    @pytest.mark.parametrize("role", ["eca_admin", "association_admin", "association_operator"])
    def test_customer_roles_hold_none(self, db, role):
        user = factories.make_user(db, ROLE_USER_TYPE[role], role_code=role)
        assert not any(has_capability(user, c) for c in PLATFORM_CAPABILITIES)

    def test_the_role_must_fit_the_actor_type(self, db):
        stray = factories.make_user(db, "eca", role_code="platform_admin")
        assert not has_capability(stray, "users.manage")

    def test_an_unknown_capability_is_never_granted(self, platform_admin):
        assert not has_capability(platform_admin, "nothing.at.all")

    def test_the_dependency_lets_the_role_in_and_keeps_others_out(self, db, platform_admin, eca_admin):
        app = FastAPI()

        @app.get("/probe")
        def probe(user=Depends(require_capability("users.manage"))):
            return {"id": str(user.id)}

        app.dependency_overrides[get_db] = lambda: db
        assert TestClient(app, headers=factories.backoffice_headers(platform_admin)).get("/probe").status_code == 200
        assert TestClient(app, headers=factories.backoffice_headers(eca_admin)).get("/probe").status_code == 401
        # a portal token, even of a platform user, is not a backoffice token
        assert TestClient(app, headers=factories.auth_headers(platform_admin)).get("/probe").status_code == 401
        assert TestClient(app).get("/probe").status_code in (401, 403)


class TestCreatePlatformAdminScript:
    def _create(self, db, **kw):
        args = {"email": "Ana@SomosR.co", "full_name": "Ana Admin", "id_number": "5550001", "password": GOOD_PASSWORD}
        return create_platform_admin(db, **{**args, **kw})

    def test_creates_the_account_ready_to_use(self, db):
        user = self._create(db)
        assert user.user_type_code == "platform" and user.role_code == "platform_admin"
        assert user.email == "ana@somosr.co" and user.is_active and user.email_verified_at is not None
        assert user.password_hash and user.password_hash != GOOD_PASSWORD

    def test_leaves_an_audit_entry_without_personal_data(self, db):
        user = self._create(db)
        entry = db.scalars(select(AuditLog).where(
            AuditLog.action == "platform_admin.created", AuditLog.target_id == str(user.id))).one()
        assert entry.details == {"via": "script"}

    def test_refuses_a_duplicate_email(self, db):
        self._create(db)
        with pytest.raises(ValueError, match="correo"):
            self._create(db, id_number="5550002")

    def test_refuses_a_duplicate_document(self, db):
        self._create(db)
        with pytest.raises(ValueError, match="documento"):
            self._create(db, email="otra@somosr.co")

    def test_requires_a_longer_password_than_customers(self, db):
        with pytest.raises(ValueError, match=str(MIN_LENGTH)):
            self._create(db, password="Corta-2026x")

    def test_refuses_a_weak_password(self, db):
        with pytest.raises(ValueError):
            self._create(db, password="12345678901234")

    def test_nothing_is_saved_when_it_fails(self, db):
        with pytest.raises(ValueError):
            self._create(db, password="corta")
        assert db.scalars(select(User).where(User.user_type_code == "platform")).all() == []
