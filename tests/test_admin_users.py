"""User management from the backoffice: find people, act on their accounts, all of it audited."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import permissions
from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.audit.models import AuditLog
from app.domains.auth import service as auth_service
from app.domains.auth.models import MfaCredential
from app.domains.catalogs.models import Role
from app.domains.organizations.enums import OrganizationStatus
from app.domains.users.enums import VerificationStatus
from app.main import app
from tests import factories

PASSWORD = factories.DEFAULT_PASSWORD


@pytest.fixture
def admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


@pytest.fixture
def bo(client, admin):
    return TestClient(app, headers=factories.backoffice_headers(admin))


@pytest.fixture
def world(db):
    """Two organizations with staff, and people outside any organization."""
    eca = factories.make_organization(db, "eca", legal_name="ECA Norte")
    assoc = factories.make_organization(db, "association", legal_name="Asociación Sur")
    return type("World", (), {
        "eca": eca, "assoc": assoc,
        "eca_admin": factories.make_user(db, "eca", role_code="eca_admin", organization_id=eca.id, full_name="Ana Álvarez"),
        "eca_op": factories.make_user(db, "eca", role_code="eca_operator", organization_id=eca.id, full_name="Beto Boyacá"),
        "assoc_admin": factories.make_user(db, "association", role_code="association_admin",
                                           organization_id=assoc.id, full_name="Carla Cárdenas"),
        "recycler": factories.make_user(db, "recycler", full_name="Diego Díaz"),
        "citizen": factories.make_user(db, "citizen", full_name="Elena Escobar"),
    })


def _actions(db, user):
    rows = db.scalars(select(AuditLog).where(AuditLog.target_id == str(user.id)).order_by(AuditLog.occurred_at)).all()
    return [(r.action, r.actor_id, r.details) for r in rows]


class TestListing:
    def test_it_spans_every_organization_and_type(self, bo, world):
        ids = {i["id"] for i in bo.get("/admin/users", params={"limit": 200}).json()["items"]}
        assert {str(u.id) for u in (world.eca_admin, world.eca_op, world.assoc_admin, world.recycler, world.citizen)} <= ids

    def test_the_list_carries_a_summary_not_the_whole_profile(self, bo, world):
        item = next(i for i in bo.get("/admin/users", params={"q": "Beto"}).json()["items"])
        assert set(item) == {"id", "email", "full_name", "user_type_code", "role_code", "organization_id",
                             "is_active", "verification_status", "email_verified", "locked",
                             "pending_activation", "created_at"}
        assert "id_number" not in item and "phone" not in item and "address" not in item

    def test_text_search_ignores_case_and_accents(self, bo, world):
        for q in ("alvarez", "ÁLVAREZ", "cardenas"):
            assert bo.get("/admin/users", params={"q": q}).json()["total"] >= 1
        names = [i["full_name"] for i in bo.get("/admin/users", params={"q": "boyaca"}).json()["items"]]
        assert names == ["Beto Boyacá"]

    def test_filters_combine(self, bo, world):
        body = bo.get("/admin/users", params={"user_type_code": "eca", "organization_id": str(world.eca.id)}).json()
        assert {i["id"] for i in body["items"]} == {str(world.eca_admin.id), str(world.eca_op.id)}
        only_admins = bo.get("/admin/users", params={"organization_id": str(world.eca.id), "role_code": "eca_admin"}).json()
        assert [i["id"] for i in only_admins["items"]] == [str(world.eca_admin.id)]

    def test_by_active_state(self, bo, db, world):
        world.eca_op.is_active = False
        db.commit()
        ids = {i["id"] for i in bo.get("/admin/users", params={"is_active": "false", "limit": 200}).json()["items"]}
        assert str(world.eca_op.id) in ids and str(world.eca_admin.id) not in ids

    def test_by_locked(self, bo, db, world):
        world.eca_op.locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
        world.eca_admin.locked_until = datetime.now(timezone.utc) - timedelta(minutes=10)  # already over
        db.commit()
        locked = {i["id"] for i in bo.get("/admin/users", params={"locked": "true", "limit": 200}).json()["items"]}
        assert str(world.eca_op.id) in locked and str(world.eca_admin.id) not in locked
        unlocked = {i["id"] for i in bo.get("/admin/users", params={"locked": "false", "limit": 200}).json()["items"]}
        assert str(world.eca_admin.id) in unlocked and str(world.eca_op.id) not in unlocked

    def test_by_pending_activation(self, bo, db, world):
        invited = factories.make_user(db, "eca", role_code="eca_operator", organization_id=world.eca.id, password_hash="")
        pending = {i["id"] for i in bo.get("/admin/users", params={"pending_activation": "true", "limit": 200}).json()["items"]}
        assert str(invited.id) in pending and str(world.eca_op.id) not in pending
        item = next(i for i in bo.get("/admin/users", params={"q": invited.email}).json()["items"])
        assert item["pending_activation"] is True and item["locked"] is False

    def test_by_verification_status(self, bo, db, world):
        pending = factories.make_user(db, "recycler", verification_status=VerificationStatus.pending)
        ids = {i["id"] for i in bo.get("/admin/users", params={"verification_status": "pending"}).json()["items"]}
        assert str(pending.id) in ids and str(world.recycler.id) not in ids

    def test_somos_r_accounts_are_listed_too(self, bo, admin):
        ids = {i["id"] for i in bo.get("/admin/users", params={"user_type_code": "platform"}).json()["items"]}
        assert str(admin.id) in ids

    def test_pagination_has_an_exact_total_and_a_stable_order(self, bo, world):
        body = bo.get("/admin/users", params={"limit": 2, "offset": 0}).json()
        rest = bo.get("/admin/users", params={"limit": 2, "offset": 2}).json()
        assert len(body["items"]) == 2 and body["total"] == rest["total"]
        assert not {i["id"] for i in body["items"]} & {i["id"] for i in rest["items"]}

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"verification_status": "x"},
                                        {"organization_id": "no-es-uuid"}, {"is_active": "quizas"}])
    def test_invalid_parameters(self, bo, params):
        assert bo.get("/admin/users", params=params).status_code == 422


class TestDetail:
    def test_the_full_profile_with_the_security_state(self, bo, db, world):
        world.eca_op.failed_login_attempts = 3
        db.commit()
        body = bo.get(f"/admin/users/{world.eca_op.id}").json()
        assert body["id_number"] == world.eca_op.id_number and body["phone"] == world.eca_op.phone
        assert body["organization_name"] == "ECA Norte" and body["failed_login_attempts"] == 3
        assert body["locked"] is False and body["pending_activation"] is False and body["mfa_enabled"] is False
        assert "password_hash" not in body

    def test_mfa_state_of_a_somos_r_account(self, bo, db, admin):
        db.add(MfaCredential(user_id=admin.id, secret_encrypted="x", enabled_at=datetime.now(timezone.utc)))
        db.commit()
        assert bo.get(f"/admin/users/{admin.id}").json()["mfa_enabled"] is True

    def test_opening_a_profile_is_audited(self, bo, db, admin, world):
        bo.get(f"/admin/users/{world.recycler.id}")
        assert ("admin.user_viewed", admin.id, {}) in _actions(db, world.recycler)

    def test_a_missing_user(self, bo):
        r = bo.get("/admin/users/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404 and r.json()["code"] == "user_not_found"


class TestActivatingAndDeactivating:
    def test_deactivating_blocks_the_account_and_ends_its_sessions(self, bo, client, db, admin, world):
        token = client.post("/auth/login", json={"email": world.eca_op.email, "password": PASSWORD}).json()
        r = bo.patch(f"/admin/users/{world.eca_op.id}/status", json={"is_active": False, "reason": "Renunció"})
        assert r.status_code == 200 and r.json()["is_active"] is False
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"}).status_code == 401
        assert client.post("/auth/refresh", json={"refresh_token": token["refresh_token"]}).status_code == 401
        assert client.post("/auth/login", json={"email": world.eca_op.email, "password": PASSWORD}).status_code == 403
        assert ("user.deactivated", admin.id, {"reason": "Renunció"}) in _actions(db, world.eca_op)

    def test_reactivating_lets_them_sign_in_again(self, bo, client, world):
        bo.patch(f"/admin/users/{world.eca_op.id}/status", json={"is_active": False})
        bo.patch(f"/admin/users/{world.eca_op.id}/status", json={"is_active": True})
        assert client.post("/auth/login", json={"email": world.eca_op.email, "password": PASSWORD}).status_code == 200

    def test_no_reason_is_fine(self, bo, db, admin, world):
        bo.patch(f"/admin/users/{world.eca_op.id}/status", json={"is_active": False})
        assert ("user.deactivated", admin.id, {}) in _actions(db, world.eca_op)

    def test_doing_what_is_already_so_records_nothing(self, bo, db, world):
        assert bo.patch(f"/admin/users/{world.eca_op.id}/status", json={"is_active": True}).status_code == 200
        assert _actions(db, world.eca_op) == []

    def test_nobody_deactivates_their_own_account(self, bo, admin):
        r = bo.patch(f"/admin/users/{admin.id}/status", json={"is_active": False})
        assert r.status_code == 403 and r.json()["code"] == "cannot_change_own_status"

    def test_another_somos_r_account_can_be_deactivated(self, bo, db):
        other = factories.make_user(db, "platform", role_code="platform_admin")
        assert bo.patch(f"/admin/users/{other.id}/status", json={"is_active": False}).status_code == 200

    def test_reason_length_is_bounded(self, bo, world):
        assert bo.patch(f"/admin/users/{world.eca_op.id}/status",
                        json={"is_active": False, "reason": "x" * 201}).status_code == 422


class TestUnlockAndSessions:
    def test_unlock_clears_the_block_and_the_counter(self, bo, client, db, admin, world):
        for _ in range(settings.login_max_attempts):
            client.post("/auth/login", json={"email": world.eca_op.email, "password": "incorrecta-1234"})
        assert client.post("/auth/login", json={"email": world.eca_op.email, "password": PASSWORD}).status_code == 401
        r = bo.post(f"/admin/users/{world.eca_op.id}/unlock")
        assert r.status_code == 200 and r.json()["locked"] is False
        assert client.post("/auth/login", json={"email": world.eca_op.email, "password": PASSWORD}).status_code == 200
        assert "user.unlocked" in [a for a, _, _ in _actions(db, world.eca_op)]

    def test_revoking_sessions_signs_them_out_everywhere_but_keeps_the_account(self, bo, client, db, admin, world):
        first = client.post("/auth/login", json={"email": world.eca_op.email, "password": PASSWORD}).json()
        second = client.post("/auth/login", json={"email": world.eca_op.email, "password": PASSWORD}).json()
        r = bo.post(f"/admin/users/{world.eca_op.id}/sessions/revoke")
        assert r.status_code == 200 and r.json()["is_active"] is True
        for pair in (first, second):
            assert client.get("/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"}).status_code == 401
            assert client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401
        assert client.post("/auth/login", json={"email": world.eca_op.email, "password": PASSWORD}).status_code == 200
        assert "user.sessions_revoked" in [a for a, _, _ in _actions(db, world.eca_op)]


class TestResendingAnActivation:
    def test_staff_get_the_invitation_email_of_their_organization(self, bo, db, world, outbox):
        invited = factories.make_user(db, "eca", role_code="eca_operator", organization_id=world.eca.id, password_hash="")
        r = bo.post(f"/admin/users/{invited.id}/invitation/resend")
        assert r.status_code == 200
        (message,) = outbox
        assert message.to == invited.email and "ECA Norte" in message.subject and "activate?token=" in message.body

    def test_the_old_link_stops_working(self, bo, client, db, world, outbox):
        invited = factories.make_user(db, "eca", role_code="eca_operator", organization_id=world.eca.id, password_hash="")
        old = auth_service.issue_token(db, invited, auth_service.ACTIVATE)
        db.commit()
        bo.post(f"/admin/users/{invited.id}/invitation/resend")
        new = outbox[-1].body.split("token=")[1].split()[0]
        assert client.post("/auth/activate", json={"token": old, "password": "Una-clave-nueva-2026"}).status_code == 400
        assert client.post("/auth/activate", json={"token": new, "password": "Una-clave-nueva-2026"}).status_code == 200

    def test_a_verified_recycler_gets_the_recycler_email(self, bo, db, outbox):
        recycler = factories.make_user(db, "recycler", password_hash="")
        assert bo.post(f"/admin/users/{recycler.id}/invitation/resend").status_code == 200
        assert "Tu perfil fue verificado" in outbox[-1].body

    def test_a_recycler_pending_verification_gets_nothing(self, bo, db, outbox):
        recycler = factories.make_user(db, "recycler", password_hash="", verification_status=VerificationStatus.pending)
        r = bo.post(f"/admin/users/{recycler.id}/invitation/resend")
        assert r.status_code == 409 and r.json()["code"] == "not_verified" and outbox == []

    def test_someone_who_already_has_a_password_is_refused(self, bo, world):
        r = bo.post(f"/admin/users/{world.eca_op.id}/invitation/resend")
        assert r.status_code == 409 and r.json()["code"] == "invitation_not_pending"

    def test_staff_without_an_organization_must_get_one_first(self, bo, db, outbox):
        orphan = factories.make_user(db, "eca", role_code="eca_operator", organization_id=None, password_hash="")
        r = bo.post(f"/admin/users/{orphan.id}/invitation/resend")
        assert r.status_code == 409 and r.json()["code"] == "no_organization" and outbox == []

    def test_a_deactivated_account_is_not_sent_a_link(self, bo, db, world):
        invited = factories.make_user(db, "eca", role_code="eca_operator", organization_id=world.eca.id,
                                      password_hash="", is_active=False)
        assert bo.post(f"/admin/users/{invited.id}/invitation/resend").status_code == 409

    def test_it_is_audited_as_done_from_the_backoffice(self, bo, db, admin, world):
        invited = factories.make_user(db, "eca", role_code="eca_operator", organization_id=world.eca.id, password_hash="")
        bo.post(f"/admin/users/{invited.id}/invitation/resend")
        assert ("user.invitation_resent", admin.id, {"via": "backoffice"}) in _actions(db, invited)


class TestChangingARole:
    def test_a_staff_member_gets_another_existing_role(self, bo, db, admin, world):
        r = bo.patch(f"/admin/users/{world.eca_op.id}/role", json={"role_code": "eca_warehouse"})
        assert r.status_code == 200 and r.json()["role_code"] == "eca_warehouse"
        assert ("user.role_changed", admin.id, {"from": "eca_operator", "to": "eca_warehouse"}) in _actions(db, world.eca_op)

    def test_the_same_role_changes_nothing(self, bo, db, world):
        assert bo.patch(f"/admin/users/{world.eca_op.id}/role", json={"role_code": "eca_operator"}).status_code == 200
        assert _actions(db, world.eca_op) == []

    def test_the_role_must_fit_the_type(self, bo, world):
        r = bo.patch(f"/admin/users/{world.eca_op.id}/role", json={"role_code": "association_admin"})
        assert r.status_code == 422 and r.json()["code"] == "invalid_role"

    def test_the_somos_r_role_can_not_be_given_to_customers_staff(self, bo, world):
        r = bo.patch(f"/admin/users/{world.eca_op.id}/role", json={"role_code": "platform_admin"})
        assert r.status_code == 422

    def test_unknown_and_inactive_roles(self, bo, db, world):
        assert bo.patch(f"/admin/users/{world.eca_op.id}/role", json={"role_code": "nada"}).status_code == 422
        db.get(Role, "eca_warehouse").is_active = False
        db.commit()
        assert bo.patch(f"/admin/users/{world.eca_op.id}/role", json={"role_code": "eca_warehouse"}).status_code == 422

    @pytest.mark.parametrize("kind", ["recycler", "citizen"])
    def test_people_without_roles_are_refused(self, bo, world, kind):
        r = bo.patch(f"/admin/users/{getattr(world, kind).id}/role", json={"role_code": "eca_admin"})
        assert r.status_code == 409 and r.json()["code"] == "role_not_editable"

    def test_somos_r_roles_are_fixed(self, bo, admin):
        assert bo.patch(f"/admin/users/{admin.id}/role", json={"role_code": "platform_admin"}).status_code == 409


class TestAssigningAnOrganization:
    @pytest.fixture
    def orphan(self, db):
        return factories.make_user(db, "eca", role_code="eca_operator", organization_id=None)

    def test_staff_without_one_get_it_and_become_reachable_from_it(self, bo, client_as, db, admin, world, orphan):
        r = bo.put(f"/admin/users/{orphan.id}/organization", json={"organization_id": str(world.eca.id)})
        assert r.status_code == 200 and r.json()["organization_id"] == str(world.eca.id)
        ids = {i["id"] for i in client_as(world.eca_admin).get("/users", params={"user_type_code": "eca"}).json()["items"]}
        assert str(orphan.id) in ids
        assert ("user.organization_assigned", admin.id, {"organization_id": str(world.eca.id)}) in _actions(db, orphan)

    def test_moving_someone_between_organizations_is_not_possible(self, bo, db, world):
        other = factories.make_organization(db, "eca")
        r = bo.put(f"/admin/users/{world.eca_op.id}/organization", json={"organization_id": str(other.id)})
        assert r.status_code == 409 and r.json()["code"] == "already_in_organization"
        db.refresh(world.eca_op)
        assert world.eca_op.organization_id == world.eca.id

    def test_the_organization_must_be_of_the_same_type(self, bo, world, orphan):
        r = bo.put(f"/admin/users/{orphan.id}/organization", json={"organization_id": str(world.assoc.id)})
        assert r.status_code == 422 and r.json()["code"] == "organization_type_mismatch"

    @pytest.mark.parametrize("state", [OrganizationStatus.draft, OrganizationStatus.submitted,
                                       OrganizationStatus.rejected, OrganizationStatus.suspended])
    def test_only_approved_organizations(self, bo, db, orphan, state):
        org = factories.make_organization(db, "eca", status=state)
        r = bo.put(f"/admin/users/{orphan.id}/organization", json={"organization_id": str(org.id)})
        assert r.status_code == 409 and r.json()["code"] == "organization_not_active"

    def test_an_unknown_organization(self, bo, orphan):
        r = bo.put(f"/admin/users/{orphan.id}/organization",
                   json={"organization_id": "00000000-0000-0000-0000-000000000000"})
        assert r.status_code == 404 and r.json()["code"] == "organization_not_found"

    @pytest.mark.parametrize("kind", ["recycler", "citizen"])
    def test_only_staff_belong_to_organizations(self, bo, world, kind):
        r = bo.put(f"/admin/users/{getattr(world, kind).id}/organization", json={"organization_id": str(world.eca.id)})
        assert r.status_code == 409 and r.json()["code"] == "organization_not_applicable"


class TestWhoMayUseIt:
    ENDPOINTS = [
        ("get", "/admin/users"), ("get", "/admin/users/{id}"), ("patch", "/admin/users/{id}/status"),
        ("post", "/admin/users/{id}/unlock"), ("post", "/admin/users/{id}/sessions/revoke"),
        ("post", "/admin/users/{id}/invitation/resend"), ("patch", "/admin/users/{id}/role"),
        ("put", "/admin/users/{id}/organization"),
    ]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_customers_and_anonymous_callers_are_refused(self, client_as, client, world, method, path):
        url = path.replace("{id}", str(world.eca_op.id))
        for user in (world.eca_admin, world.assoc_admin):
            assert client_as(user).request(method.upper(), url, json={}).status_code == 401
        assert client.request(method.upper(), url, json={}).status_code in (401, 403)

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_the_capability_decides(self, bo, world, monkeypatch, method, path):
        monkeypatch.setitem(permissions.PLATFORM_CAPABILITIES, "users.manage", frozenset())
        r = bo.request(method.upper(), path.replace("{id}", str(world.eca_op.id)), json={})
        assert r.status_code == 403 and r.json()["code"] == "forbidden"

    def test_a_platform_users_portal_token_is_not_enough(self, client_as, admin):
        assert client_as(admin).get("/admin/users").status_code == 401

    def test_the_network_restriction_applies(self, client, admin, monkeypatch):
        monkeypatch.setattr(settings, "admin_allowed_cidrs", "203.0.113.0/24")
        headers = factories.backoffice_headers(admin)
        assert TestClient(app, headers=headers, client=("192.0.2.1", 1)).get("/admin/users").status_code == 403
        assert TestClient(app, headers=headers, client=("203.0.113.5", 1)).get("/admin/users").status_code == 200

    def test_the_rate_limit_applies(self, bo, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_admin", "3/minute")
        limiter.reset()
        try:
            assert [bo.get("/admin/users").status_code for _ in range(5)] == [200, 200, 200, 429, 429]
        finally:
            limiter.reset()


class TestNothingLeaksToTheCustomerApi:
    def test_a_deactivation_from_the_backoffice_is_visible_to_the_organization(self, bo, client_as, world):
        bo.patch(f"/admin/users/{world.eca_op.id}/status", json={"is_active": False})
        body = client_as(world.eca_admin).get(f"/users/{world.eca_op.id}").json()
        assert body["is_active"] is False
