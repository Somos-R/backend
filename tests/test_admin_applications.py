"""Review of applications from the backoffice (6.9, part 2): the queue, taking one, and deciding."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core import permissions
from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.applications.models import OrganizationApplication, OrganizationReview
from app.domains.audit.models import AuditLog
from app.domains.organizations.enums import OrganizationStatus
from app.domains.organizations.models import Organization
from app.domains.users.models import User
from app.main import app
from tests import factories

CHOSEN_PASSWORD = "Una-clave-nueva-2026"
COMPLETE = {
    "tax_id": "900123456-7", "legal_representative": "Rep Legal", "contact_email": "contacto@org.org",
    "contact_phone": "3150000000", "address": "Calle 1 # 2-3", "city": "Bogotá",
    "applicant_id_type": "CC", "applicant_id_number": "1020304050", "applicant_phone": "3001234567",
}
_n = iter(range(1, 100_000))


@pytest.fixture
def admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


@pytest.fixture
def bo(client, admin):
    return TestClient(app, headers=factories.backoffice_headers(admin))


@pytest.fixture
def other_bo(client, db):
    other = factories.make_user(db, "platform", role_code="platform_admin")
    return TestClient(app, headers=factories.backoffice_headers(other)), other


def _token(message) -> str:
    return message.body.split("token=")[1].split()[0]


def _apply(client, outbox, org_type="association", name="Asociación Esperanza", send=True, **overrides):
    """A public application, optionally sent. Returns (organization id, magic-link token)."""
    n = next(_n)
    r = client.post("/applications", json={
        "type": org_type, "legal_name": name, "applicant_name": "Laura Gómez",
        "applicant_email": f"laura{n}@org.org", "consent": True})
    assert r.status_code == 202, r.text
    token = _token(outbox[-1])
    headers = {"X-Application-Token": token}
    if send:
        fields = {**COMPLETE, "tax_id": f"9{n:08d}-1", "applicant_id_number": f"10{n:08d}", **overrides}
        assert client.patch("/applications/current", headers=headers, json=fields).status_code == 200
        factories.attach_required_documents(client, token)
        r = client.post("/applications/current/submit", headers=headers)
        assert r.status_code == 200, r.text
    return client.get("/applications/current", headers=headers).json()["id"], token


@pytest.fixture
def sent(client, outbox):
    return _apply(client, outbox)


def _approve_documents(bo, org_id):
    """The reviewer's verdict `ok` on every uploaded document (ignored if the request cannot be reviewed)."""
    detail = bo.get(f"/admin/applications/{org_id}").json()
    for slot in detail.get("documents", []):
        if slot["document"] and slot["document"]["status"] != "ok":
            bo.patch(f"/admin/applications/{org_id}/documents/{slot['document']['id']}", json={"status": "ok"})


def _decide(bo, org_id, decision, summary=None, documents_ok=True):
    if decision == "approve" and documents_ok:
        _approve_documents(bo, org_id)
    body = {"decision": decision}
    if summary is not None:
        body["summary"] = summary
    return bo.post(f"/admin/applications/{org_id}/decision", json=body)


def _audit(db, action, org_id):
    return db.scalars(select(AuditLog).where(AuditLog.action == action, AuditLog.target_id == str(org_id))).all()


