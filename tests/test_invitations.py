"""Staff invitations: the organization's admin names the person, the person chooses the password."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.audit.models import AuditLog
from app.domains.auth import service as auth_service
from app.domains.auth.models import OneTimeToken
from app.domains.catalogs.models import Role
from app.domains.organizations.enums import OrganizationStatus
from app.domains.users.models import User
from app.main import app
from tests import factories

NEW_PASSWORD = "Una-clave-nueva-2026"
_n = itertools.count(1)


def _payload(role="eca_operator", **overrides):
    n = next(_n)
    payload = {"email": f"invitada{n}@test.com", "full_name": "Persona Invitada", "id_type": "CC",
               "id_number": f"6{n:07d}", "phone": "3150000000", "role_code": role}
    payload.update(overrides)
    return payload


def _token_from(message) -> str:
    return message.body.split("token=")[1].split()[0]


@pytest.fixture
def eca(db):
    org = factories.make_organization(db, "eca", legal_name="ECA Norte")
    return org, factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)


@pytest.fixture
def assoc(db):
    org = factories.make_organization(db, "association", legal_name="Asociación Sur", tax_id="900555666-1",
                                      legal_representative="Rep Legal Sur")
    return org, factories.make_user(db, "association", role_code="association_admin", organization_id=org.id)


class TestInviting:
    def test_creates_the_account_without_a_password_in_the_admins_organization(self, client_as, db, eca):
        org, admin = eca
        r = client_as(admin).post("/users/invitations", json=_payload("eca_operator"))
        assert r.status_code == 201, r.text
        user = db.get(User, r.json()["id"])
        assert user.password_hash == "" and user.organization_id == org.id
        assert user.user_type_code == "eca" and user.role_code == "eca_operator" and user.is_active
        assert user.email_verified_at is None

    def test_the_response_never_carries_a_token_or_password(self, client_as, eca):
        body = client_as(eca[1]).post("/users/invitations", json=_payload()).json()
        assert not {"token", "password", "password_hash", "activation_token"} & set(body)
        assert body["role_code"] == "eca_operator" and body["organization_id"] == str(eca[0].id)

    def test_an_association_admin_invites_association_staff_with_the_organizations_data(self, client_as, db, assoc):
        org, admin = assoc
        r = client_as(admin).post("/users/invitations", json=_payload("association_operator"))
        user = db.get(User, r.json()["id"])
        assert user.user_type_code == "association" and user.organization_id == org.id
        assert user.association_nit == "900555666-1" and user.legal_representative == "Rep Legal Sur"

    def test_the_invitee_gets_an_email_with_a_one_time_activation_link(self, client_as, eca, outbox):
        payload = _payload()
        client_as(eca[1]).post("/users/invitations", json=payload)
        (message,) = outbox
        assert message.to == payload["email"] and "ECA Norte" in message.subject
        assert f"{settings.frontend_url.rstrip('/')}/activate?token=" in message.body
        assert f"{settings.activation_token_minutes // 60} horas" in message.body

    def test_the_link_is_stored_only_as_a_hash_and_expires_in_48_hours(self, client_as, db, eca, outbox):
        client_as(eca[1]).post("/users/invitations", json=_payload())
        token = _token_from(outbox[-1])
        row = db.scalars(select(OneTimeToken).where(OneTimeToken.purpose == auth_service.ACTIVATE)).one()
        assert token not in row.token_hash and row.used_at is None
        assert timedelta(hours=47) < row.expires_at - datetime.now(timezone.utc) <= timedelta(hours=48)

    def test_it_is_audited_with_the_role_and_without_personal_data(self, client_as, db, eca):
        payload = _payload()
        user_id = client_as(eca[1]).post("/users/invitations", json=payload).json()["id"]
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.invited")).one()
        assert entry.target_id == user_id and entry.actor_id == eca[1].id
        assert entry.details == {"role_code": "eca_operator"}
        assert payload["email"] not in str(entry.details) and payload["id_number"] not in str(entry.details)

    def test_the_invitee_appears_in_the_organizations_list(self, client_as, eca):
        created = client_as(eca[1]).post("/users/invitations", json=_payload()).json()
        items = client_as(eca[1]).get("/users", params={"user_type_code": "eca"}).json()["items"]
        assert created["id"] in {i["id"] for i in items}

    def test_the_client_cannot_choose_the_type_or_the_organization(self, client_as, db, eca):
        other = factories.make_organization(db, "eca")
        r = client_as(eca[1]).post("/users/invitations", json=_payload(
            organization_id=str(other.id), user_type_code="association", password=NEW_PASSWORD))
        user = db.get(User, r.json()["id"])
        assert user.organization_id == eca[0].id and user.user_type_code == "eca" and user.password_hash == ""


class TestNobodyCanSignInUntilTheyActivate:
    def test_the_login_is_refused(self, client, client_as, eca):
        payload = _payload()
        client_as(eca[1]).post("/users/invitations", json=payload)
        r = client.post("/auth/login", json={"email": payload["email"], "password": NEW_PASSWORD})
        assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"

    def test_no_password_reset_link_is_offered_either(self, client, client_as, eca, outbox):
        payload = _payload()
        client_as(eca[1]).post("/users/invitations", json=payload)
        outbox.clear()
        client.post("/auth/forgot-password", json={"email": payload["email"]})
        assert outbox == []


class TestActivating:
    def _invite(self, client_as, admin, outbox, **kw):
        payload = _payload(**kw)
        client_as(admin).post("/users/invitations", json=payload)
        return payload, _token_from(outbox[-1])

    def test_the_person_chooses_their_password_and_signs_in(self, client, client_as, db, eca, outbox):
        payload, token = self._invite(client_as, eca[1], outbox)
        r = client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD})
        assert r.status_code == 200, r.text
        login = client.post("/auth/login", json={"email": payload["email"], "password": NEW_PASSWORD})
        assert login.status_code == 200
        user = db.scalars(select(User).where(User.email == payload["email"])).one()
        assert user.email_verified_at is not None
        assert user.role_code == "eca_operator" and user.organization_id == eca[0].id

    def test_the_link_works_once(self, client, client_as, eca, outbox):
        _, token = self._invite(client_as, eca[1], outbox)
        assert client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD}).status_code == 200
        again = client.post("/auth/activate", json={"token": token, "password": "Otra-clave-2026x"})
        assert again.status_code == 400 and again.json()["code"] == "invalid_link"

    def test_an_expired_link_is_refused(self, client, client_as, db, eca, outbox):
        _, token = self._invite(client_as, eca[1], outbox)
        row = db.scalars(select(OneTimeToken).where(OneTimeToken.purpose == auth_service.ACTIVATE)).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        assert client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD}).status_code == 400

    def test_the_password_policy_applies(self, client, client_as, eca, outbox):
        _, token = self._invite(client_as, eca[1], outbox)
        assert client.post("/auth/activate", json={"token": token, "password": "corta"}).status_code == 422
        assert client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD}).status_code == 200

    def test_a_link_cannot_set_the_password_of_an_account_that_already_has_one(self, client, db):
        staff = factories.make_user(db, "eca", role_code="eca_operator")  # has a password
        token = auth_service.issue_token(db, staff, auth_service.ACTIVATE)
        db.commit()
        r = client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD})
        assert r.status_code == 400 and r.json()["code"] == "invalid_link"

    def test_a_somos_r_account_can_never_be_activated_this_way(self, client, db):
        platform = factories.make_user(db, "platform", role_code="platform_admin", password_hash="")
        token = auth_service.issue_token(db, platform, auth_service.ACTIVATE)
        db.commit()
        r = client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD})
        assert r.status_code == 400 and r.json()["code"] == "invalid_link"
        db.refresh(platform)
        assert platform.password_hash == ""

    def test_activation_is_audited(self, client, client_as, db, eca, outbox):
        _, token = self._invite(client_as, eca[1], outbox)
        client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD})
        assert db.scalars(select(AuditLog).where(AuditLog.action == "account.activated")).one() is not None

    def test_recycler_activation_still_works(self, client, client_as, db, association_admin, outbox):
        pending = factories.make_user(db, "recycler", password_hash="", verification_status="pending")
        client_as(association_admin).patch(f"/users/{pending.id}/verification-status", json={"status": "verified"})
        token = _token_from(outbox[-1])
        assert client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD}).status_code == 200


class TestWhoMayInvite:
    @pytest.mark.parametrize("kind", ["eca_operator", "eca_warehouse", "association_operator", "route_manager"])
    def test_other_staff_roles_cannot(self, client_as, db, kind):
        role_type = "association" if kind in ("association_operator", "route_manager") else "eca"
        staff = factories.make_user(db, role_type, role_code=kind)
        assert client_as(staff).post("/users/invitations", json=_payload()).status_code == 403

    def test_recyclers_and_citizens_cannot(self, client_as, db):
        for user in (factories.make_user(db, "recycler"), factories.make_user(db, "citizen")):
            assert client_as(user).post("/users/invitations", json=_payload()).status_code == 403

    def test_anonymous_callers_cannot(self, client):
        assert client.post("/users/invitations", json=_payload()).status_code in (401, 403)

    def test_a_somos_r_token_is_not_a_customer_token(self, client, db):
        platform = factories.make_user(db, "platform", role_code="platform_admin")
        r = TestClient(app, headers=factories.backoffice_headers(platform)).post("/users/invitations", json=_payload())
        assert r.status_code == 401


class TestWhatCanBeGrantedAndTo(object):
    def test_only_roles_of_the_admins_own_organization(self, client_as, eca):
        r = client_as(eca[1]).post("/users/invitations", json=_payload("association_admin"))
        assert r.status_code == 422 and r.json()["code"] == "invalid_role"

    def test_an_association_cannot_invite_eca_roles(self, client_as, assoc):
        assert client_as(assoc[1]).post("/users/invitations", json=_payload("eca_operator")).status_code == 422

    def test_the_somos_r_role_can_never_be_handed_out(self, client_as, eca):
        r = client_as(eca[1]).post("/users/invitations", json=_payload("platform_admin"))
        assert r.status_code == 422 and r.json()["code"] == "invalid_role"

    def test_an_unknown_or_inactive_role(self, client_as, db, eca):
        assert client_as(eca[1]).post("/users/invitations", json=_payload("no_existe")).status_code == 422
        db.get(Role, "eca_warehouse").is_active = False
        db.commit()
        assert client_as(eca[1]).post("/users/invitations", json=_payload("eca_warehouse")).status_code == 422

    def test_an_unknown_document_type(self, client_as, eca):
        r = client_as(eca[1]).post("/users/invitations", json=_payload(id_type="ZZZ"))
        assert r.status_code == 422 and r.json()["code"] == "invalid_id_type"

    def test_an_admin_can_invite_another_admin(self, client_as, eca):
        assert client_as(eca[1]).post("/users/invitations", json=_payload("eca_admin")).status_code == 201


class TestConflictsAndInvalidInput:
    def test_a_repeated_email_or_document(self, client_as, eca):
        first = _payload()
        assert client_as(eca[1]).post("/users/invitations", json=first).status_code == 201
        dup_email = client_as(eca[1]).post("/users/invitations", json=_payload(email=first["email"]))
        dup_doc = client_as(eca[1]).post("/users/invitations", json=_payload(id_number=first["id_number"]))
        assert dup_email.status_code == dup_doc.status_code == 409
        assert dup_email.json()["code"] == "account_already_exists"

    def test_a_failed_invitation_sends_nothing(self, client_as, eca, outbox):
        client_as(eca[1]).post("/users/invitations", json=_payload(id_type="ZZZ"))
        assert outbox == []

    @pytest.mark.parametrize("bad", [{"email": "no-es-correo"}, {"full_name": "A"}, {"id_number": "1"},
                                     {"role_code": ""}, {"phone": "9" * 30}])
    def test_malformed_input(self, client_as, eca, bad):
        assert client_as(eca[1]).post("/users/invitations", json=_payload(**bad)).status_code == 422


class TestOrganizationMustBeAbleToOperate:
    def test_an_admin_with_no_organization_cannot_invite(self, client_as, db):
        orphan = factories.make_user(db, "eca", role_code="eca_admin", organization_id=None)
        r = client_as(orphan).post("/users/invitations", json=_payload())
        assert r.status_code == 403 and r.json()["code"] == "no_organization"

    @pytest.mark.parametrize("state", [OrganizationStatus.draft, OrganizationStatus.submitted,
                                       OrganizationStatus.rejected, OrganizationStatus.suspended])
    def test_only_an_approved_organization_can(self, client_as, db, state):
        org = factories.make_organization(db, "eca", status=state)
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        r = client_as(admin).post("/users/invitations", json=_payload())
        assert r.status_code == 403 and r.json()["code"] == "organization_not_active"


class TestResending:
    def _invited(self, client_as, admin, outbox):
        user_id = client_as(admin).post("/users/invitations", json=_payload()).json()["id"]
        return user_id, _token_from(outbox[-1])

    def test_a_new_link_replaces_the_old_one(self, client, client_as, eca, outbox):
        user_id, old = self._invited(client_as, eca[1], outbox)
        r = client_as(eca[1]).post(f"/users/{user_id}/invitation/resend")
        assert r.status_code == 200, r.text
        new = _token_from(outbox[-1])
        assert new != old and len(outbox) == 2
        assert client.post("/auth/activate", json={"token": old, "password": NEW_PASSWORD}).status_code == 400
        assert client.post("/auth/activate", json={"token": new, "password": NEW_PASSWORD}).status_code == 200

    def test_it_is_audited(self, client_as, db, eca, outbox):
        user_id, _ = self._invited(client_as, eca[1], outbox)
        client_as(eca[1]).post(f"/users/{user_id}/invitation/resend")
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.invitation_resent")).one()
        assert entry.target_id == user_id and entry.actor_id == eca[1].id

    def test_it_is_refused_once_the_person_has_activated(self, client, client_as, eca, outbox):
        user_id, token = self._invited(client_as, eca[1], outbox)
        client.post("/auth/activate", json={"token": token, "password": NEW_PASSWORD})
        r = client_as(eca[1]).post(f"/users/{user_id}/invitation/resend")
        assert r.status_code == 409 and r.json()["code"] == "invitation_not_pending"

    def test_someone_elses_invitee_looks_like_a_missing_user(self, client_as, db, eca, outbox):
        user_id, _ = self._invited(client_as, eca[1], outbox)
        other_org = factories.make_organization(db, "eca")
        other_admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=other_org.id)
        r = client_as(other_admin).post(f"/users/{user_id}/invitation/resend")
        missing = client_as(other_admin).post("/users/00000000-0000-0000-0000-000000000000/invitation/resend")
        assert r.status_code == 404 and r.json() == missing.json()

    def test_it_only_applies_to_staff_never_to_recyclers(self, client_as, db, association_admin):
        recycler = factories.make_user(db, "recycler", password_hash="")
        r = client_as(association_admin).post(f"/users/{recycler.id}/invitation/resend")
        assert r.status_code == 404

    def test_only_admins_may_resend(self, client_as, db, eca, outbox):
        user_id, _ = self._invited(client_as, eca[1], outbox)
        operator = factories.make_user(db, "eca", role_code="eca_operator", organization_id=eca[0].id)
        assert client_as(operator).post(f"/users/{user_id}/invitation/resend").status_code == 403


class TestLimits:
    def test_inviting_is_rate_limited(self, client_as, eca, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_register", "2/minute")
        limiter.reset()
        try:
            statuses = [client_as(eca[1]).post("/users/invitations", json=_payload()).status_code for _ in range(4)]
            assert statuses == [201, 201, 429, 429]
        finally:
            limiter.reset()


class TestWhatTheWebNeeds:
    def test_only_organization_admins_are_told_they_can_invite_and_see_staff(self, client_as, db, eca):
        caps = client_as(eca[1]).get("/auth/me").json()["capabilities"]
        assert {"staff.invite", "staff.view"} <= set(caps)
        operator = factories.make_user(db, "eca", role_code="eca_operator", organization_id=eca[0].id)
        caps = client_as(operator).get("/auth/me").json()["capabilities"]
        assert not {"staff.invite", "staff.view"} & set(caps)

    def test_pending_activation_tells_an_invitee_from_someone_active(self, client_as, client, eca, outbox):
        payload = _payload()
        invited = client_as(eca[1]).post("/users/invitations", json=payload).json()
        assert invited["pending_activation"] is True
        items = {i["id"]: i for i in client_as(eca[1]).get("/users", params={"user_type_code": "eca"}).json()["items"]}
        assert items[invited["id"]]["pending_activation"] is True
        assert items[str(eca[1].id)]["pending_activation"] is False
        client.post("/auth/activate", json={"token": _token_from(outbox[-1]), "password": NEW_PASSWORD})
        assert client_as(eca[1]).get(f"/users/{invited['id']}").json()["pending_activation"] is False

    def test_someone_who_registered_alone_and_has_not_confirmed_their_email_is_not_pending(self, client_as, db, eca):
        unverified = factories.make_user(db, "eca", role_code="eca_operator", organization_id=eca[0].id,
                                         email_verified_at=None)
        assert client_as(eca[1]).get(f"/users/{unverified.id}").json()["pending_activation"] is False

    def test_the_list_can_be_narrowed_by_role(self, client_as, eca):
        client_as(eca[1]).post("/users/invitations", json=_payload("eca_warehouse"))
        items = client_as(eca[1]).get("/users", params={"user_type_code": "eca", "role_code": "eca_warehouse"}).json()["items"]
        assert items and all(i["role_code"] == "eca_warehouse" for i in items)

    def test_the_role_catalog_says_which_type_each_role_belongs_to(self, client):
        roles = {r["code"]: r["user_type_code"] for r in client.get("/catalogs/roles").json()}
        assert roles["eca_operator"] == "eca" and roles["association_admin"] == "association"
        assert "platform_admin" not in roles

    def test_the_model_flag_matches_the_types_activation_accepts(self):
        from app.domains.auth.service import ACTIVATABLE_TYPES

        assert ACTIVATABLE_TYPES == {"recycler", "eca", "association"}
