import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domains.organizations.enums import OrganizationStatus, OrganizationType


class Organization(Base):
    """An Association or an ECA as a customer of the platform.

    Every person of an organization's staff belongs to exactly one (`users.organization_id`); it is
    what lets one organization's data be kept apart from another's. Its `status` follows the
    onboarding described in the backoffice design: an organization is reviewed and approved once.
    """

    __tablename__ = "organizations"
    __table_args__ = (
        # One tax id per type of organization (an Association and an ECA may share a NIT).
        Index("uq_organizations_type_tax_id", "type", "tax_id", unique=True, postgresql_where=text("tax_id IS NOT NULL")),
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