class TestTheQueue:
    def test_by_default_it_lists_what_waits_or_is_being_reviewed_oldest_first(self, bo, client, outbox, db):
        draft, _ = _apply(client, outbox, name="Borrador", send=False)
        first, _ = _apply(client, outbox, name="Primera")
        second, _ = _apply(client, outbox, name="Segunda")
        ids = [i["id"] for i in bo.get("/admin/applications").json()["items"]]
        assert ids == [first, second] and draft not in ids

    def test_each_row_has_the_applicant_and_the_state(self, bo, sent):
        (row,) = bo.get("/admin/applications").json()["items"]
        assert row["id"] == sent[0] and row["status"] == "submitted" and row["type"] == "association"
        assert row["legal_name"] == "Asociación Esperanza" and row["applicant_name"] == "Laura Gómez"
        assert row["submission_count"] == 1 and row["submitted_at"] and row["reviewer"] is None

    def test_filters_by_status_type_and_text(self, bo, client, outbox):
        a, _ = _apply(client, outbox, name="Asociación Ñandú")
        e, _ = _apply(client, outbox, org_type="eca", name="ECA Zorro")
        bo.post(f"/admin/applications/{e}/start-review")
        ids = lambda **p: {i["id"] for i in bo.get("/admin/applications", params=p).json()["items"]}  # noqa: E731
        assert ids(type="eca") == {e} and ids(type="association") == {a}
        assert ids(status="in_review") == {e} and ids(status="submitted") == {a}
        assert ids(q="NANDU") == {a} and ids(q="zorro") == {e}

    def test_the_reviewer_is_shown_once_someone_takes_it(self, bo, admin, sent):
        bo.post(f"/admin/applications/{sent[0]}/start-review")
        (row,) = bo.get("/admin/applications").json()["items"]
        assert row["status"] == "in_review" and row["reviewer"]["id"] == str(admin.id)

    def test_pagination_reports_the_total(self, bo, client, outbox):
        for _ in range(3):
            _apply(client, outbox)
        page = bo.get("/admin/applications", params={"limit": 2, "offset": 0}).json()
        assert page["total"] == 3 and len(page["items"]) == 2

    def test_organizations_without_an_application_are_not_in_it(self, bo, db):
        factories.make_organization(db, "eca", status=OrganizationStatus.submitted)
        assert bo.get("/admin/applications").json()["total"] == 0


class TestTheDetail:
    def test_it_shows_the_organization_the_applicant_and_the_history(self, bo, sent):
        body = bo.get(f"/admin/applications/{sent[0]}").json()
        assert body["legal_representative"] == "Rep Legal" and body["address"] == "Calle 1 # 2-3"
        assert body["applicant_id_type"] == "CC" and body["applicant_phone"] == "3001234567"
        assert body["email_verified_at"] and body["consent_version"] == settings.application_consent_version
        assert body["reviews"] == []

    def test_opening_it_is_audited_but_deciding_does_not_add_a_view(self, bo, db, admin, sent):
        bo.get(f"/admin/applications/{sent[0]}")
        (entry,) = _audit(db, "admin.application_viewed", sent[0])
        assert entry.actor_id == admin.id
        _approve_documents(bo, sent[0])  # opens the detail: one more audited view, on purpose
        views = len(_audit(db, "admin.application_viewed", sent[0]))
        assert _decide(bo, sent[0], "approve", documents_ok=False).status_code == 200
        assert len(_audit(db, "admin.application_viewed", sent[0])) == views

    def test_an_organization_without_an_application_is_404(self, bo, db):
        org = factories.make_organization(db, "eca")
        r = bo.get(f"/admin/applications/{org.id}")
        assert r.status_code == 404 and r.json()["code"] == "application_not_found"


class TestTakingIt:
    def test_it_becomes_in_review_and_is_assigned(self, bo, db, admin, sent):
        r = bo.post(f"/admin/applications/{sent[0]}/start-review")
        assert r.status_code == 200 and r.json()["status"] == "in_review" and r.json()["reviewer"]["id"] == str(admin.id)
        assert r.json()["review_started_at"]
        assert len(_audit(db, "application.review_started", sent[0])) == 1

    def test_taking_your_own_again_changes_nothing(self, bo, db, sent):
        bo.post(f"/admin/applications/{sent[0]}/start-review")
        assert bo.post(f"/admin/applications/{sent[0]}/start-review").status_code == 200
        assert len(_audit(db, "application.review_started", sent[0])) == 1

    def test_someone_elses_review_is_refused(self, bo, other_bo, sent):
        bo.post(f"/admin/applications/{sent[0]}/start-review")
        r = other_bo[0].post(f"/admin/applications/{sent[0]}/start-review")
        assert r.status_code == 409 and r.json()["code"] == "already_in_review"

    def test_a_draft_cannot_be_taken(self, bo, client, outbox):
        org_id, _ = _apply(client, outbox, send=False)
        r = bo.post(f"/admin/applications/{org_id}/start-review")
        assert r.status_code == 409 and r.json()["code"] == "application_not_reviewable"


