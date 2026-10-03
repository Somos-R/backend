"""The public application to join Somos R (6.8, first part): start, come back by magic link, complete, send."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.applications.models import OrganizationApplication
from app.domains.audit.models import AuditLog
from app.domains.organizations.enums import OrganizationStatus, OrganizationType
from app.domains.organizations.models import Organization
from app.domains.users.models import User
from tests import factories

COMPLETE = {
    "legal_representative": "Rep Legal", "contact_email": "contacto@asociacion.org", "contact_phone": "3150000000",
    "address": "Calle 1 # 2-3", "city": "Bogotá", "tax_id": "900123456-7",
    "applicant_id_type": "CC", "applicant_id_number": "1020304050", "applicant_phone": "3001234567",
}


def _start(client, **overrides):
    body = {"type": "association", "legal_name": "Asociación Esperanza", "applicant_name": "Laura Gómez",
            "applicant_email": "laura@asociacion.org", "consent": True, **overrides}
    return client.post("/applications", json=body)


def _token(message) -> str:
    return message.body.split("token=")[1].split()[0]


def _auth(token) -> dict:
    return {"X-Application-Token": token}


def _complete(client, token):
    """Fill in every required field and attach every required document."""
    assert client.patch("/applications/current", headers=_auth(token), json=COMPLETE).status_code == 200
    factories.attach_required_documents(client, token)


@pytest.fixture
def started(client, db, outbox):
    r = _start(client)
    assert r.status_code == 202, r.text
    return _token(outbox[-1])


def _application(db, email="laura@asociacion.org"):
    return db.scalars(select(OrganizationApplication).where(OrganizationApplication.applicant_email == email)).one()


class TestStarting:
    def test_it_creates_a_draft_organization_and_emails_the_link(self, client, db, outbox):
        r = _start(client)
        assert r.status_code == 202
        application = _application(db)
        organization = db.get(Organization, application.organization_id)
        assert organization.status == OrganizationStatus.draft and organization.type == OrganizationType.association
        assert organization.legal_name == "Asociación Esperanza" and organization.tax_id is None
        assert [m.to for m in outbox] == ["laura@asociacion.org"] and "token=" in outbox[0].body

    def test_the_response_never_carries_the_token(self, client, outbox):
        r = _start(client)
        assert _token(outbox[-1]) not in r.text and set(r.json()) == {"message"}

    def test_only_the_hash_of_the_token_is_stored(self, client, db, outbox):
        _start(client)
        token = _token(outbox[-1])
        application = _application(db)
        assert application.access_token_hash != token and len(application.access_token_hash) == 64
        assert application.access_token_expires_at > datetime.now(timezone.utc) + timedelta(days=settings.application_link_days - 1)

    def test_the_consent_is_recorded_and_required(self, client, db):
        assert _start(client, consent=False).status_code == 422
        assert db.scalars(select(OrganizationApplication)).all() == []
        _start(client)
        application = _application(db)
        assert application.consent_version == settings.application_consent_version and application.consent_at

    def test_it_creates_no_account_and_the_organization_is_not_public(self, client, db):
        users_before = db.scalars(select(User.id)).all()
        _start(client)
        assert db.scalars(select(User.id)).all() == users_before
        assert "Asociación Esperanza" not in [a["legal_name"] for a in client.get("/catalogs/associations").json()]

    def test_the_email_is_normalized(self, client, db):
        _start(client, applicant_email="Laura@Asociacion.ORG")
        assert _application(db).applicant_email == "laura@asociacion.org"

    def test_a_second_start_with_the_same_email_and_type_only_sends_a_new_link(self, client, db, outbox):
        _start(client)
        first = _token(outbox[-1])
        r = _start(client, legal_name="Otro nombre")
        assert r.status_code == 202 and len(outbox) == 2
        assert len(db.scalars(select(OrganizationApplication)).all()) == 1
        assert client.get("/applications/current", headers=_auth(first)).status_code == 401
        assert client.get("/applications/current", headers=_auth(_token(outbox[-1]))).status_code == 200

    def test_the_same_person_may_apply_as_an_eca_too(self, client, db):
        _start(client)
        _start(client, type="eca", legal_name="ECA Esperanza")
        assert len(db.scalars(select(OrganizationApplication)).all()) == 2

    def test_a_tax_id_of_an_operating_organization_is_refused(self, client, db):
        factories.make_organization(db, "association", tax_id="900123456-7")
        r = _start(client, tax_id="900123456-7")
        assert r.status_code == 409 and r.json()["code"] == "organization_already_registered"

    def test_a_draft_cannot_squat_a_tax_id(self, client, db, outbox):
        _start(client, tax_id="900123456-7")
        r = _start(client, applicant_email="otra@asociacion.org", tax_id="900123456-7")
        assert r.status_code == 202  # two requests may claim it; the reviewer decides, the operating one is unique

    @pytest.mark.parametrize("body", [{"type": "citizen"}, {"legal_name": "x"}, {"applicant_email": "no-es-correo"}])
    def test_invalid_input_is_a_validation_error(self, client, body):
        assert _start(client, **body).status_code == 422

    def test_it_is_rate_limited(self, client, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_applications", "3/minute")
        limiter.reset()
        try:
            codes = [_start(client, applicant_email=f"p{i}@x.org").status_code for i in range(5)]
            assert codes == [202, 202, 202, 429, 429]
        finally:
            limiter.reset()

    def test_it_is_audited_without_personal_data(self, client, db):
        _start(client)
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "application.created")).one()
        assert entry.actor_id is None and "laura" not in str(entry.details).lower()


class TestAccessLink:
    def test_a_known_and_an_unknown_email_get_the_same_answer(self, client, outbox, started):
        known = client.post("/applications/access-link", json={"email": "laura@asociacion.org"})
        unknown = client.post("/applications/access-link", json={"email": "nadie@x.org"})
        assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
        assert [m.to for m in outbox] == ["laura@asociacion.org", "laura@asociacion.org"]

    def test_a_new_link_replaces_the_old_one(self, client, outbox, started):
        client.post("/applications/access-link", json={"email": "laura@asociacion.org"})
        assert client.get("/applications/current", headers=_auth(started)).status_code == 401
        assert client.get("/applications/current", headers=_auth(_token(outbox[-1]))).status_code == 200

    def test_a_closed_application_gets_no_link(self, client, db, outbox, started):
        db.get(Organization, _application(db).organization_id).status = OrganizationStatus.rejected
        db.commit()
        client.post("/applications/access-link", json={"email": "laura@asociacion.org"})
        assert len(outbox) == 1


class TestTheLink:
    def test_without_or_with_a_wrong_token_it_is_401(self, client, started):
        assert client.get("/applications/current").status_code == 401
        r = client.get("/applications/current", headers=_auth("nope"))
        assert r.status_code == 401 and r.json()["code"] == "invalid_application_link"

    def test_an_expired_link_is_401(self, client, db, started):
        application = _application(db)
        application.access_token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert client.get("/applications/current", headers=_auth(started)).status_code == 401

    @pytest.mark.parametrize("closed", [OrganizationStatus.approved, OrganizationStatus.rejected])
    def test_the_link_is_over_once_the_request_is_decided(self, client, db, started, closed):
        db.get(Organization, _application(db).organization_id).status = closed
        db.commit()
        assert client.get("/applications/current", headers=_auth(started)).status_code == 401

    def test_the_first_use_verifies_the_email(self, client, db, started):
        assert _application(db).email_verified_at is None
        client.get("/applications/current", headers=_auth(started))
        assert _application(db).email_verified_at is not None

    def test_each_link_opens_only_its_own_request(self, client, outbox, started):
        _start(client, applicant_email="otra@asociacion.org", legal_name="Otra Asociación")
        mine = client.get("/applications/current", headers=_auth(started)).json()
        theirs = client.get("/applications/current", headers=_auth(_token(outbox[-1]))).json()
        assert mine["legal_name"] == "Asociación Esperanza" and theirs["legal_name"] == "Otra Asociación"


class TestViewing:
    def test_a_new_request_says_what_is_missing(self, client, started):
        body = client.get("/applications/current", headers=_auth(started)).json()
        assert body["status"] == "draft" and body["can_edit"] is True and body["can_submit"] is False
        assert body["submissions_left"] == settings.application_max_submissions and body["submission_count"] == 0
        assert set(body["missing_fields"]) == {
            "tax_id", "legal_representative", "contact_email", "contact_phone", "address", "city",
            "applicant_id_type", "applicant_id_number", "applicant_phone",
            "documents:assoc_rut", "documents:assoc_legal_representative_id", "documents:assoc_legal_personality"}

    def test_it_exposes_nothing_internal(self, client, started):
        text = client.get("/applications/current", headers=_auth(started)).text
        assert "token" not in text and "hash" not in text


class TestEditing:
    def test_only_the_sent_fields_change(self, client, started):
        r = client.patch("/applications/current", headers=_auth(started), json={"city": "Cali", "address": "Cra 5"})
        assert r.status_code == 200 and r.json()["city"] == "Cali" and r.json()["legal_name"] == "Asociación Esperanza"

    def test_an_empty_text_clears_a_field_but_never_the_name(self, client, started):
        client.patch("/applications/current", headers=_auth(started), json={"city": "Cali"})
        r = client.patch("/applications/current", headers=_auth(started), json={"city": "  ", "legal_name": "  "})
        assert r.status_code == 200
        body = client.get("/applications/current", headers=_auth(started)).json()
        assert body["city"] is None and body["legal_name"] == "Asociación Esperanza"

    def test_null_clears_an_optional_field_including_the_email(self, client, started):
        client.patch("/applications/current", headers=_auth(started),
                     json={"contact_email": "contacto@org.org", "city": "Cali", "applicant_id_type": "CC"})
        r = client.patch("/applications/current", headers=_auth(started),
                         json={"contact_email": None, "city": None, "applicant_id_type": None})
        assert r.status_code == 200
        body = r.json()
        assert body["contact_email"] is None and body["city"] is None and body["applicant_id_type"] is None

    def test_an_empty_email_is_a_validation_error_so_the_web_sends_null(self, client, started):
        r = client.patch("/applications/current", headers=_auth(started), json={"contact_email": ""})
        assert r.status_code == 422

    def test_nothing_to_change_is_422(self, client, started):
        assert client.patch("/applications/current", headers=_auth(started), json={}).status_code == 422

    def test_the_type_and_the_applicants_email_cannot_be_changed(self, client, started):
        r = client.patch("/applications/current", headers=_auth(started),
                         json={"type": "eca", "applicant_email": "otro@x.org", "city": "Cali"})
        body = client.get("/applications/current", headers=_auth(started)).json()
        assert r.status_code == 200 and body["type"] == "association" and body["applicant_email"] == "laura@asociacion.org"

    def test_a_tax_id_of_an_operating_organization_is_refused(self, client, db, started):
        factories.make_organization(db, "association", tax_id="900999888-1")
        r = client.patch("/applications/current", headers=_auth(started), json={"tax_id": "900999888-1"})
        assert r.status_code == 409 and r.json()["code"] == "organization_already_registered"

    def test_the_audit_names_the_fields_not_their_values(self, client, db, started):
        client.patch("/applications/current", headers=_auth(started), json={"city": "Cali", "contact_phone": "3001112222"})
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "application.updated")).one()
        assert sorted(entry.details["fields"]) == ["city", "contact_phone"] and "3001112222" not in str(entry.details)

    def test_it_cannot_be_edited_after_sending(self, client, started):
        _complete(client, started)
        client.post("/applications/current/submit", headers=_auth(started))
        r = client.patch("/applications/current", headers=_auth(started), json={"city": "Cali"})
        assert r.status_code == 409 and r.json()["code"] == "application_locked"


class TestTheApplicantsOwnData:
    """The person who applies becomes the first administrator, so their document and phone are collected."""

    def test_they_are_edited_like_the_rest_and_audited_by_name(self, client, db, started):
        r = client.patch("/applications/current", headers=_auth(started), json={
            "applicant_id_type": "CC", "applicant_id_number": "1020304050", "applicant_phone": "3001234567"})
        assert r.status_code == 200 and r.json()["applicant_id_number"] == "1020304050"
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "application.updated")).one()
        assert sorted(entry.details["fields"]) == ["applicant_id_number", "applicant_id_type", "applicant_phone"]
        assert "1020304050" not in str(entry.details)

    def test_an_unknown_document_type_is_refused(self, client, started):
        r = client.patch("/applications/current", headers=_auth(started), json={"applicant_id_type": "ZZZ"})
        assert r.status_code == 422 and r.json()["code"] == "invalid_id_type"

    def test_without_them_the_request_cannot_be_sent(self, client, started):
        organization_only = {k: v for k, v in COMPLETE.items() if not k.startswith("applicant_")}
        client.patch("/applications/current", headers=_auth(started), json=organization_only)
        r = client.post("/applications/current/submit", headers=_auth(started))
        assert r.status_code == 422 and "applicant_id_number" in r.json()["detail"]


class TestSending:
    def test_an_incomplete_request_names_what_is_missing(self, client, started):
        client.patch("/applications/current", headers=_auth(started), json={"city": "Cali"})
        r = client.post("/applications/current/submit", headers=_auth(started))
        assert r.status_code == 422 and r.json()["code"] == "application_incomplete"
        assert "tax_id" in r.json()["detail"] and "city" not in r.json()["detail"]

    def test_a_complete_request_is_sent_and_confirmed_by_email(self, client, db, outbox, started):
        _complete(client, started)
        before = len(outbox)
        r = client.post("/applications/current/submit", headers=_auth(started))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "submitted" and body["can_edit"] is False and body["submission_count"] == 1
        assert body["submitted_at"] and len(outbox) == before + 1 and "Recibimos" in outbox[-1].subject
        assert db.get(Organization, _application(db).organization_id).status == OrganizationStatus.submitted

    def test_it_cannot_be_sent_twice(self, client, started):
        _complete(client, started)
        client.post("/applications/current/submit", headers=_auth(started))
        r = client.post("/applications/current/submit", headers=_auth(started))
        assert r.status_code == 409 and r.json()["code"] == "application_locked"

    def test_corrections_may_be_resent_up_to_the_limit(self, client, db, started, monkeypatch):
        monkeypatch.setattr(settings, "application_max_submissions", 2)
        _complete(client, started)

        def reviewer_asks_for_changes():
            db.get(Organization, _application(db).organization_id).status = OrganizationStatus.changes_requested
            db.commit()

        first = client.post("/applications/current/submit", headers=_auth(started))
        assert first.status_code == 200 and first.json()["submission_count"] == 1
        reviewer_asks_for_changes()
        second = client.post("/applications/current/submit", headers=_auth(started))
        assert second.status_code == 200 and second.json()["submission_count"] == 2
        assert second.json()["submissions_left"] == 0
        reviewer_asks_for_changes()
        third = client.post("/applications/current/submit", headers=_auth(started))
        assert third.status_code == 409 and third.json()["code"] == "too_many_submissions"
        assert client.get("/applications/current", headers=_auth(started)).json()["can_submit"] is False

    def test_a_tax_id_that_became_operating_in_the_meantime_is_refused(self, client, db, started):
        _complete(client, started)
        factories.make_organization(db, "association", tax_id=COMPLETE["tax_id"])
        r = client.post("/applications/current/submit", headers=_auth(started))
        assert r.status_code == 409 and r.json()["code"] == "organization_already_registered"

    def test_it_is_audited(self, client, db, started):
        _complete(client, started)
        client.post("/applications/current/submit", headers=_auth(started))
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "application.submitted")).one()
        assert entry.details == {"submission": 1}


class TestTheDatabaseRule:
    def test_two_requests_may_share_a_tax_id_but_an_operating_organization_is_unique(self, db):
        factories.make_organization(db, "association", tax_id="900555000-1", status=OrganizationStatus.draft)
        factories.make_organization(db, "association", tax_id="900555000-1", status=OrganizationStatus.submitted)
        factories.make_organization(db, "association", tax_id="900555000-1")  # approved
        with pytest.raises(IntegrityError):
            factories.make_organization(db, "association", tax_id="900555000-1")  # a second approved one
        db.rollback()
