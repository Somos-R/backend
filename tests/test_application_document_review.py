"""Review of an application's documents (6.9, part 3b): open one through a short-lived signed link, give a verdict,
and what that does to approving or sending a request back."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import permissions, signed_links
from app.core import storage as storage_module
from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.applications.models import (
    OrganizationApplication,
    OrganizationDocument,
    OrganizationDocumentType,
)
from app.domains.audit.models import AuditLog
from app.domains.organizations.enums import OrganizationStatus
from app.domains.organizations.models import Organization
from app.main import app
from tests import factories

PDF = factories.PDF
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
COMPLETE = {
    "tax_id": "900123456-7", "legal_representative": "Rep Legal", "contact_email": "contacto@org.org",
    "contact_phone": "3150000000", "address": "Calle 1 # 2-3", "city": "Bogotá",
    "applicant_id_type": "CC", "applicant_id_number": "1020304050", "applicant_phone": "3001234567",
}
_n = iter(range(1, 100_000))


def _token(message) -> str:
    return message.body.split("token=")[1].split()[0]


def _h(token) -> dict:
    return {"X-Application-Token": token}


@pytest.fixture
def admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


@pytest.fixture
def bo(client, admin):
    return TestClient(app, headers=factories.backoffice_headers(admin))


def _apply(client, outbox, send=True):
    """A public application with every required document, sent. Returns (organization id, magic-link token)."""
    n = next(_n)
    client.post("/applications", json={
        "type": "association", "legal_name": f"Asociación {n}", "applicant_name": "Laura Gómez",
        "applicant_email": f"laura{n}@org.org", "consent": True})
    token = _token(outbox[-1])
    fields = {**COMPLETE, "tax_id": f"9{n:08d}-1", "applicant_id_number": f"10{n:08d}"}
    assert client.patch("/applications/current", headers=_h(token), json=fields).status_code == 200
    factories.attach_required_documents(client, token)
    if send:
        assert client.post("/applications/current/submit", headers=_h(token)).status_code == 200
    return client.get("/applications/current", headers=_h(token)).json()["id"], token


@pytest.fixture
def sent(client, outbox):
    return _apply(client, outbox)


def _docs(bo, org_id):
    return {s["document_type"]["code"]: s["document"] for s in bo.get(f"/admin/applications/{org_id}").json()["documents"]}


def _verdict(bo, org_id, code, status, comment=None):
    document = _docs(bo, org_id)[code]
    body = {"status": status} if comment is None else {"status": status, "comment": comment}
    return bo.patch(f"/admin/applications/{org_id}/documents/{document['id']}", json=body)


def _all_ok(bo, org_id):
    for code in _docs(bo, org_id):
        assert _verdict(bo, org_id, code, "ok").status_code == 200


def _audit(db, action, target_id):
    return db.scalars(select(AuditLog).where(AuditLog.action == action, AuditLog.target_id == str(target_id))).all()


class TestSignedLinks:
    def test_a_genuine_link_returns_its_payload(self):
        token, expires = signed_links.sign("purpose", "hello|world", 60)
        assert signed_links.verify("purpose", token) == "hello|world" and expires > time.time()

    def test_a_link_for_another_purpose_does_not_validate(self):
        token, _ = signed_links.sign("purpose", "x", 60)
        with pytest.raises(signed_links.InvalidLink):
            signed_links.verify("other", token)

    def test_an_expired_link_does_not_validate(self):
        token, _ = signed_links.sign("purpose", "x", -5)
        with pytest.raises(signed_links.InvalidLink):
            signed_links.verify("purpose", token)

    def test_a_tampered_or_malformed_link_does_not_validate(self):
        token, _ = signed_links.sign("purpose", "x", 60)
        body, signature = token.split(".")
        forged_body, _ = signed_links.sign("purpose", "y", 60)
        for bad in (token + "a", f"{forged_body.split('.')[0]}.{signature}", body, "", "a.b", "...", "no-dots", f"{body}.{signature[:-2]}"):
            with pytest.raises(signed_links.InvalidLink):
                signed_links.verify("purpose", bad)

    def test_a_different_secret_invalidates_old_links(self, monkeypatch):
        token, _ = signed_links.sign("purpose", "x", 60)
        monkeypatch.setattr(settings, "secret_key", "another-secret-key-that-is-long-enough-123456")
        with pytest.raises(signed_links.InvalidLink):
            signed_links.verify("purpose", token)


class TestOpeningADocument:
    def test_the_detail_lists_the_documents_with_their_verdicts(self, bo, sent):
        slots = bo.get(f"/admin/applications/{sent[0]}").json()["documents"]
        assert [s["document_type"]["code"] for s in slots] == [
            "assoc_rut", "assoc_legal_representative_id", "assoc_legal_personality"]
        first = slots[0]["document"]
        assert first["status"] == "pending" and first["original_name"] == "assoc_rut.pdf"
        assert first["reviewed_by"] is None and "storage" not in str(first)

    def test_a_signed_link_downloads_the_exact_file_without_any_header(self, bo, client, sent):
        document = _docs(bo, sent[0])["assoc_rut"]
        access = bo.post(f"/admin/applications/{sent[0]}/documents/{document['id']}/access")
        assert access.status_code == 200 and access.json()["url"].startswith("/admin/documents/download/")
        anonymous = TestClient(app)  # a browser tab: no Authorization header
        r = anonymous.get(access.json()["url"])
        assert r.status_code == 200 and r.content == PDF
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"] == 'attachment; filename="assoc_rut.pdf"'
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"

    def test_the_download_name_is_cleaned(self, bo, client, db, sent):
        row = db.scalars(select(OrganizationDocument).where(OrganizationDocument.document_type_code == "assoc_rut")).one()
        row.original_name = 'evil"; filename=x.exe\r\n.pdf'
        db.commit()
        url = bo.post(f"/admin/applications/{sent[0]}/documents/{row.id}/access").json()["url"]
        header = TestClient(app).get(url).headers["content-disposition"]
        assert "\r" not in header and "\n" not in header and header.count('"') == 2

    def test_a_link_expires(self, bo, sent, monkeypatch):
        document = _docs(bo, sent[0])["assoc_rut"]
        monkeypatch.setattr(settings, "document_url_ttl_seconds", -5)
        url = bo.post(f"/admin/applications/{sent[0]}/documents/{document['id']}/access").json()["url"]
        r = TestClient(app).get(url)
        assert r.status_code == 401 and r.json()["code"] == "invalid_document_link"

    def test_a_forged_link_is_refused(self, bo, sent):
        document = _docs(bo, sent[0])["assoc_rut"]
        url = bo.post(f"/admin/applications/{sent[0]}/documents/{document['id']}/access").json()["url"]
        assert TestClient(app).get(url + "x").status_code == 401
        assert TestClient(app).get("/admin/documents/download/not-a-token").status_code == 401

    def test_a_link_stops_working_once_the_file_is_replaced(self, bo, client, db, sent):
        document = _docs(bo, sent[0])["assoc_rut"]
        url = bo.post(f"/admin/applications/{sent[0]}/documents/{document['id']}/access").json()["url"]
        db.get(Organization, sent[0]).status = OrganizationStatus.changes_requested
        db.commit()
        assert client.put("/applications/current/documents/assoc_rut", headers=_h(sent[1]),
                          files={"file": ("nuevo.png", PNG)}).status_code == 200
        assert TestClient(app).get(url).status_code == 401

    def test_a_file_missing_from_storage_is_404(self, bo, db, sent):
        row = db.scalars(select(OrganizationDocument).where(OrganizationDocument.document_type_code == "assoc_rut")).one()
        url = bo.post(f"/admin/applications/{sent[0]}/documents/{row.id}/access").json()["url"]
        storage_module.get_storage().delete(row.storage_key)
        assert TestClient(app).get(url).status_code == 404

    def test_a_document_of_another_application_looks_missing(self, bo, client, outbox, sent):
        other_org, _ = _apply(client, outbox)
        other_document = _docs(bo, other_org)["assoc_rut"]
        r = bo.post(f"/admin/applications/{sent[0]}/documents/{other_document['id']}/access")
        assert r.status_code == 404 and r.json()["code"] == "document_not_found"
        missing = bo.post(f"/admin/applications/{sent[0]}/documents/00000000-0000-0000-0000-000000000000/access")
        assert missing.status_code == 404 and missing.json() == r.json()

    def test_asking_for_and_using_the_link_are_both_audited(self, bo, db, admin, sent):
        document = _docs(bo, sent[0])["assoc_rut"]
        url = bo.post(f"/admin/applications/{sent[0]}/documents/{document['id']}/access").json()["url"]
        TestClient(app).get(url)
        (viewed,) = _audit(db, "admin.document_viewed", document["id"])
        (downloaded,) = _audit(db, "admin.document_downloaded", document["id"])
        assert viewed.actor_id == admin.id and viewed.details == {
            "organization_id": sent[0], "document_type": "assoc_rut"}
        assert downloaded.actor_id is None and downloaded.details["document_type"] == "assoc_rut"
        assert "assoc_rut.pdf" not in str(viewed.details)

    def test_the_network_restriction_applies_to_the_download_too(self, bo, sent, admin, monkeypatch):
        document = _docs(bo, sent[0])["assoc_rut"]
        url = bo.post(f"/admin/applications/{sent[0]}/documents/{document['id']}/access").json()["url"]
        monkeypatch.setattr(settings, "admin_allowed_cidrs", "203.0.113.0/24")
        assert TestClient(app, client=("192.0.2.1", 1)).get(url).status_code == 403
        assert TestClient(app, client=("203.0.113.5", 1)).get(url).status_code == 200


class TestTheVerdict:
    def test_ok_needs_no_comment_and_records_who_and_when(self, bo, admin, sent):
        r = _verdict(bo, sent[0], "assoc_rut", "ok")
        assert r.status_code == 200 and r.json()["document"]["status"] == "ok"
        assert r.json()["document"]["reviewed_by"]["id"] == str(admin.id) and r.json()["document"]["reviewed_at"]
        assert r.json()["document_type"]["code"] == "assoc_rut"

    @pytest.mark.parametrize("status", ["missing", "not_compliant"])
    def test_a_problem_needs_a_comment(self, bo, sent, status):
        r = _verdict(bo, sent[0], "assoc_rut", status)
        assert r.status_code == 422 and r.json()["code"] == "comment_required"
        assert _verdict(bo, sent[0], "assoc_rut", status, "   ").status_code == 422
        r = _verdict(bo, sent[0], "assoc_rut", status, "El documento está vencido.")
        assert r.status_code == 200 and r.json()["document"]["review_comment"] == "El documento está vencido."

    def test_a_reviewer_cannot_set_pending_or_an_unknown_status(self, bo, sent):
        for status in ("pending", "approved", ""):
            assert _verdict(bo, sent[0], "assoc_rut", status, "x").status_code == 422

    def test_a_verdict_can_change_and_the_comment_goes_away_with_ok(self, bo, sent):
        _verdict(bo, sent[0], "assoc_rut", "not_compliant", "Ilegible por completo.")
        r = _verdict(bo, sent[0], "assoc_rut", "ok")
        assert r.json()["document"]["status"] == "ok" and r.json()["document"]["review_comment"] is None

    def test_only_while_the_request_waits_for_review(self, bo, client, outbox, sent):
        draft_org, _ = _apply(client, outbox, send=False)
        document = _docs(bo, draft_org)["assoc_rut"]
        r = bo.patch(f"/admin/applications/{draft_org}/documents/{document['id']}", json={"status": "ok"})
        assert r.status_code == 409 and r.json()["code"] == "application_not_reviewable"
        _all_ok(bo, sent[0])
        bo.post(f"/admin/applications/{sent[0]}/decision", json={"decision": "approve"})
        late = _docs(bo, sent[0])["assoc_rut"]
        assert bo.patch(f"/admin/applications/{sent[0]}/documents/{late['id']}", json={"status": "ok"}).status_code == 409

    def test_a_document_of_another_application_cannot_be_judged_from_this_one(self, bo, client, outbox, sent):
        other_org, _ = _apply(client, outbox)
        other = _docs(bo, other_org)["assoc_rut"]
        r = bo.patch(f"/admin/applications/{sent[0]}/documents/{other['id']}", json={"status": "ok"})
        assert r.status_code == 404
        assert _docs(bo, other_org)["assoc_rut"]["status"] == "pending"

    def test_it_is_audited_without_the_comment(self, bo, db, admin, sent):
        _verdict(bo, sent[0], "assoc_rut", "not_compliant", "Contiene datos de otra persona.")
        document = _docs(bo, sent[0])["assoc_rut"]
        (entry,) = _audit(db, "application.document_reviewed", document["id"])
        assert entry.actor_id == admin.id
        assert entry.details == {"organization_id": sent[0], "document_type": "assoc_rut",
                                 "status": "not_compliant", "has_comment": True}


class TestApproving:
    def test_it_is_blocked_until_every_required_document_is_ok(self, bo, sent):
        r = bo.post(f"/admin/applications/{sent[0]}/decision", json={"decision": "approve"})
        assert r.status_code == 409 and r.json()["code"] == "documents_not_approved"
        for code in ("assoc_rut", "assoc_legal_representative_id", "assoc_legal_personality"):
            assert code in r.json()["detail"]
        for code in ("assoc_rut", "assoc_legal_representative_id"):
            _verdict(bo, sent[0], code, "ok")
        r = bo.post(f"/admin/applications/{sent[0]}/decision", json={"decision": "approve"})
        assert r.status_code == 409 and "assoc_legal_personality" in r.json()["detail"] and "assoc_rut" not in r.json()["detail"]
        _verdict(bo, sent[0], "assoc_legal_personality", "ok")
        assert bo.post(f"/admin/applications/{sent[0]}/decision", json={"decision": "approve"}).status_code == 200

    def test_a_required_document_marked_as_a_problem_blocks_it(self, bo, sent):
        _all_ok(bo, sent[0])
        _verdict(bo, sent[0], "assoc_rut", "not_compliant", "No coincide con el NIT.")
        assert bo.post(f"/admin/applications/{sent[0]}/decision", json={"decision": "approve"}).status_code == 409

    def test_an_optional_document_does_not_block_it(self, bo, db, client, outbox):
        db.get(OrganizationDocumentType, "assoc_legal_personality").is_required = False
        db.commit()
        org_id, token = _apply(client, outbox, send=False)
        assert client.put("/applications/current/documents/assoc_legal_personality", headers=_h(token),
                          files={"file": ("personeria.pdf", PDF)}).status_code == 200
        assert client.post("/applications/current/submit", headers=_h(token)).status_code == 200
        for code in ("assoc_rut", "assoc_legal_representative_id"):
            _verdict(bo, org_id, code, "ok")
        _verdict(bo, org_id, "assoc_legal_personality", "not_compliant", "No es legible el documento.")
        assert bo.post(f"/admin/applications/{org_id}/decision", json={"decision": "approve"}).status_code == 200

    def test_a_required_type_added_after_sending_blocks_it_until_it_is_attached(self, bo, db, sent):
        _all_ok(bo, sent[0])
        db.add(OrganizationDocumentType(code="assoc_extra", organization_type="association", label="Extra", sort_order=90))
        db.commit()
        r = bo.post(f"/admin/applications/{sent[0]}/decision", json={"decision": "approve"})
        assert r.status_code == 409 and "assoc_extra" in r.json()["detail"]


class TestSendingItBack:
    def test_the_documents_with_problems_travel_with_the_review_and_the_email(self, bo, client, db, outbox, sent):
        _all_ok(bo, sent[0])
        _verdict(bo, sent[0], "assoc_rut", "not_compliant", "El RUT está vencido.")
        before = len(outbox)
        r = bo.post(f"/admin/applications/{sent[0]}/decision", json={
            "decision": "request_changes", "summary": "Hay un documento por corregir."})
        assert r.status_code == 200, r.text
        (review,) = r.json()["reviews"]
        assert review["details"] == [{"code": "assoc_rut", "label": "RUT o certificado del NIT de la asociación",
                                      "status": "not_compliant", "comment": "El RUT está vencido."}]
        assert len(outbox) == before + 1
        assert "Documentos por corregir" in outbox[-1].body and "El RUT está vencido." in outbox[-1].body
        assert "personería" not in outbox[-1].body  # the documents that are fine are not listed

    def test_the_applicant_sees_which_documents_and_why(self, bo, client, outbox, sent):
        _verdict(bo, sent[0], "assoc_legal_personality", "missing", "Falta el certificado con vigencia de 30 días.")
        bo.post(f"/admin/applications/{sent[0]}/decision", json={
            "decision": "request_changes", "summary": "Corrige el documento de personería jurídica."})
        token = _token(outbox[-1])
        view = client.get("/applications/current", headers=_h(token)).json()
        assert [d["code"] for d in view["feedback"]["documents"]] == ["assoc_legal_personality"]
        assert view["feedback"]["documents"][0]["comment"].startswith("Falta el certificado")
        slots = {s["document_type"]["code"]: s["document"] for s in client.get(
            "/applications/current/documents", headers=_h(token)).json()}
        assert slots["assoc_legal_personality"]["status"] == "missing"
        assert slots["assoc_legal_personality"]["review_comment"].startswith("Falta el certificado")
        assert slots["assoc_rut"]["status"] == "pending"

    def test_a_replaced_file_goes_back_to_pending_and_the_ones_that_were_ok_stay_ok(self, bo, client, outbox, sent):
        _verdict(bo, sent[0], "assoc_rut", "ok")
        _verdict(bo, sent[0], "assoc_legal_personality", "not_compliant", "Documento ilegible, súbelo de nuevo.")
        bo.post(f"/admin/applications/{sent[0]}/decision", json={
            "decision": "request_changes", "summary": "Sube de nuevo la personería jurídica."})
        token = _token(outbox[-1])
        assert client.put("/applications/current/documents/assoc_legal_personality", headers=_h(token),
                          files={"file": ("nueva.png", PNG)}).status_code == 200
        docs = _docs(bo, sent[0])
        assert docs["assoc_legal_personality"]["status"] == "pending" and docs["assoc_legal_personality"]["review_comment"] is None
        assert docs["assoc_rut"]["status"] == "ok"

    def test_a_required_document_that_was_never_attached_is_listed_as_missing(self, bo, db, outbox, sent):
        db.add(OrganizationDocumentType(code="assoc_extra", organization_type="association", label="Extra", sort_order=90))
        db.commit()
        r = bo.post(f"/admin/applications/{sent[0]}/decision", json={
            "decision": "request_changes", "summary": "Falta un documento nuevo."})
        assert {"code": "assoc_extra", "label": "Extra", "status": "missing", "comment": "No se adjuntó este documento"} in (
            r.json()["reviews"][0]["details"])

    def test_the_whole_round_trip_ends_with_an_approval(self, bo, client, db, outbox, sent):
        _verdict(bo, sent[0], "assoc_rut", "ok")
        _verdict(bo, sent[0], "assoc_legal_representative_id", "ok")
        _verdict(bo, sent[0], "assoc_legal_personality", "not_compliant", "Falta la firma del notario.")
        bo.post(f"/admin/applications/{sent[0]}/decision", json={
            "decision": "request_changes", "summary": "La personería necesita la firma del notario."})
        token = _token(outbox[-1])
        client.put("/applications/current/documents/assoc_legal_personality", headers=_h(token),
                   files={"file": ("firmada.pdf", PDF)})
        assert client.post("/applications/current/submit", headers=_h(token)).status_code == 200
        assert bo.post(f"/admin/applications/{sent[0]}/decision", json={"decision": "approve"}).status_code == 409
        _verdict(bo, sent[0], "assoc_legal_personality", "ok")
        assert bo.post(f"/admin/applications/{sent[0]}/decision", json={"decision": "approve"}).status_code == 200
        reviews = bo.get(f"/admin/applications/{sent[0]}").json()["reviews"]
        assert [(r["decision"], r["submission_number"]) for r in reviews] == [("changes_requested", 1), ("approved", 2)]
        assert reviews[0]["details"][0]["code"] == "assoc_legal_personality" and reviews[1]["details"] == []


class TestWhoMayUseIt:
    ENDPOINTS = [("post", "/admin/applications/{org}/documents/{doc}/access"),
                 ("patch", "/admin/applications/{org}/documents/{doc}")]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_customers_and_anonymous_callers_are_refused(self, client_as, client, db, bo, sent, method, path):
        url = path.replace("{org}", sent[0]).replace("{doc}", _docs(bo, sent[0])["assoc_rut"]["id"])
        eca_admin = factories.make_user(db, "eca", role_code="eca_admin")
        assert client_as(eca_admin).request(method.upper(), url, json={"status": "ok"}).status_code == 401
        assert client.request(method.upper(), url, json={"status": "ok"}).status_code in (401, 403)

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_the_capability_decides(self, bo, sent, monkeypatch, method, path):
        url = path.replace("{org}", sent[0]).replace("{doc}", _docs(bo, sent[0])["assoc_rut"]["id"])
        monkeypatch.setitem(permissions.PLATFORM_CAPABILITIES, "organizations.review", frozenset())
        assert bo.request(method.upper(), url, json={"status": "ok"}).status_code == 403

    def test_the_download_is_rate_limited(self, bo, sent, monkeypatch):
        document = _docs(bo, sent[0])["assoc_rut"]
        url = bo.post(f"/admin/applications/{sent[0]}/documents/{document['id']}/access").json()["url"]
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_admin", "3/minute")
        limiter.reset()
        try:
            anonymous = TestClient(app)
            assert [anonymous.get(url).status_code for _ in range(5)] == [200, 200, 200, 429, 429]
        finally:
            limiter.reset()


def test_the_application_row_is_unchanged_by_reviewing_documents(bo, db, sent):
    before = db.scalars(select(OrganizationApplication.submission_count)).one()
    _verdict(bo, sent[0], "assoc_rut", "ok")
    assert db.scalars(select(OrganizationApplication.submission_count)).one() == before