class TestApproving:
    def test_it_approves_and_creates_the_first_administrator(self, bo, db, client, outbox, sent):
        before = len(outbox)
        r = _decide(bo, sent[0], "approve")
        assert r.status_code == 200, r.text
        org = db.get(Organization, sent[0])
        assert org.status == OrganizationStatus.approved and org.approved_at is not None
        application = db.scalars(select(OrganizationApplication).where(
            OrganizationApplication.organization_id == org.id)).one()
        admin_user = db.scalars(select(User).where(User.email == application.applicant_email)).one()
        assert admin_user.user_type_code == "association" and admin_user.role_code == "association_admin"
        assert admin_user.organization_id == org.id and admin_user.password_hash == ""
        assert admin_user.email_verified_at is not None and admin_user.id_type == "CC"
        assert len(outbox) == before + 1 and outbox[-1].to == application.applicant_email
        assert "activate?token=" in outbox[-1].body

    def test_an_eca_gets_an_eca_administrator(self, bo, db, client, outbox):
        org_id, _ = _apply(client, outbox, org_type="eca", name="ECA Norte")
        _decide(bo, org_id, "approve")
        user = db.scalars(select(User).where(User.organization_id == org_id)).one()
        assert user.user_type_code == "eca" and user.role_code == "eca_admin"

    def test_the_applicant_sets_their_password_and_signs_in(self, bo, client, outbox, sent):
        _decide(bo, sent[0], "approve")
        email = outbox[-1].to
        assert client.post("/auth/login", json={"email": email, "password": CHOSEN_PASSWORD}).status_code in (401, 403)
        r = client.post("/auth/activate", json={"token": _token(outbox[-1]), "password": CHOSEN_PASSWORD})
        assert r.status_code == 200, r.text
        login = client.post("/auth/login", json={"email": email, "password": CHOSEN_PASSWORD})
        assert login.status_code == 200
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}).json()
        assert me["role_code"] == "association_admin" and "staff.invite" in me["capabilities"]

    def test_the_magic_link_is_over_and_the_organization_becomes_public(self, bo, client, sent):
        _decide(bo, sent[0], "approve")
        assert client.get("/applications/current", headers={"X-Application-Token": sent[1]}).status_code == 401
        assert "Asociación Esperanza" in [a["legal_name"] for a in client.get("/catalogs/associations").json()]

    def test_the_review_and_the_audit_record_it_without_the_text(self, bo, db, admin, sent):
        _decide(bo, sent[0], "approve")
        (review,) = db.scalars(select(OrganizationReview).where(OrganizationReview.organization_id == sent[0])).all()
        assert review.decision == "approved" and review.reviewer_id == admin.id and review.submission_number == 1
        (entry,) = _audit(db, "application.approved", sent[0])
        assert entry.actor_id == admin.id and "admin_user_id" in entry.details

    def test_an_existing_account_with_the_same_email_blocks_it_and_nothing_is_created(self, bo, db, client, outbox):
        org_id, _ = _apply(client, outbox)
        email = db.scalars(select(OrganizationApplication.applicant_email).where(
            OrganizationApplication.organization_id == org_id)).one()
        factories.make_user(db, "citizen", email=email)
        r = _decide(bo, org_id, "approve")
        assert r.status_code == 409 and r.json()["code"] == "applicant_account_conflict"
        assert db.get(Organization, org_id).status == OrganizationStatus.submitted
        assert db.scalars(select(OrganizationReview)).all() == []

    def test_an_existing_account_with_the_same_document_blocks_it(self, bo, db, client, outbox):
        org_id, _ = _apply(client, outbox, applicant_id_number="55555555")
        factories.make_user(db, "citizen", id_number="55555555")
        assert _decide(bo, org_id, "approve").status_code == 409

    def test_a_tax_id_taken_by_an_operating_organization_in_the_meantime_blocks_it(self, bo, db, client, outbox):
        org_id, _ = _apply(client, outbox, tax_id="900777000-1")
        factories.make_organization(db, "association", tax_id="900777000-1")
        r = _decide(bo, org_id, "approve")
        assert r.status_code == 409 and r.json()["code"] == "organization_already_registered"

    def test_it_can_be_decided_without_taking_it_first(self, bo, sent):
        assert _decide(bo, sent[0], "approve").json()["status"] == "approved"


