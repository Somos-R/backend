"""The backoffice audit viewer: the whole trail, across organizations, and itself audited."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import permissions
from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.audit.models import AuditLog
from app.main import app
from tests import factories


@pytest.fixture
def admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


@pytest.fixture
def bo(client, admin):
    """A client signed in to the backoffice (`client` wires the per-test database)."""
    return TestClient(app, headers=factories.backoffice_headers(admin))


def _get(bo, params=None):
    return bo.get("/admin/audit-log", params=params or {"limit": 200})


def _body(response):
    assert response.status_code == 200, response.text
    return response.json()


def _event(db, actor=None, target=None, target_type="user", action="auth.login", outcome="success", minute=0,
           request_id=None):
    db.add(AuditLog(
        action=action, outcome=outcome, actor_id=actor.id if actor else None,
        actor_role=getattr(actor, "role_code", None),
        target_type=target_type, target_id=str(target.id) if target else None,
        request_id=request_id, occurred_at=datetime(2026, 5, 1, 12, minute, tzinfo=timezone.utc)))
    db.commit()


@pytest.fixture
def orgs(db):
    eca = factories.make_organization(db, "eca")
    assoc = factories.make_organization(db, "association")
    return (eca, assoc,
            factories.make_user(db, "eca", role_code="eca_admin", organization_id=eca.id),
            factories.make_user(db, "association", role_code="association_admin", organization_id=assoc.id))


class TestWholeTrail:
    def test_it_spans_every_organization_and_somos_r_itself(self, bo, db, admin, orgs):
        _, _, eca_admin, assoc_admin = orgs
        _event(db, eca_admin, eca_admin, minute=1)
        _event(db, assoc_admin, assoc_admin, minute=2)
        _event(db, admin, admin, action="admin.login", minute=3)
        actors = {i["actor_id"] for i in _body(_get(bo))["items"]}
        assert {str(eca_admin.id), str(assoc_admin.id), str(admin.id)} <= actors

    def test_events_without_an_actor_or_an_account_are_included(self, bo, db):
        _event(db, None, None, target_type="weighing", action="weighing.created", minute=1)
        assert any(i["action"] == "weighing.created" for i in _body(_get(bo))["items"])

    def test_newest_first_with_a_stable_order(self, bo, db, orgs):
        _, _, eca_admin, _ = orgs
        for minute in (5, 7, 6):
            _event(db, eca_admin, eca_admin, minute=minute, request_id=f"m{minute}")
        ids = [i["request_id"] for i in _body(_get(bo, {"actor_id": str(eca_admin.id)}))["items"]]
        assert ids == ["m7", "m6", "m5"]

    def test_pagination_reports_the_exact_total(self, bo, db, orgs):
        _, _, eca_admin, _ = orgs
        for minute in range(5):
            _event(db, eca_admin, eca_admin, minute=minute)
        page = _body(_get(bo, {"actor_id": str(eca_admin.id), "limit": 2, "offset": 2}))
        assert page["total"] == 5 and len(page["items"]) == 2 and page["limit"] == 2 and page["offset"] == 2


class TestFilters:
    def test_the_customer_filters_work(self, bo, db, orgs):
        _, _, eca_admin, assoc_admin = orgs
        _event(db, eca_admin, eca_admin, action="auth.login", minute=1, request_id="r1")
        _event(db, assoc_admin, assoc_admin, action="auth.logout", minute=2, request_id="r2")
        _event(db, None, assoc_admin, action="auth.login_failed", outcome="failure", minute=3, request_id="r3")
        assert {i["request_id"] for i in _body(_get(bo, {"action": "auth.logout"}))["items"]} == {"r2"}
        failed = _body(_get(bo, {"outcome": "failure", "target_id": str(assoc_admin.id)}))["items"]
        assert {i["request_id"] for i in failed} == {"r3"}
        assert {i["request_id"] for i in _body(_get(bo, {"request_id": "r1"}))["items"]} == {"r1"}

    def test_by_role(self, bo, db, orgs):
        _, _, eca_admin, assoc_admin = orgs
        _event(db, eca_admin, eca_admin, minute=1, request_id="e")
        _event(db, assoc_admin, assoc_admin, minute=2, request_id="a")
        got = {i["request_id"] for i in _body(_get(bo, {"actor_role": "association_admin"}))["items"]}
        assert "a" in got and "e" not in got

    def test_by_organization(self, bo, db, orgs):
        eca, assoc, eca_admin, assoc_admin = orgs
        _event(db, eca_admin, eca_admin, minute=1, request_id="e")
        _event(db, assoc_admin, assoc_admin, minute=2, request_id="a")
        _event(db, None, assoc_admin, action="auth.login_failed", outcome="failure", minute=3, request_id="af")
        assert {i["request_id"] for i in _body(_get(bo, {"organization_id": str(assoc.id)}))["items"]} == {"a", "af"}
        assert {i["request_id"] for i in _body(_get(bo, {"organization_id": str(eca.id)}))["items"]} == {"e"}

    def test_an_organization_with_no_events_is_empty(self, bo, db):
        empty = factories.make_organization(db, "eca")
        assert _body(_get(bo, {"organization_id": str(empty.id)}))["total"] == 0

    def test_by_date_range(self, bo, db, orgs):
        _, _, eca_admin, _ = orgs
        for minute in (1, 2, 3):
            _event(db, eca_admin, eca_admin, minute=minute, request_id=f"t{minute}")
        since = datetime(2026, 5, 1, 12, 2, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        got = {i["request_id"] for i in _body(_get(bo, {"actor_id": str(eca_admin.id), "since": since}))["items"]}
        assert got == {"t2", "t3"}

    @pytest.mark.parametrize("params", [{"limit": 201}, {"limit": 0}, {"offset": -1}, {"outcome": "maybe"},
                                        {"organization_id": "no-es-uuid"}, {"actor_id": "no-es-uuid"}])
    def test_invalid_parameters_are_rejected(self, bo, params):
        assert _get(bo, params).status_code == 422


class TestReadingItIsAudited:
    def _viewed(self, db, admin):
        return db.scalars(select(AuditLog).where(
            AuditLog.action == "admin.audit_viewed", AuditLog.actor_id == admin.id)).all()

    def test_each_query_leaves_an_entry_with_the_filters_used_but_not_their_values(self, bo, db, admin):
        _get(bo, {"action": "auth.login", "limit": 10})
        (entry,) = self._viewed(db, admin)
        assert entry.details == {"filters": ["action"], "limit": 10, "offset": 0}
        assert entry.target_type == "audit_log" and entry.outcome == "success"
        assert "auth.login" not in str(entry.details)

    def test_the_organization_filter_is_named_too(self, bo, db, admin, orgs):
        eca, *_ = orgs
        _get(bo, {"organization_id": str(eca.id), "outcome": "success"})
        (entry,) = self._viewed(db, admin)
        assert entry.details["filters"] == ["organization_id", "outcome"]
        assert str(eca.id) not in str(entry.details)

    def test_it_shows_up_in_the_next_reading(self, bo, admin):
        _get(bo)
        again = _body(_get(bo, {"action": "admin.audit_viewed"}))
        assert again["total"] >= 1 and again["items"][0]["actor_id"] == str(admin.id)

    def test_a_refused_reader_leaves_no_entry(self, client, db, eca_admin):
        before = db.scalars(select(AuditLog).where(AuditLog.action == "admin.audit_viewed")).all()
        r = TestClient(app, headers=factories.auth_headers(eca_admin)).get("/admin/audit-log")
        assert r.status_code == 401
        assert db.scalars(select(AuditLog).where(AuditLog.action == "admin.audit_viewed")).all() == before


class TestWhoMayRead:
    def test_customers_cannot_use_the_backoffice_route(self, client_as, eca_admin, association_admin):
        for user in (eca_admin, association_admin):
            r = client_as(user).get("/admin/audit-log")
            assert r.status_code == 401 and r.json()["code"] == "invalid_token"

    def test_a_platform_users_portal_token_is_not_enough(self, client_as, admin):
        assert client_as(admin).get("/admin/audit-log").status_code == 401

    def test_without_a_token(self, client):
        assert client.get("/admin/audit-log").status_code in (401, 403)

    def test_the_capability_is_what_decides(self, bo, monkeypatch):
        monkeypatch.setitem(permissions.PLATFORM_CAPABILITIES, "audit.read", frozenset())
        r = _get(bo)
        assert r.status_code == 403 and r.json()["code"] == "forbidden"

    def test_the_customer_route_does_not_take_a_backoffice_token(self, bo):
        assert bo.get("/audit-log").status_code == 401

    def test_the_customer_route_is_still_scoped_to_the_organization(self, client_as, db, orgs):
        _, _, eca_admin, assoc_admin = orgs
        _event(db, eca_admin, eca_admin, minute=1)
        assert client_as(assoc_admin).get("/audit-log").json()["total"] == 0


class TestBackofficeRules:
    def test_it_respects_the_network_restriction(self, client, admin, monkeypatch):
        monkeypatch.setattr(settings, "admin_allowed_cidrs", "203.0.113.0/24")
        headers = factories.backoffice_headers(admin)
        outside = TestClient(app, headers=headers, client=("192.0.2.1", 5000))
        inside = TestClient(app, headers=headers, client=("203.0.113.9", 5000))
        assert outside.get("/admin/audit-log").status_code == 403
        assert inside.get("/admin/audit-log").status_code == 200

    def test_it_has_the_backoffice_rate_limit(self, bo, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_admin", "3/minute")
        limiter.reset()
        try:
            assert [_get(bo).status_code for _ in range(5)] == [200, 200, 200, 429, 429]
        finally:
            limiter.reset()

    def test_admin_me_lists_the_capability(self, bo):
        assert "audit.read" in bo.get("/admin/me").json()["capabilities"]
