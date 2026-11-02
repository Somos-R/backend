"""collections: pickup requests from citizens and buildings

A new table, nothing existing changes. A citizen or a building asks for their recyclable material to be picked
up; the request starts in `pending_assignment` and only a confirmed route moves it to `assigned` (the recycler
never accepts it). The location is kept twice: latitude/longitude as given, and as a PostGIS point with a GiST
index for the route engine.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-02

"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: Union[str, Sequence[str], None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = ("pending_assignment", "assigned", "rejected", "cancelled")


def upgrade() -> None:
    collection_status = postgresql.ENUM(*STATUSES, name="collection_status", create_type=False)
    collection_status.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("requester_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", collection_status, nullable=False),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("location", geoalchemy2.types.Geometry(
            geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("materials", postgresql.JSONB, nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("scheduled_date", sa.Date, nullable=True),
        sa.Column("cancellation_reason", sa.Text, nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_collections_latitude_range"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_collections_longitude_range"),
    )
    op.create_index("ix_collections_requester_status_created", "collections",
                    ["requester_id", "status", "created_at"])
    op.execute("CREATE INDEX ix_collections_location ON collections USING GIST (location)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_collections_location")
    op.drop_index("ix_collections_requester_status_created", table_name="collections")
    op.drop_table("collections")
    postgresql.ENUM(name="collection_status").drop(op.get_bind(), checkfirst=False)
