"""The audit trail (task 4.8): what is recorded, what is never recorded, and that it cannot be edited."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.context import client_ip_var, request_id_var
from app.core.database import get_db
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.audit.models import AuditLog
from app.domains.users.enums import VerificationStatus
from app.main import app
from tests import factories
from tests.factories import DEFAULT_PASSWORD, make_actor


def rows(db, action=None):
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    return query.order_by(AuditLog.occurred_at, AuditLog.id).all()


def only(db, action):
    found = rows(db, action)
    assert len(found) == 1, f"expected exactly one {action}, found {[r.action for r in rows(db)]}"
    return found[0]


def login(client, user, password=DEFAULT_PASSWORD):
    return client.post("/auth/login", json={"email": user.email, "password": password})


# --- The table cannot be rewritten -------------------------------------------------------------

class TestAppendOnly:
    @pytest.fixture
    def entry(self, db):
        row = AuditLog(action="test.event", details={"a": 1})
        db.add(row)
        db.commit()
        return row

    def test_update_is_rejected_by_the_database(self, db, entry):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("UPDATE audit_log SET action = 'forged' WHERE id = :id"), {"id": entry.id})
        db.rollback()

    def test_delete_is_rejected_by_the_database(self, db, entry):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": entry.id})
        db.rollback()

    def test_editing_through_the_orm_is_rejected_too(self, db, entry):
        entry.action = "forged"
        with pytest.raises(DBAPIError, match="append-only"):
            db.flush()
        db.rollback()

    def test_bulk_deletes_are_rejected(self, db, entry):
        with pytest.raises(DBAPIError, match="append-only"):
            db.query(AuditLog).delete()
        db.rollback()

    def test_the_row_survives_all_of_that(self, db, entry):
        assert db.get(AuditLog, entry.id).action == "test.event"

    def test_outcome_must_be_success_or_failure(self, db):
        db.add(AuditLog(action="x", outcome="maybe"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

    def test_actor_and_target_are_not_foreign_keys(self, db):
        # The trail must outlive the records it describes.
        db.add(AuditLog(action="x", actor_id=uuid.uuid4(), target_type="user", target_id=str(uuid.uuid4())))
        db.flush()


# --- The recording service ---------------------------------------------------------------------

class TestRecord:
    def test_fills_the_actor_from_the_user(self, db):
        staff = make_actor(db, "eca_operator")
        entry = audit.record(db, "x.y", actor=staff, target_type="thing", target_id=uuid.uuid4())
        db.flush()
        assert entry.actor_id == staff.id and entry.actor_role == "eca_operator"
        assert entry.target_type == "thing" and len(entry.target_id) == 36

    def test_actors_without_a_role_are_recorded_by_their_type(self, db):
        recycler = factories.make_user(db, "recycler")
        assert audit.record(db, "x.y", actor=recycler).actor_role == "recycler"

    def test_anonymous_events_have_no_actor(self, db):
        entry = audit.record(db, "x.y")
        assert entry.actor_id is None and entry.actor_role is None

    def test_ip_and_request_id_come_from_the_request_context(self, db):
        request_id_var.set("req-123")
        client_ip_var.set("203.0.113.9")
        try:
            entry = audit.record(db, "x.y")
        finally:
            request_id_var.set(None)
            client_ip_var.set(None)
        assert (entry.request_id, entry.ip) == ("req-123", "203.0.113.9")

    def test_it_rolls_back_together_with_the_action(self, db):
        audit.record(db, "phantom.event")
        db.rollback()
        assert rows(db, "phantom.event") == []

    def test_it_commits_together_with_the_action(self, db):
        audit.record(db, "real.event")
        db.commit()
        assert len(rows(db, "real.event")) == 1


class TestSanitize:
    def test_secrets_are_masked_whatever_the_case_or_nesting(self):
        cleaned = audit.sanitize({
            "password": "hunter2", "new_password": "x", "Refresh_Token": "abc",
            "password_hash": "$2b$", "Authorization": "Bearer abc", "api_key": "k",
            "nested": {"token": "t", "safe": "ok"}, "list": [{"secret": "s"}],
        })
        assert cleaned["password"] == cleaned["new_password"] == cleaned["Refresh_Token"] == audit.REDACTED
        assert cleaned["password_hash"] == cleaned["Authorization"] == cleaned["api_key"] == audit.REDACTED
        assert cleaned["nested"] == {"token": audit.REDACTED, "safe": "ok"}
        assert cleaned["list"] == [{"secret": audit.REDACTED}]
        assert "hunter2" not in json.dumps(cleaned)

    def test_values_are_made_json_safe(self):
        cleaned = audit.sanitize({"id": uuid.UUID(int=1), "kg": Decimal("1.50"),
                                  "at": datetime(2026, 1, 1, tzinfo=timezone.utc), "s": {1, 2}})
        json.dumps(cleaned)
        assert cleaned["kg"] == "1.50" and cleaned["id"].endswith("0001")

    def test_long_strings_are_truncated(self):
        cleaned = audit.sanitize({"note": "x" * 1000})
        assert len(cleaned["note"]) == audit.MAX_STRING + 1

    def test_oversized_details_collapse(self):
        assert audit.sanitize({f"k{i}": "v" * 150 for i in range(80)}) == {"truncated": True}

    @pytest.mark.parametrize("empty", [None, {}])
    def test_empty_is_an_empty_object(self, empty):
        assert audit.sanitize(empty) == {}


# --- Authentication events -----------------------------------------------------------------------

class TestAuthEvents:
    def test_registration(self, client, db):
        r = client.post("/auth/register", json={
            "user_type_code": "citizen", "email": "nuevo@test.com", "password": DEFAULT_PASSWORD,
            "full_name": "Nuevo", "id_type": "CC", "id_number": "4455667788"})
        entry = only(db, Action.USER_REGISTERED)
        assert entry.actor_id is None and entry.target_id == r.json()["id"]
        assert entry.details == {"user_type": "citizen", "role_code": None}

    def test_registering_staff_records_who_did_it(self, client_as, eca_admin, db):
        client_as(eca_admin).post("/auth/register", json={
            "user_type_code": "eca", "role_code": "eca_operator", "email": "staff@test.com",
            "password": DEFAULT_PASSWORD, "full_name": "Staff", "id_type": "CC", "id_number": "1122334455"})
        entry = only(db, Action.USER_REGISTERED)
        assert entry.actor_id == eca_admin.id and entry.details["role_code"] == "eca_operator"

    def test_failed_registration_leaves_no_trace(self, client, db, citizen):
        client.post("/auth/register", json={
            "user_type_code": "citizen", "email": citizen.email, "password": DEFAULT_PASSWORD,
            "full_name": "Dup", "id_type": "CC", "id_number": "99887766"})
        assert rows(db, Action.USER_REGISTERED) == []

    def test_successful_login(self, client, db, citizen):
        login(client, citizen)
        entry = only(db, Action.LOGIN)
        assert entry.actor_id == citizen.id and entry.outcome == "success"
        assert entry.ip == "testclient"

    @pytest.mark.parametrize("scenario,reason", [
        ("unknown", "unknown_account"), ("bad_password", "bad_password"), ("locked", "locked"),
        ("inactive", "inactive"), ("pending", "pending_verification"), ("no_password", "no_password"),
    ])
    def test_failed_logins_record_the_real_reason(self, client, db, scenario, reason):
        user = None
        if scenario == "unknown":
            response = client.post("/auth/login", json={"email": "nadie@test.com", "password": "x"})
        elif scenario == "bad_password":
            user = factories.make_user(db, "citizen")
            response = login(client, user, "incorrecta")
        elif scenario == "locked":
            user = factories.make_user(db, "citizen",
                                       locked_until=datetime.now(timezone.utc) + timedelta(minutes=5))
            response = login(client, user)
        elif scenario == "inactive":
            user = factories.make_user(db, "citizen", is_active=False)
            response = login(client, user)
        elif scenario == "pending":
            user = factories.make_user(db, "recycler", verification_status=VerificationStatus.pending)
            response = login(client, user)
        else:
            user = factories.make_user(db, "recycler", password_hash="")
            response = login(client, user, "")

        entry = only(db, Action.LOGIN_FAILED)
        assert entry.outcome == "failure" and entry.details["reason"] == reason
        assert entry.target_id == (str(user.id) if user else None)
        # The client never learns the reason (401 is uniform; 403 is the existing, documented one).
        assert response.status_code in (401, 403)

    def test_the_attempted_email_and_password_are_never_stored(self, client, db):
        client.post("/auth/login", json={"email": "secreto@privado.com", "password": "ClaveMuySecreta1"})  # gitleaks:allow  (fake test password)
        dump = json.dumps([(r.details, r.target_id, r.action) for r in rows(db)])
        assert "secreto@privado.com" not in dump and "ClaveMuySecreta1" not in dump

    def test_a_failed_login_is_committed_even_though_the_request_fails(self, engine):
        """Uses real connections: the entry must survive the 401, not just exist in the session."""
        with Session(engine) as setup:
            user = factories.make_user(setup, "citizen")
            email = user.email
        try:
            response = TestClient(app).post(
                "/auth/login", json={"email": email, "password": "incorrecta"})
            assert response.status_code == 401
            with Session(engine) as check:
                entries = check.query(AuditLog).filter_by(action=Action.LOGIN_FAILED).all()
                assert len(entries) == 1 and entries[0].details["reason"] == "bad_password"
        finally:
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE users, audit_log CASCADE"))

    def test_logout(self, client_as, db, citizen):
        client_as(citizen).post("/auth/logout")
        assert only(db, Action.LOGOUT).actor_id == citizen.id

    def test_change_password_and_a_wrong_current_password(self, client_as, db, citizen):
        c = client_as(citizen)
        c.post("/auth/change-password", json={"current_password": "mala", "new_password": "Nueva-Clave-2026"})
        failed = only(db, Action.PASSWORD_CHANGE_FAILED)
        assert failed.outcome == "failure" and failed.actor_id == citizen.id
        assert rows(db, Action.PASSWORD_CHANGED) == []

        c.post("/auth/change-password",
               json={"current_password": DEFAULT_PASSWORD, "new_password": "Nueva-Clave-2026"})
        assert only(db, Action.PASSWORD_CHANGED).actor_id == citizen.id

    def test_password_reset_flow(self, client, db, citizen, outbox):
        client.post("/auth/forgot-password", json={"email": citizen.email})
        assert only(db, Action.PASSWORD_RESET_REQUESTED).target_id == str(citizen.id)

        token = outbox[0].body.split("token=")[1].split()[0]
        client.post("/auth/reset-password", json={"token": token, "password": "Nueva-Clave-2026"})
        assert only(db, Action.PASSWORD_RESET).actor_id == citizen.id
        assert token not in json.dumps([r.details for r in rows(db)])

    def test_reset_requests_for_unknown_emails_are_not_recorded(self, client, db):
        client.post("/auth/forgot-password", json={"email": "nadie@test.com"})
        assert rows(db) == []

    def test_email_verification(self, client, db, outbox):
        client.post("/auth/register", json={
            "user_type_code": "citizen", "email": "ver@test.com", "password": DEFAULT_PASSWORD,
            "full_name": "Ver", "id_type": "CC", "id_number": "5566778899"})
        token = outbox[0].body.split("token=")[1].split()[0]
        client.post("/auth/verify-email", json={"token": token})
        assert only(db, Action.EMAIL_VERIFIED).outcome == "success"

    def test_account_activation(self, client, client_as, association_admin, db, outbox):
        recycler = factories.make_user(
            db, "recycler", verification_status=VerificationStatus.pending, password_hash="")
        client_as(association_admin).patch(
            f"/users/{recycler.id}/verification-status", json={"status": "verified"})
        token = outbox[0].body.split("token=")[1].split()[0]
        client.post("/auth/activate", json={"token": token, "password": "Reciclaje-2026!"})
        assert only(db, Action.ACCOUNT_ACTIVATED).actor_id == recycler.id


class TestRefreshTokenAbuse:
    def test_reuse_of_a_rotated_token_is_flagged_as_likely_theft(self, client, db, citizen):
        first = login(client, citizen).json()["refresh_token"]
        client.post("/auth/refresh", json={"refresh_token": first})
        client.post("/auth/refresh", json={"refresh_token": first})  # the replay

        entry = only(db, Action.REFRESH_REUSE_DETECTED)
        assert entry.outcome == "failure" and entry.target_id == str(citizen.id)
        assert entry.details["reason"] == "rotated_token_reused" and "family_id" in entry.details
        assert first not in json.dumps(entry.details)

    def test_a_revoked_token_after_logout_is_only_a_denial(self, client, db, citizen):
        pair = login(client, citizen).json()
        client.post("/auth/logout", headers={"Authorization": f"Bearer {pair['access_token']}"})
        client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
        assert rows(db, Action.REFRESH_REUSE_DETECTED) == []
        assert only(db, Action.REFRESH_DENIED).details["reason"] == "revoked"

    def test_a_deactivated_account_is_denied(self, client, db, citizen):
        token = login(client, citizen).json()["refresh_token"]
        citizen.is_active = False
        db.commit()
        client.post("/auth/refresh", json={"refresh_token": token})
        assert only(db, Action.REFRESH_DENIED).details["reason"] == "account_inactive_or_unverified"

    def test_ordinary_refreshes_are_not_recorded(self, client, db, citizen):
        token = login(client, citizen).json()["refresh_token"]
        client.post("/auth/refresh", json={"refresh_token": token})
        assert [r.action for r in rows(db)] == [Action.LOGIN]


# --- User administration ---------------------------------------------------------------------------

class TestUserEvents:
    @staticmethod
    def _pending(db):
        return factories.make_user(
            db, "recycler", verification_status=VerificationStatus.pending, password_hash="")

    def test_verifying_and_rejecting_a_recycler(self, client_as, association_admin, db):
        a, b = self._pending(db), self._pending(db)
        c = client_as(association_admin)
        c.patch(f"/users/{a.id}/verification-status", json={"status": "verified"})
        c.patch(f"/users/{b.id}/verification-status",
                json={"status": "rejected", "rejection_reason": "Documento con datos personales 12345"})

        verified = only(db, Action.RECYCLER_VERIFIED)
        assert verified.actor_id == association_admin.id and verified.target_id == str(a.id)
        assert verified.details == {"activation_email_queued": True, "has_reason": False}
        rejected = only(db, Action.RECYCLER_REJECTED)
        assert rejected.details["has_reason"] is True
        assert "12345" not in json.dumps(rejected.details)  # free text is never copied into the trail

    def test_profile_updates_record_field_names_not_values(self, client_as, db, citizen):
        client_as(citizen).patch(f"/users/{citizen.id}", json={"phone": "3119998877", "address": "Calle 1"})
        entry = only(db, Action.USER_UPDATED)
        assert entry.details == {"fields": ["address", "phone"], "self": True}
        assert "3119998877" not in json.dumps(entry.details)

    def test_an_empty_update_records_nothing(self, client_as, db, citizen):
        client_as(citizen).patch(f"/users/{citizen.id}", json={})
        assert rows(db) == []

    def test_role_changes_record_before_and_after(self, client_as, eca_admin, db):
        staff = factories.make_user(db, "eca", role_code="eca_operator")
        client_as(eca_admin).patch(f"/users/{staff.id}", json={"role_code": "eca_warehouse"})
        entry = only(db, Action.USER_ROLE_CHANGED)
        assert entry.actor_id == eca_admin.id and entry.target_id == str(staff.id)
        assert entry.details == {"from": "eca_operator", "to": "eca_warehouse"}

    def test_a_denied_role_change_records_nothing(self, client_as, citizen, db):
        client_as(citizen).patch(f"/users/{citizen.id}", json={"role_code": "eca_admin"})
        assert rows(db) == []


# --- Weighings, transactions and inventory -----------------------------------------------------------

def _weighing(client, recycler, warehouse):
    return client.post("/weighings", json={
        "recycler_id": str(recycler.id), "material_code": "plastico",
        "warehouse_id": str(warehouse.id), "kg": "12.5", "precio_kg": "400"}).json()


class TestBusinessEvents:
    def test_weighing_lifecycle(self, client_as, eca_admin, recycler, warehouse, db):
        c = client_as(eca_admin)
        w = _weighing(c, recycler, warehouse)
        created = only(db, Action.WEIGHING_CREATED)
        assert created.actor_id == eca_admin.id and created.target_id == w["id"]
        assert created.details["kg"] == "12.50" or created.details["kg"] == "12.5"

        c.patch(f"/weighings/{w['id']}/status", json={"status": "validado"})
        c.patch(f"/weighings/{w['id']}/status", json={"status": "pagado"})
        assert only(db, Action.WEIGHING_VALIDATED).details["from"] == "pendiente"
        assert only(db, Action.WEIGHING_PAID).details["from"] == "validado"

    def test_rejection(self, client_as, eca_admin, recycler, warehouse, db):
        c = client_as(eca_admin)
        w = _weighing(c, recycler, warehouse)
        c.patch(f"/weighings/{w['id']}/status", json={"status": "rechazado", "rejection_reason": "sucio"})
        assert only(db, Action.WEIGHING_REJECTED).target_id == w["id"]

    def test_a_failed_transition_records_nothing(self, client_as, eca_admin, recycler, warehouse, db):
        c = client_as(eca_admin)
        w = _weighing(c, recycler, warehouse)
        c.patch(f"/weighings/{w['id']}/status", json={"status": "validado"})
        again = c.patch(f"/weighings/{w['id']}/status", json={"status": "validado"})
        assert again.status_code == 400
        assert len(rows(db, Action.WEIGHING_VALIDATED)) == 1

    def test_sale_lifecycle(self, client_as, eca_admin, warehouse, db):
        factories.stock(db, warehouse, kg="100")
        c = client_as(eca_admin)
        body = {"material_code": "plastico", "warehouse_id": str(warehouse.id), "kg": "10", "precio_kg": "900"}
        first = c.post("/transactions", json=body).json()
        second = c.post("/transactions", json=body).json()
        c.patch(f"/transactions/{first['id']}/status", json={"status": "cancelado"})
        c.patch(f"/transactions/{second['id']}/status", json={"status": "entregado"})

        assert len(rows(db, Action.TRANSACTION_CREATED)) == 2
        assert only(db, Action.TRANSACTION_CANCELLED).target_id == first["id"]
        delivered = only(db, Action.TRANSACTION_DELIVERED)
        assert delivered.details["from"] == "pendiente" and delivered.details["type"] == "venta"

    def test_paying_a_purchase(self, client_as, eca_admin, recycler, warehouse, db):
        c = client_as(eca_admin)
        w = _weighing(c, recycler, warehouse)
        c.patch(f"/weighings/{w['id']}/status", json={"status": "validado"})
        purchase = c.get("/transactions?type=compra").json()["items"][0]
        c.patch(f"/transactions/{purchase['id']}/status", json={"status": "pagado"})
        assert only(db, Action.TRANSACTION_PAID).details["type"] == "compra"

    def test_a_refused_sale_records_nothing(self, client_as, eca_admin, warehouse, db):
        factories.stock(db, warehouse, kg="5")
        r = client_as(eca_admin).post("/transactions", json={
            "material_code": "plastico", "warehouse_id": str(warehouse.id), "kg": "50", "precio_kg": "900"})
        assert r.status_code == 400 and rows(db, Action.TRANSACTION_CREATED) == []

    def test_inventory_changes_record_old_and_new_values(self, client_as, eca_admin, warehouse, db):
        item = factories.stock(db, warehouse, kg="100", precio_kg="500")
        client_as(eca_admin).patch(f"/inventory/{item.id}", json={"precio_kg": "750", "stock_min_kg": "20"})
        entry = only(db, Action.INVENTORY_UPDATED)
        assert entry.details["changes"] == {"precio_kg": ["500.00", "750"], "stock_min_kg": ["50.00", "20"]}
        assert entry.details["material"] == "plastico"

    def test_an_inventory_patch_that_changes_nothing_records_nothing(self, client_as, eca_admin, warehouse, db):
        item = factories.stock(db, warehouse)
        client_as(eca_admin).patch(f"/inventory/{item.id}", json={})
        assert rows(db, Action.INVENTORY_UPDATED) == []

    def test_every_entry_carries_the_request_id_of_its_response(self, client_as, eca_admin, recycler, warehouse, db):
        r = client_as(eca_admin).post("/weighings", json={
            "recycler_id": str(recycler.id), "material_code": "plastico",
            "warehouse_id": str(warehouse.id), "kg": "1", "precio_kg": "1"})
        assert only(db, Action.WEIGHING_CREATED).request_id == r.headers["x-request-id"]

    def test_reads_are_not_audited(self, client_as, eca_admin, db):
        c = client_as(eca_admin)
        c.get("/weighings")
        c.get("/inventory")
        c.get("/transactions")
        assert rows(db) == []


# --- Reading the trail ---------------------------------------------------------------------------------

class TestAuditEndpoint:
    @pytest.fixture
    def seeded(self, db):
        actor_a, actor_b = uuid.uuid4(), uuid.uuid4()
        base = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
        data = [
            ("auth.login", "success", actor_a, "user", "u1", "req-1", 0),
            ("auth.login_failed", "failure", None, "user", "u1", "req-2", 1),
            ("weighing.validated", "success", actor_b, "weighing", "w1", "req-3", 2),
            ("weighing.paid", "success", actor_b, "weighing", "w1", "req-4", 3),
            ("auth.login", "success", actor_a, "user", "u2", "req-5", 4),
        ]
        for action, outcome, actor, ttype, tid, rid, day in data:
            db.add(AuditLog(action=action, outcome=outcome, actor_id=actor, target_type=ttype, target_id=tid,
                            request_id=rid, occurred_at=base + timedelta(days=day), details={"n": day}))
        db.commit()
        return {"actor_a": actor_a, "actor_b": actor_b, "base": base}

    def _get(self, client_as, admin, query=""):
        return client_as(admin).get(f"/audit-log{query}")

    def test_newest_first_with_the_expected_shape(self, client_as, association_admin, seeded):
        body = self._get(client_as, association_admin).json()
        assert body["total"] == 5 and body["limit"] == 50 and body["offset"] == 0
        assert [i["request_id"] for i in body["items"]] == ["req-5", "req-4", "req-3", "req-2", "req-1"]
        assert set(body["items"][0]) == {"id", "occurred_at", "action", "outcome", "actor_id", "actor_role",
                                         "target_type", "target_id", "ip", "request_id", "details"}

    @pytest.mark.parametrize("query,expected", [
        ("?action=auth.login", {"req-1", "req-5"}),
        ("?outcome=failure", {"req-2"}),
        ("?target_type=weighing", {"req-3", "req-4"}),
        ("?target_id=u1", {"req-1", "req-2"}),
        ("?request_id=req-3", {"req-3"}),
        ("?action=auth.login&target_id=u2", {"req-5"}),
        ("?action=nada", set()),
    ])
    def test_filters(self, client_as, association_admin, seeded, query, expected):
        items = self._get(client_as, association_admin, query).json()["items"]
        assert {i["request_id"] for i in items} == expected

    def test_filter_by_actor(self, client_as, association_admin, seeded):
        items = self._get(client_as, association_admin, f"?actor_id={seeded['actor_b']}").json()["items"]
        assert {i["request_id"] for i in items} == {"req-3", "req-4"}

    def test_filter_by_date_range(self, client_as, association_admin, seeded):
        since = (seeded["base"] + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        until = (seeded["base"] + timedelta(days=3)).isoformat().replace("+00:00", "Z")
        items = self._get(client_as, association_admin, f"?since={since}&until={until}").json()["items"]
        assert {i["request_id"] for i in items} == {"req-2", "req-3", "req-4"}

    def test_pagination(self, client_as, association_admin, seeded):
        page = self._get(client_as, association_admin, "?limit=2&offset=2").json()
        assert page["total"] == 5 and [i["request_id"] for i in page["items"]] == ["req-3", "req-2"]

    @pytest.mark.parametrize("query", ["?limit=201", "?limit=0", "?offset=-1", "?outcome=maybe",
                                       "?actor_id=no-es-uuid", "?since=ayer"])
    def test_invalid_parameters_are_rejected(self, client_as, association_admin, query):
        assert self._get(client_as, association_admin, query).status_code == 422

    def test_only_association_admins_may_read_it(self, client_as, db, seeded):
        for kind in ("citizen", "recycler", "eca_admin", "eca_operator", "association_operator", "route_manager"):
            assert client_as(make_actor(db, kind)).get("/audit-log").status_code == 403, kind

    def test_anonymous_callers_are_rejected(self, client):
        assert client.get("/audit-log").status_code in (401, 403)

    def test_it_exposes_no_write_endpoints(self, client_as, association_admin):
        c = client_as(association_admin)
        for method in ("post", "put", "patch", "delete"):
            assert getattr(c, method)("/audit-log").status_code == 405

    def test_a_real_workflow_is_readable_end_to_end(self, client_as, eca_admin, association_admin, recycler,
                                                    warehouse, db):
        w = _weighing(client_as(eca_admin), recycler, warehouse)
        r = self._get(client_as, association_admin, f"?target_id={w['id']}")
        assert [i["action"] for i in r.json()["items"]] == [Action.WEIGHING_CREATED]
        assert r.json()["items"][0]["actor_role"] == "eca_admin"


def test_the_dependency_override_does_not_leak_between_tests():
    assert get_db not in app.dependency_overrides
