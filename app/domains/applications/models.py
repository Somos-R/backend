import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domains.organizations.enums import OrganizationType


class OrganizationApplication(Base):
    """The request of an Association or an ECA to join Somos R, before it has an account.

    The organization itself (`organizations`) carries the data and the onboarding `status`; this row carries
    what only concerns the request: who applies, how they come back to it (a magic link, since they have no
    account) and the consent they gave. Only the SHA-256 of the link's token is stored.
    """

    __tablename__ = "organization_applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), unique=True, nullable=False)

    applicant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    applicant_email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    # The applicant becomes the organization's first administrator when it is approved: the account needs a document.
    applicant_id_type: Mapped[str | None] = mapped_column(String(10), ForeignKey("document_types.code"), nullable=True)
    applicant_id_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    applicant_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    access_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    consent_version: Mapped[str] = mapped_column(String(20), nullable=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submission_count: Mapped[int] = mapped_column(Integer, server_default="0", default=0, nullable=False)

    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    review_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class OrganizationReview(Base):
    """One decision of a reviewer on an application. Append-only (a database trigger rejects UPDATE and DELETE):
    it is the history, and what the applicant is told, of why a request was approved, sent back or rejected."""

    __tablename__ = "organization_reviews"
    __table_args__ = (
        CheckConstraint("decision IN ('approved', 'changes_requested', 'rejected')", name="ck_organization_reviews_decision"),
        Index("ix_organization_reviews_organization", "organization_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    submission_number: Mapped[int] = mapped_column(Integer, nullable=False)  # which send of the applicant it answers
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OrganizationDocumentType(Base):
    """A document an organization must (or may) attach to its application. A catalog the backoffice edits:
    the list can change without touching code. Codes are unique across both types of organization."""

    __tablename__ = "organization_document_types"
    __table_args__ = (Index("ix_organization_document_types_type", "organization_type", "is_active"),)

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    organization_type: Mapped[OrganizationType] = mapped_column(
        Enum(OrganizationType, name="organization_type", create_type=False), nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, server_default="true", default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0", default=0, nullable=False)


class OrganizationDocument(Base):
    """A file an organization attached for one type of document (one per type: a new upload replaces it).

    The file lives in private storage under a random `storage_key`; it is never served directly. `status` is
    the verdict of the reviewer on it and goes back to `pending` whenever the file is replaced.
    """

    __tablename__ = "organization_documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "document_type_code", name="uq_organization_document_type"),
        CheckConstraint(
            "status IN ('pending', 'ok', 'missing', 'not_compliant')", name="ck_organization_documents_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True, nullable=False)
    document_type_code: Mapped[str] = mapped_column(
        String(40), ForeignKey("organization_document_types.code"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False)
    original_name: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default="pending", default="pending", nullable=False)
    review_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
