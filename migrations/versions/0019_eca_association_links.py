"""eca_association_links: the many-to-many between ECAs and Associations

The ECA asks, the Association decides (requested -> active / rejected), either can remove it later.
One row per pair; asking again reuses the row.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = ("requested", "active", "rejected", "removed")


def upgrade() -> None:
    status_enum = postgresql.ENUM(*STATUSES, name="link_status", create_type=False)
    status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "eca_association_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("eca_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("association_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="requested"),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("eca_id", "association_id", name="uq_eca_association_link"),
        sa.CheckConstraint("eca_id <> association_id", name="ck_link_distinct_organizations"),
    )
    op.create_index("ix_eca_association_links_association", "eca_association_links", ["association_id", "status"])
    op.create_index("ix_eca_association_links_eca", "eca_association_links", ["eca_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_eca_association_links_eca", table_name="eca_association_links")
    op.drop_index("ix_eca_association_links_association", table_name="eca_association_links")
    op.drop_table("eca_association_links")
    op.execute("DROP TYPE link_status")
