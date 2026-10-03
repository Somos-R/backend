"""What documents an application is asked for and what it has attached. Queries only, no rules: both the
application service and the documents service build on it."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.applications.models import (
    OrganizationDocument,
    OrganizationDocumentType,
)
from app.domains.organizations.models import Organization


def active_types(db: Session, organization: Organization) -> list[OrganizationDocumentType]:
    """What this type of organization is asked for, in the order the catalog defines."""
    return list(db.scalars(select(OrganizationDocumentType).where(
        OrganizationDocumentType.organization_type == organization.type,
        OrganizationDocumentType.is_active.is_(True),
    ).order_by(OrganizationDocumentType.sort_order, OrganizationDocumentType.code)).all())


def uploaded(db: Session, organization_id: uuid.UUID) -> dict[str, OrganizationDocument]:
    return {d.document_type_code: d for d in db.scalars(select(OrganizationDocument).where(
        OrganizationDocument.organization_id == organization_id)).all()}


def missing_required(db: Session, organization: Organization) -> list[str]:
    """Required document types with no file yet, as `documents:<code>` (they go in `missing_fields`)."""
    have = uploaded(db, organization.id)
    return [f"documents:{t.code}" for t in active_types(db, organization) if t.is_required and t.code not in have]