class TestAskingForChanges:
    def test_it_goes_back_to_the_applicant_with_the_reason_and_a_fresh_link(self, bo, client, outbox, sent):
        before = len(outbox)
        r = _decide(bo, sent[0], "request_changes", "Falta el certificado de la Cámara de Comercio.")
        assert r.status_code == 200 and r.json()["status"] == "changes_requested"
        assert len(outbox) == before + 1 and "Cámara de Comercio" in outbox[-1].body
        assert client.get("/applications/current", headers={"X-Application-Token": sent[1]}).status_code == 401
        fresh = client.get("/applications/current", headers={"X-Application-Token": _token(outbox[-1])}).json()
        assert fresh["can_edit"] is True and fresh["feedback"]["summary"].startswith("Falta el certificado")
        assert fresh["feedback"]["submission_number"] == 1

    def test_the_applicant_corrects_and_resends_and_the_feedback_clears(self, bo, client, outbox, sent):
        _decide(bo, sent[0], "request_changes", "El NIT no coincide con el RUT.")
        headers = {"X-Application-Token": _token(outbox[-1])}
        assert client.patch("/applications/current", headers=headers, json={"tax_id": "900888000-1"}).status_code == 200
        r = client.post("/applications/current/submit", headers=headers)
        assert r.status_code == 200 and r.json()["status"] == "submitted" and r.json()["feedback"] is None
        assert r.json()["submission_count"] == 2
        assert [i["id"] for i in bo.get("/admin/applications").json()["items"]] == [sent[0]]

    def test_the_history_keeps_every_round(self, bo, db, client, outbox, sent):
        _decide(bo, sent[0], "request_changes", "Primera observación del revisor.")
        headers = {"X-Application-Token": _token(outbox[-1])}
        client.post("/applications/current/submit", headers=headers)
        _decide(bo, sent[0], "approve")
        reviews = bo.get(f"/admin/applications/{sent[0]}").json()["reviews"]
        assert [(r["decision"], r["submission_number"]) for r in reviews] == [("changes_requested", 1), ("approved", 2)]

    @pytest.mark.parametrize("summary", [None, "", "corto", "         "])
    def test_the_reason_is_required(self, bo, sent, summary):
        r = _decide(bo, sent[0], "request_changes", summary)
        assert r.status_code == 422

    def test_it_is_audited_without_the_reason(self, bo, db, sent):
        _decide(bo, sent[0], "request_changes", "Hay que corregir la dirección registrada.")
        (entry,) = _audit(db, "application.changes_requested", sent[0])
        assert entry.details == {"submission": 1, "has_summary": True}


