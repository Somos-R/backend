import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from geoalchemy2 import Geometry
from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CollectionStatus(str, enum.Enum):
    """Where a pickup request stands. A recycler never accepts one: a confirmed route assigns it."""

    pending_assignment = "pending_assignment"
    assigned = "assigned"
    rejected = "rejected"
    cancelled = "cancelled"


class Collection(Base):
    """A request from a citizen or a building to have their recyclable material picked up."""

    __tablename__ = "collections"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_collections_latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_collections_longitude_range"),
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
    location:     Mapped[object]           = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False)
    # Codes of the `materials` catalog the requester says they have.
    materials:    Mapped[list[str]]        = mapped_column(JSONB, nullable=False)
    notes:        Mapped[str | None]       = mapped_column(Text, nullable=True)
    scheduled_date: Mapped[date | None]    = mapped_column(Date, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:   Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at:   Mapped[datetime]         = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
