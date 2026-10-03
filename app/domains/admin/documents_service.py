"""The reviewer's work on an application's documents: open one (a short-lived signed link, audited), give a
verdict on it, and what is still missing before the request can be approved."""
import uuid
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import signed_links
from app.core import storage as storage_module
from app.core.config import settings
from app.core.errors import ApiError
from app.domains.admin import applications_service as review_service
from app.domains.applications import catalog
from app.domains.applications.models import (
    OrganizationDocument,
    OrganizationDocumentType,
)
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.organizations.models import Organization
from app.domains.users.models import User

PURPOSE = "application-document"
DOWNLOAD_PATH = "/admin/documents/download"


def _not_found() -> ApiError:
    return ApiError("document_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado")


def _document(db: Session, organization_id: uuid.UUID, document_id: uuid.UUID) -> OrganizationDocument:
    """The document, only if it belongs to that application: another organization's looks like a missing one."""
    document = db.get(OrganizationDocument, document_id)
    if document is None or document.organization_id != organization_id:
        raise _not_found()
    return document


def _entry(db: Session, document: OrganizationDocument, reviewers: dict[uuid.UUID, dict]) -> dict:
    return {
        "id": document.id, "original_name": document.original_name, "content_type": document.content_type,
        "size_bytes": document.size_bytes, "uploaded_at": document.uploaded_at, "status": document.status,
        "review_comment": document.review_comment, "reviewed_at": document.reviewed_at,
        "reviewed_by": reviewers.get(document.reviewed_by) if document.reviewed_by else None,
    }


def slots_for_review(db: Session, organization: Organization) -> list[dict]:
    """Every document the organization is asked for plus anything else it uploaded, with the verdicts."""
    have = catalog.uploaded(db, organization.id)
    types = {t.code: t for t in catalog.active_types(db, organization)}
    extra = [code for code in have if code not in types]
    if extra:
        for t in db.scalars(select(OrganizationDocumentType).where(OrganizationDocumentType.code.in_(extra))).all():
            types[t.code] = t
    reviewer_ids = {d.reviewed_by for d in have.values() if d.reviewed_by}
    reviewers = review_service._reviewers(db, reviewer_ids)  # noqa: SLF001
    ordered = sorted(types.values(), key=lambda t: (t.sort_order, t.code))
    return [{
        "document_type": {"code": t.code, "label": t.label, "is_required": t.is_required},
        "document": _entry(db, have[t.code], reviewers) if t.code in have else None,
    } for t in ordered]


def access(db: Session, actor: User, organization_id: uuid.UUID, document_id: uuid.UUID) -> dict:
    """A link that opens the file for a few minutes. Minting it is the audited act of looking at the document."""
    review_service.load(db, organization_id)
    document = _document(db, organization_id, document_id)
    token, expires = signed_links.sign(
        PURPOSE, f"{document.id}|{document.sha256[:16]}", settings.document_url_ttl_seconds)
    audit.record(db, Action.ADMIN_DOCUMENT_VIEWED, actor=actor, target_type="organization_document",
                 target_id=document.id,
                 details={"organization_id": str(organization_id), "document_type": document.document_type_code})
    db.commit()
    return {"url": f"{DOWNLOAD_PATH}/{token}", "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc)}


def fetch_for_download(db: Session, token: str) -> tuple[bytes, OrganizationDocument]:
    """The bytes behind a valid, unexpired link. The link is tied to the exact file it was minted for."""
    invalid = ApiError("invalid_document_link", status_code=status.HTTP_401_UNAUTHORIZED,
                       detail="El enlace del documento no es válido o venció")
    try:
        document_id, fingerprint = signed_links.verify(PURPOSE, token).split("|")
        document = db.get(OrganizationDocument, uuid.UUID(document_id))
    except (signed_links.InvalidLink, ValueError):
        raise invalid
    if document is None or document.sha256[:16] != fingerprint:
        raise invalid  # replaced since the link was minted
    try:
        data = storage_module.get_storage().get(document.storage_key)
    except storage_module.StorageError:
        raise _not_found()
    audit.record(db, Action.ADMIN_DOCUMENT_DOWNLOADED, target_type="organization_document", target_id=document.id,
                 details={"organization_id": str(document.organization_id),
                          "document_type": document.document_type_code})
    db.commit()
    return data, document


def review(
    db: Session, actor: User, organization_id: uuid.UUID, document_id: uuid.UUID, new_status: str, comment: str | None,
) -> dict:
    """The verdict on one document, while the request is waiting for review."""
    _, organization = review_service.load(db, organization_id)
    if organization.status not in review_service.REVIEWABLE:
        raise ApiError("application_not_reviewable", status_code=status.HTTP_409_CONFLICT,
                       detail="La solicitud no está esperando revisión")
    document = _document(db, organization_id, document_id)
    comment = comment.strip() if comment else None
    if new_status in ("missing", "not_compliant") and not comment:
        raise ApiError("comment_required", status_code=422, detail="Indica qué está mal en este documento")
    document.status = new_status
    document.review_comment = comment
    document.reviewed_by = actor.id
    document.reviewed_at = datetime.now(timezone.utc)
    audit.record(db, Action.APPLICATION_DOCUMENT_REVIEWED, actor=actor, target_type="organization_document",
                 target_id=document.id,
                 details={"organization_id": str(organization_id), "document_type": document.document_type_code,
                          "status": new_status, "has_comment": bool(comment)})
    db.commit()
    return _entry(db, document, review_service._reviewers(db, {actor.id}))  # noqa: SLF001


def required_not_approved(db: Session, organization: Organization) -> list[str]:
    """Required documents that are missing or whose verdict is not `ok`: they block the approval."""
    have = catalog.uploaded(db, organization.id)
    return [t.code for t in catalog.active_types(db, organization)
            if t.is_required and (t.code not in have or have[t.code].status != "ok")]


def snapshot_for_changes(db: Session, organization: Organization) -> list[dict]:
    """What to tell the applicant when a request goes back: required documents never attached and documents the
    reviewer marked missing or not compliant, each with the reason. Saved with the review."""
    have = catalog.uploaded(db, organization.id)
    items: list[dict] = []
    for t in catalog.active_types(db, organization):
        document = have.get(t.code)
        if document is None:
            if t.is_required:
                items.append({"code": t.code, "label": t.label, "status": "missing",
                              "comment": "No se adjuntó este documento"})
        elif document.status in ("missing", "not_compliant"):
            items.append({"code": t.code, "label": t.label, "status": document.status,
                          "comment": document.review_comment})
    return items
