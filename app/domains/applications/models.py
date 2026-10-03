import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


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
