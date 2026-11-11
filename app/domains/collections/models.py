import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CollectionStatus(str, enum.Enum):
    """Where a pickup request stands. A recycler never accepts one: a confirmed route assigns it."""

    pending_assignment = "pending_assignment"
    assigned = "assigned"
    rejected = "rejected"
    cancelled = "cancelled"


class RejectionReason(Base):
    """Why an association turns a pickup request down: always a code from this catalog, never free text, so the
    reasons can be counted."""

    __tablename__ = "rejection_reasons"

    code:           Mapped[str]  = mapped_column(String(40), primary_key=True)
    label:          Mapped[str]  = mapped_column(String(150), nullable=False)
    # Whether the requester can fix the request and send it again, or the rejection is final.
    is_correctable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_active:      Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Collection(Base):
    """A request from a citizen or a building to have their recyclable material picked up."""

    __tablename__ = "collections"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_collections_latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_collections_longitude_range"),
        CheckConstraint("(status = 'rejected') = (rejection_reason_code IS NOT NULL)",
                        name="ck_collections_rejected_has_reason"),
        # A requester's own history filtered by state, newest first.
        Index("ix_collections_requester_status_created", "requester_id", "status", "created_at"),
    )

    id:           Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_id: Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status:       Mapped[CollectionStatus] = mapped_column(
        Enum(CollectionStatus, name="collection_status"), nullable=False, default=CollectionStatus.pending_assignment)
    address:      Mapped[str]              = mapped_column(String(255), nullable=False)
    latitude:     Mapped[Decimal]          = mapped_column(Numeric(9, 6), nullable=False)
    longitude:    Mapped[Decimal]          = mapped_column(Numeric(9, 6), nullable=False)
    # The same point for PostGIS (the route engine works on it); always built from latitude/longitude.
    # GiST-indexed in the migration; query it through app/core/spatial.py.
    location:     Mapped[object]           = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False)
    # Codes of the `materials` catalog the requester says they have.
    materials:    Mapped[list[str]]        = mapped_column(JSONB, nullable=False)
    notes:        Mapped[str | None]       = mapped_column(Text, nullable=True)
    scheduled_date: Mapped[date | None]    = mapped_column(Date, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)
    # The association that handles the request, set when it is created (`assign_association`). While it is null no
    # association reaches the request.
    association_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    rejection_reason_code: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("rejection_reasons.code"), nullable=True)
    rejected_by:  Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    rejected_at:  Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)
    # When the device saved it, if it arrived through POST /sync/batch (the device's clock, not the record's date).
    client_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:   Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at:   Mapped[datetime]         = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    rejection_reason: Mapped[RejectionReason | None] = relationship()


class CollectionRejection(Base):
    """One rejection of a pickup request, kept after the requester corrects it and sends it again: the history of
    every rejection and resubmission. `collections` only holds the current state."""

    __tablename__ = "collection_rejections"
    __table_args__ = (Index("ix_collection_rejections_collection_rejected", "collection_id", "rejected_at"),)

    id:            Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID]        = mapped_column(
        UUID(as_uuid=True), ForeignKey("collections.id"), nullable=False)
    reason_code:   Mapped[str]              = mapped_column(
        String(40), ForeignKey("rejection_reasons.code"), nullable=False)
    # Null only for rejections copied from before this table existed that did not record who rejected.
    rejected_by:   Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    rejected_at:   Mapped[datetime]         = mapped_column(DateTime(timezone=True), nullable=False)
    # When the requester sent the corrected request back to the queue; null while the rejection stands.
    resubmitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
