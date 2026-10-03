import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domains.organizations.enums import (
    LinkStatus,
    OrganizationStatus,
    OrganizationType,
)


class Organization(Base):
    """An Association or an ECA as a customer of the platform.

    Every person of an organization's staff belongs to exactly one (`users.organization_id`); it is
    what lets one organization's data be kept apart from another's. Its `status` follows the
    onboarding described in the backoffice design: an organization is reviewed and approved once.
    """

    __tablename__ = "organizations"
    __table_args__ = (
        # One tax id per type among organizations that operate (an Association and an ECA may share a NIT).
        # Applications in progress are not counted: anyone may start one with any tax id, so a draft must not
        # block the real organization. Duplicates of an operating one are refused when the request is submitted.
        Index("uq_organizations_type_tax_id", "type", "tax_id", unique=True,
              postgresql_where=text("tax_id IS NOT NULL AND status IN ('approved', 'suspended')")),
        Index("ix_organizations_type_tax_id", "type", "tax_id"),
        Index("ix_organizations_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[OrganizationType] = mapped_column(Enum(OrganizationType, name="organization_type"), nullable=False)
    status: Mapped[OrganizationStatus] = mapped_column(
        Enum(OrganizationStatus, name="organization_status"),
        server_default=OrganizationStatus.draft.value, default=OrganizationStatus.draft, nullable=False)

    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(50), nullable=True)  # NIT
    legal_representative: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class EcaAssociationLink(Base):
    """Many-to-many between ECAs and Associations, always started by the ECA and decided by the Association.

    One row per pair: asking again after a rejection or a removal reuses it (the history lives in the
    audit trail). Only an `active` link makes the Association's recyclers deliver to that ECA.
    """

    __tablename__ = "eca_association_links"
    __table_args__ = (
        UniqueConstraint("eca_id", "association_id", name="uq_eca_association_link"),
        Index("ix_eca_association_links_association", "association_id", "status"),
        Index("ix_eca_association_links_eca", "eca_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    eca_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    association_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    status: Mapped[LinkStatus] = mapped_column(
        Enum(LinkStatus, name="link_status"), server_default=LinkStatus.requested.value,
        default=LinkStatus.requested, nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    eca: Mapped[Organization] = relationship("Organization", foreign_keys=[eca_id])
    association: Mapped[Organization] = relationship("Organization", foreign_keys=[association_id])
