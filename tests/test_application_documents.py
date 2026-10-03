"""Documents of an application (6.7 / 6.8, part 3a): the catalog the backoffice edits, private storage, and
uploading, replacing and removing files from the public application."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import permissions, uploads
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
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
COMPLETE = {
    "tax_id": "900123456-7", "legal_representative": "Rep Legal", "contact_email": "contacto@org.org",
    "contact_phone": "3150000000", "address": "Calle 1 # 2-3", "city": "Bogotá",
    "applicant_id_type": "CC", "applicant_id_number": "1020304050", "applicant_phone": "3001234567",
}
_n = iter(range(1, 100_000))


def _token(message) -> str:
    return message.body.split("token=")[1].split()[0]


def _start(client, outbox, org_type="association"):
    n = next(_n)
    r = client.post("/applications", json={
        "type": org_type, "legal_name": f"Organización {n}", "applicant_name": "Laura Gómez",
        "applicant_email": f"laura{n}@org.org", "consent": True})
    assert r.status_code == 202, r.text
    return _token(outbox[-1])


def _h(token) -> dict:
    return {"X-Application-Token": token}


def _put(client, token, code, data=PDF, name="doc.pdf"):
    return client.put(f"/applications/current/documents/{code}", headers=_h(token), files={"file": (name, data)})


@pytest.fixture
def token(client, outbox):
    return _start(client, outbox)


@pytest.fixture
def admin(db):
    return factories.make_user(db, "platform", role_code="platform_admin")


@pytest.fixture
def bo(client, admin):
    return TestClient(app, headers=factories.backoffice_headers(admin))


def _row(db, code, token_owner_email=None):
    return db.scalars(select(OrganizationDocument).where(OrganizationDocument.document_type_code == code)).one()


class TestTheListing:
    def test_an_association_is_asked_for_its_documents_and_has_none_yet(self, client, token):
        slots = client.get("/applications/current/documents", headers=_h(token)).json()
        assert [s["document_type"]["code"] for s in slots] == [
            "assoc_rut", "assoc_legal_representative_id", "assoc_legal_personality"]
        assert all(s["document"] is None and s["document_type"]["is_required"] for s in slots)
        assert all(s["document_type"]["label"] for s in slots)

    def test_an_eca_is_asked_for_different_ones(self, client, outbox):
        eca = _start(client, outbox, "eca")
        codes = [s["document_type"]["code"] for s in client.get("/applications/current/documents", headers=_h(eca)).json()]
        assert codes == ["eca_rut", "eca_sspd_habilitation"]

    def test_it_needs_the_magic_link(self, client):
        assert client.get("/applications/current/documents").status_code == 401


class TestUploading:
    def test_a_pdf_is_stored_privately_and_described_without_its_location(self, client, db, token):
        r = _put(client, token, "assoc_rut", PDF, "Mi RUT 2026.pdf")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["original_name"] == "Mi RUT 2026.pdf" and body["content_type"] == "application/pdf"
        assert body["size_bytes"] == len(PDF) and body["status"] == "pending" and body["review_comment"] is None
        assert "storage" not in r.text and "key" not in body and "sha256" not in body
        row = _row(db, "assoc_rut")
        assert storage_module.get_storage().get(row.storage_key) == PDF
        assert "Mi RUT" not in row.storage_key and row.storage_key.startswith("applications/")
        assert len(row.sha256) == 64

    @pytest.mark.parametrize("data,content_type", [(PDF, "application/pdf"), (PNG, "image/png"), (JPG, "image/jpeg")])
    def test_pdf_png_and_jpg_are_accepted(self, client, token, data, content_type):
        assert _put(client, token, "assoc_rut", data).json()["content_type"] == content_type

    def test_the_type_is_decided_by_the_content_not_the_name(self, client, token):
        r = _put(client, token, "assoc_rut", b"MZ\x90\x00 not a document", "contrato.pdf")
        assert r.status_code == 415 and r.json()["code"] == "unsupported_file_type"
        assert _put(client, token, "assoc_rut", PDF, "script.exe").status_code == 200  # a PDF by content is a PDF

    def test_an_empty_file_is_refused(self, client, token):
        r = _put(client, token, "assoc_rut", b"")
        assert r.status_code == 422 and r.json()["code"] == "empty_file"

    def test_a_file_over_the_limit_is_refused_and_nothing_is_stored(self, client, db, token, monkeypatch):
        monkeypatch.setattr(settings, "document_max_bytes", 100)
        r = _put(client, token, "assoc_rut", PDF + b"x" * 200)
        assert r.status_code == 413 and r.json()["code"] == "file_too_large"
        assert db.scalars(select(OrganizationDocument)).all() == []

    def test_a_huge_declared_body_is_refused_before_it_is_read(self, client, token, monkeypatch):
        monkeypatch.setattr(settings, "document_max_bytes", 100)
        r = client.put("/applications/current/documents/assoc_rut", headers={**_h(token), "content-length": "999999999"},
                       files={"file": ("a.pdf", PDF)})
        assert r.status_code == 413

    def test_the_shown_name_is_only_a_clean_last_part(self, client, token):
        r = _put(client, token, "assoc_rut", PDF, "../../etc/passwd")
        assert r.json()["original_name"] == "passwd"
        assert uploads.display_name("a\x00b\nc.pdf") == "abc.pdf" and uploads.display_name(None) == "documento"
        assert len(uploads.display_name("x" * 500)) == 200

    def test_a_document_that_is_not_asked_for_is_404(self, client, token):
        for code in ("eca_rut", "no_such_document"):
            r = _put(client, token, code)
            assert r.status_code == 404 and r.json()["code"] == "document_type_not_found"

    def test_an_inactive_document_is_not_asked_for_any_more(self, client, db, token):
        db.get(OrganizationDocumentType, "assoc_rut").is_active = False
        db.commit()
        assert _put(client, token, "assoc_rut").status_code == 404
        assert "assoc_rut" not in [s["document_type"]["code"] for s in client.get(
            "/applications/current/documents", headers=_h(token)).json()]

    def test_it_is_audited_without_the_file_name(self, client, db, token):
        _put(client, token, "assoc_rut", PDF, "cedula-de-juan-perez.pdf")
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "application.document_uploaded")).one()
        assert entry.details == {"document_type": "assoc_rut", "size_bytes": len(PDF), "replaced": False}

    def test_it_is_rate_limited(self, client, token, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_application_uploads", "3/minute")
        limiter.reset()
        try:
            codes = [_put(client, token, "assoc_rut").status_code for _ in range(5)]
            assert codes == [200, 200, 200, 429, 429]
        finally:
            limiter.reset()


class TestReplacingAndRemoving:
    def test_a_new_upload_replaces_the_file_and_forgets_the_verdict(self, client, db, token):
        _put(client, token, "assoc_rut", PDF)
        row = _row(db, "assoc_rut")
        old_key = row.storage_key
        row.status, row.review_comment = "not_compliant", "Ilegible"
        db.commit()
        r = _put(client, token, "assoc_rut", PNG, "nuevo.png")
        assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["review_comment"] is None
        db.expire_all()
        row = _row(db, "assoc_rut")
        assert row.storage_key != old_key and row.content_type == "image/png"
        assert storage_module.get_storage().get(row.storage_key) == PNG
        with pytest.raises(storage_module.StorageError):
            storage_module.get_storage().get(old_key)
        assert len(db.scalars(select(OrganizationDocument)).all()) == 1

    def test_removing_deletes_the_row_and_the_file(self, client, db, token):
        _put(client, token, "assoc_rut")
        key = _row(db, "assoc_rut").storage_key
        assert client.delete("/applications/current/documents/assoc_rut", headers=_h(token)).status_code == 204
        assert db.scalars(select(OrganizationDocument)).all() == []
        with pytest.raises(storage_module.StorageError):
            storage_module.get_storage().get(key)
        assert db.scalars(select(AuditLog).where(AuditLog.action == "application.document_deleted")).one()

    def test_removing_what_is_not_there_is_404(self, client, token):
        r = client.delete("/applications/current/documents/assoc_rut", headers=_h(token))
        assert r.status_code == 404 and r.json()["code"] == "document_not_found"


class TestSending:
    def _fill(self, client, token):
        assert client.patch("/applications/current", headers=_h(token), json=COMPLETE).status_code == 200

    def test_the_required_documents_show_as_missing_until_they_are_attached(self, client, token):
        view = client.get("/applications/current", headers=_h(token)).json()
        assert {"documents:assoc_rut", "documents:assoc_legal_personality"} <= set(view["missing_fields"])
        _put(client, token, "assoc_rut")
        view = client.get("/applications/current", headers=_h(token)).json()
        assert "documents:assoc_rut" not in view["missing_fields"]

    def test_it_cannot_be_sent_without_them_and_the_error_names_them(self, client, token):
        self._fill(client, token)
        r = client.post("/applications/current/submit", headers=_h(token))
        assert r.status_code == 422 and r.json()["code"] == "application_incomplete"
        assert "documents:assoc_rut" in r.json()["detail"] and "tax_id" not in r.json()["detail"]

    def test_with_everything_attached_it_is_sent(self, client, token):
        self._fill(client, token)
        factories.attach_required_documents(client, token)
        r = client.post("/applications/current/submit", headers=_h(token))
        assert r.status_code == 200 and r.json()["status"] == "submitted" and r.json()["missing_fields"] == []

    def test_an_optional_document_does_not_block_it(self, client, db, token):
        for code in ("assoc_legal_personality",):
            db.get(OrganizationDocumentType, code).is_required = False
        db.commit()
        self._fill(client, token)
        _put(client, token, "assoc_rut")
        _put(client, token, "assoc_legal_representative_id")
        assert client.post("/applications/current/submit", headers=_h(token)).status_code == 200

    def test_after_sending_the_files_are_locked_until_changes_are_requested(self, client, db, token):
        self._fill(client, token)
        factories.attach_required_documents(client, token)
        client.post("/applications/current/submit", headers=_h(token))
        for r in (_put(client, token, "assoc_rut"), client.delete("/applications/current/documents/assoc_rut", headers=_h(token))):
            assert r.status_code == 409 and r.json()["code"] == "application_locked"
        db.get(Organization, db.scalars(select(OrganizationApplication.organization_id)).one()).status = (
            OrganizationStatus.changes_requested)
        db.commit()
        assert _put(client, token, "assoc_rut", PNG).status_code == 200


class TestEachApplicantSeesOnlyTheirOwn:
    def test_documents_are_not_shared_between_applications(self, client, outbox):
        first, second = _start(client, outbox), _start(client, outbox)
        _put(client, first, "assoc_rut")
        mine = {s["document_type"]["code"]: s["document"] for s in client.get("/applications/current/documents", headers=_h(first)).json()}
        theirs = {s["document_type"]["code"]: s["document"] for s in client.get("/applications/current/documents", headers=_h(second)).json()}
        assert mine["assoc_rut"] is not None and theirs["assoc_rut"] is None

    def test_a_second_applicant_uploading_the_same_type_does_not_touch_the_first(self, client, db, outbox):
        first, second = _start(client, outbox), _start(client, outbox)
        _put(client, first, "assoc_rut", PDF)
        _put(client, second, "assoc_rut", PNG)
        rows = db.scalars(select(OrganizationDocument)).all()
        assert len(rows) == 2 and {r.content_type for r in rows} == {"application/pdf", "image/png"}
        assert len({r.storage_key.split("/")[1] for r in rows}) == 2  # each under its own organization folder


class TestTheCatalogFromTheBackoffice:
    def test_the_seed_is_listed_and_can_be_filtered(self, bo):
        everything = bo.get("/admin/catalogs/organization-documents").json()
        assert {"eca_rut", "eca_sspd_habilitation", "assoc_rut", "assoc_legal_representative_id",
                "assoc_legal_personality"} <= {d["code"] for d in everything}
        only_eca = bo.get("/admin/catalogs/organization-documents", params={"organization_type": "eca"}).json()
        assert {d["organization_type"] for d in only_eca} == {"eca"}

    def test_create_a_document_and_it_is_asked_for_from_then_on(self, bo, client, token):
        r = bo.post("/admin/catalogs/organization-documents", json={
            "code": "assoc_bank_certificate", "label": "Certificación bancaria", "organization_type": "association",
            "sort_order": 40})
        assert r.status_code == 201 and r.json()["is_required"] is True and r.json()["is_active"] is True
        view = client.get("/applications/current", headers=_h(token)).json()
        assert "documents:assoc_bank_certificate" in view["missing_fields"]

    @pytest.mark.parametrize("code", ["Assoc", "1abc", "a", "con espacio", "x" * 41])
    def test_the_code_has_a_fixed_shape(self, bo, code):
        r = bo.post("/admin/catalogs/organization-documents", json={
            "code": code, "label": "x", "organization_type": "eca"})
        assert r.status_code == 422

    def test_a_code_cannot_be_reused(self, bo):
        r = bo.post("/admin/catalogs/organization-documents", json={
            "code": "eca_rut", "label": "Otro", "organization_type": "eca"})
        assert r.status_code == 409 and r.json()["code"] == "code_already_exists"

    def test_making_one_optional_or_retiring_it_changes_what_is_required(self, bo, client, token):
        bo.patch("/admin/catalogs/organization-documents/assoc_rut", json={"is_required": False})
        bo.patch("/admin/catalogs/organization-documents/assoc_legal_personality", json={"is_active": False})
        view = client.get("/applications/current", headers=_h(token)).json()
        assert "documents:assoc_rut" not in view["missing_fields"]
        assert "documents:assoc_legal_personality" not in view["missing_fields"]
        assert "documents:assoc_legal_representative_id" in view["missing_fields"]

    def test_the_label_and_the_order_can_change_but_not_the_code_or_the_type(self, bo):
        r = bo.patch("/admin/catalogs/organization-documents/assoc_rut", json={
            "label": "RUT actualizado", "sort_order": 99, "code": "otro", "organization_type": "eca"})
        assert r.status_code == 200
        assert r.json()["label"] == "RUT actualizado" and r.json()["sort_order"] == 99
        assert r.json()["code"] == "assoc_rut" and r.json()["organization_type"] == "association"

    def test_an_empty_change_is_422_and_a_missing_one_404(self, bo):
        assert bo.patch("/admin/catalogs/organization-documents/assoc_rut", json={}).status_code == 422
        assert bo.patch("/admin/catalogs/organization-documents/nope", json={"label": "x"}).status_code == 404

    def test_it_is_audited(self, bo, db):
        bo.post("/admin/catalogs/organization-documents", json={
            "code": "eca_extra", "label": "Extra", "organization_type": "eca"})
        bo.patch("/admin/catalogs/organization-documents/eca_extra", json={"is_required": False})
        actions = db.scalars(select(AuditLog.action).where(AuditLog.target_id == "eca_extra")).all()
        assert sorted(actions) == ["catalog.created", "catalog.updated"]

    ENDPOINTS = [("get", "/admin/catalogs/organization-documents"), ("post", "/admin/catalogs/organization-documents"),
                 ("patch", "/admin/catalogs/organization-documents/assoc_rut")]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_customers_and_anonymous_callers_are_refused(self, client_as, client, db, method, path):
        eca_admin = factories.make_user(db, "eca", role_code="eca_admin")
        assert client_as(eca_admin).request(method.upper(), path, json={}).status_code == 401
        assert client.request(method.upper(), path, json={}).status_code in (401, 403)

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_the_capability_decides(self, bo, monkeypatch, method, path):
        monkeypatch.setitem(permissions.PLATFORM_CAPABILITIES, "catalogs.manage", frozenset())
        assert bo.request(method.upper(), path, json={}).status_code == 403


class TestPrivateStorage:
    def test_it_keeps_what_is_put_and_forgets_what_is_deleted(self, tmp_path):
        store = storage_module.LocalStorage(str(tmp_path))
        store.put("applications/abc/file1", b"hello")
        assert store.get("applications/abc/file1") == b"hello"
        store.delete("applications/abc/file1")
        store.delete("applications/abc/file1")  # deleting twice is fine
        with pytest.raises(storage_module.StorageError):
            store.get("applications/abc/file1")

    @pytest.mark.parametrize("key", ["../outside", "/etc/passwd", "a/../../b", "a//b", "A/B", "a b", "", "a/.."])
    def test_a_key_cannot_escape_the_directory(self, tmp_path, key):
        store = storage_module.LocalStorage(str(tmp_path / "root"))
        for call in (lambda: store.put(key, b"x"), lambda: store.get(key), lambda: store.delete(key)):
            with pytest.raises(storage_module.StorageError):
                call()

    def test_keys_are_random_and_under_the_given_folders(self):
        first, second = storage_module.new_key("applications", "org1"), storage_module.new_key("applications", "org1")
        assert first != second and first.startswith("applications/org1/") and len(first.split("/")[-1]) == 32

    def test_an_unknown_backend_is_an_error(self, monkeypatch):
        monkeypatch.setattr(settings, "storage_backend", "tape")
        with pytest.raises(storage_module.StorageError):
            storage_module.get_storage()

    def test_production_is_warned_about_the_local_backend(self, monkeypatch):
        monkeypatch.setattr(settings, "app_env", "prod")
        assert any("STORAGE_BACKEND=local" in w for w in settings.deployment_warnings())
        monkeypatch.setattr(settings, "storage_backend", "s3")
        assert not any("STORAGE_BACKEND" in w for w in settings.deployment_warnings())
