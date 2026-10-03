"""The documents an application attaches. One file per type of document: a new upload replaces the old one.

Rules: only while the application is in the applicant's hands (draft or changes requested), only the types the
catalog lists for the organization's type, only PDF, PNG or JPG recognized by their content, within the size
limit. The bytes go to private storage under a random key; the original name is only a label.
"""

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import storage as storage_module
from app.core import uploads
from app.core.errors import ApiError
from app.domains.applications import service
from app.domains.applications.catalog import active_types, uploaded
from app.domains.applications.models import (
    OrganizationApplication,
    OrganizationDocument,
    OrganizationDocumentType,
)
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.organizations.models import Organization


def document_view(document: OrganizationDocument | None) -> dict | None:
    if document is None:
        return None
    return {
        "id": document.id, "original_name": document.original_name, "content_type": document.content_type,
        "size_bytes": document.size_bytes, "uploaded_at": document.uploaded_at, "status": document.status,
        "review_comment": document.review_comment,
    }


def listing(db: Session, organization: Organization) -> list[dict]:
    """Every document the organization is asked for, each with what it has uploaded (or null)."""
    have = uploaded(db, organization.id)
    return [{
        "document_type": {"code": t.code, "label": t.label, "is_required": t.is_required},
        "document": document_view(have.get(t.code)),
    } for t in active_types(db, organization)]


def _type_for(db: Session, organization: Organization, type_code: str) -> OrganizationDocumentType:
    document_type = db.get(OrganizationDocumentType, type_code)
    if (document_type is None or not document_type.is_active
            or document_type.organization_type != organization.type):
        raise ApiError("document_type_not_found", status_code=status.HTTP_404_NOT_FOUND,
                       detail="Ese documento no se pide a esta organización")
    return document_type


def upload(
    db: Session, application: OrganizationApplication, organization: Organization, type_code: str,
    data: bytes, filename: str | None,
) -> dict:
    service.ensure_editable(organization)
    document_type = _type_for(db, organization, type_code)
    checked = uploads.check_document(data)

    store = storage_module.get_storage()
    key = storage_module.new_key("applications", str(organization.id))
    store.put(key, data)
    existing = db.scalars(select(OrganizationDocument).where(
        OrganizationDocument.organization_id == organization.id,
        OrganizationDocument.document_type_code == document_type.code).with_for_update()).first()
    old_key = existing.storage_key if existing else None
    try:
        if existing is None:
            existing = OrganizationDocument(organization_id=organization.id, document_type_code=document_type.code)
            db.add(existing)
        existing.storage_key = key
        existing.original_name = uploads.display_name(filename)
        existing.content_type = checked.content_type
        existing.size_bytes = checked.size_bytes
        existing.sha256 = checked.sha256
        existing.status = "pending"  # a new file has not been reviewed
        existing.review_comment = None
        existing.reviewed_by = None
        existing.reviewed_at = None
        audit.record(db, Action.APPLICATION_DOCUMENT_UPLOADED, target_type="organization", target_id=organization.id,
                     details={"document_type": document_type.code, "size_bytes": checked.size_bytes,
                              "replaced": old_key is not None})
        db.commit()
    except Exception:
        db.rollback()
        store.delete(key)  # nothing points to the new file
        raise
    if old_key:
        store.delete(old_key)
    db.refresh(existing)
    return document_view(existing)  # type: ignore[return-value]


def delete(db: Session, application: OrganizationApplication, organization: Organization, type_code: str) -> None:
    service.ensure_editable(organization)
    document = db.scalars(select(OrganizationDocument).where(
        OrganizationDocument.organization_id == organization.id,
        OrganizationDocument.document_type_code == type_code).with_for_update()).first()
    if document is None:
        raise ApiError("document_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="No hay un archivo para ese documento")
    key = document.storage_key
    db.delete(document)
    audit.record(db, Action.APPLICATION_DOCUMENT_DELETED, target_type="organization", target_id=organization.id,
                 details={"document_type": type_code})
    db.commit()
    storage_module.get_storage().delete(key)
