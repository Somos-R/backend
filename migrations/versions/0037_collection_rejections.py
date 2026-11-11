"""collection rejections: the history of every rejection of a pickup request and its resubmission

- `collection_rejections` is a new table: one row per rejection (`collection_id`, `reason_code`, `rejected_by`,
  `rejected_at`) and, once the requester corrects the request and sends it again, its `resubmitted_at`.
  `collections` keeps holding only the current state.
- Backfill: every request that is `rejected` today gets its row, copied from `collections` (`rejected_at` falls
  back to `updated_at` where it is missing; `rejected_by` may stay NULL).

Additive only. Downgrade drops the table (and the history recorded after the upgrade); the current state of each
request stays in `collections`.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: Union[str, Sequence[str], None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_rejections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("collections.id"), nullable=False),
        sa.Column("reason_code", sa.String(40), sa.ForeignKey("rejection_reasons.code"), nullable=False),
        sa.Column("rejected_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resubmitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_collection_rejections_collection_rejected", "collection_rejections",
                    ["collection_id", "rejected_at"])
    op.execute("""
        INSERT INTO collection_rejections (id, collection_id, reason_code, rejected_by, rejected_at)
        SELECT gen_random_uuid(), id, rejection_reason_code, rejected_by, COALESCE(rejected_at, updated_at)
        FROM collections
        WHERE status = 'rejected'
    """)


def downgrade() -> None:
    op.drop_index("ix_collection_rejections_collection_rejected", table_name="collection_rejections")
    op.drop_table("collection_rejections")