class TestRejecting:
    def test_it_is_final_and_the_applicant_is_told_why(self, bo, client, outbox, sent):
        before = len(outbox)
        r = _decide(bo, sent[0], "reject", "La organización no cumple los requisitos de habilitación.")
        assert r.status_code == 200 and r.json()["status"] == "rejected"
        assert len(outbox) == before + 1 and "requisitos de habilitación" in outbox[-1].body
        assert client.get("/applications/current", headers={"X-Application-Token": sent[1]}).status_code == 401
        assert _decide(bo, sent[0], "approve").status_code == 409

    def test_the_reason_is_required(self, bo, sent):
        assert _decide(bo, sent[0], "reject").status_code == 422

    def test_the_same_person_may_apply_again_afterwards(self, bo, client, outbox, sent):
        _decide(bo, sent[0], "reject", "Documentación ilegible en su totalidad.")
        email = outbox[-1].to
        r = client.post("/applications", json={
            "type": "association", "legal_name": "Nueva solicitud", "applicant_name": "Laura Gómez",
            "applicant_email": email, "consent": True})
        assert r.status_code == 202 and "Nueva solicitud" in outbox[-1].body


class TestWhatCanBeDecided:
    def test_a_draft_cannot_be_decided(self, bo, client, outbox):
        org_id, _ = _apply(client, outbox, send=False)
        r = _decide(bo, org_id, "approve")
        assert r.status_code == 409 and r.json()["code"] == "application_not_reviewable"

    def test_an_already_approved_one_cannot_be_decided_again(self, bo, sent):
        _decide(bo, sent[0], "approve")
        assert _decide(bo, sent[0], "reject", "Motivo suficientemente largo.").status_code == 409

    def test_an_unknown_decision_is_422(self, bo, sent):
        assert bo.post(f"/admin/applications/{sent[0]}/decision", json={"decision": "maybe"}).status_code == 422

    def test_a_missing_application_is_404(self, bo):
        assert _decide(bo, "00000000-0000-0000-0000-000000000000", "approve").status_code == 404


class TestTheHistoryCannotBeRewritten:
    def test_the_database_rejects_updates_and_deletes(self, bo, db, sent):
        _decide(bo, sent[0], "request_changes", "Un motivo suficientemente largo.")
        review = db.scalars(select(OrganizationReview)).first()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("UPDATE organization_reviews SET summary = 'x' WHERE id = :i"), {"i": review.id})
        db.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("DELETE FROM organization_reviews WHERE id = :i"), {"i": review.id})
        db.rollback()


class TestWhoMayUseIt:
    ENDPOINTS = [("get", "/admin/applications"), ("get", "/admin/applications/{id}"),
                 ("post", "/admin/applications/{id}/start-review"), ("post", "/admin/applications/{id}/decision")]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_customers_and_anonymous_callers_are_refused(self, client_as, client, db, sent, method, path):
        url = path.replace("{id}", sent[0])
        eca_admin = factories.make_user(db, "eca", role_code="eca_admin")
        assert client_as(eca_admin).request(method.upper(), url, json={}).status_code == 401
        assert client.request(method.upper(), url, json={}).status_code in (401, 403)

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_the_capability_decides(self, bo, sent, monkeypatch, method, path):
        monkeypatch.setitem(permissions.PLATFORM_CAPABILITIES, "organizations.review", frozenset())
        r = bo.request(method.upper(), path.replace("{id}", sent[0]), json={})
        assert r.status_code == 403 and r.json()["code"] == "forbidden"

    def test_the_network_restriction_applies(self, client, admin, monkeypatch):
        monkeypatch.setattr(settings, "admin_allowed_cidrs", "203.0.113.0/24")
        headers = factories.backoffice_headers(admin)
        assert TestClient(app, headers=headers, client=("192.0.2.1", 1)).get("/admin/applications").status_code == 403
        assert TestClient(app, headers=headers, client=("203.0.113.5", 1)).get("/admin/applications").status_code == 200

    def test_the_rate_limit_applies(self, bo, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_admin", "3/minute")
        limiter.reset()
        try:
            assert [bo.get("/admin/applications").status_code for _ in range(5)] == [200, 200, 200, 429, 429]
        finally:
            limiter.reset()
