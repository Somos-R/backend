"""idempotency_keys: remember the response to a creation so a retry with the same key creates nothing

A new table, nothing existing changes. The mobile app resends what it created offline; with an
`Idempotency-Key` header the server returns the original response instead of recording the same weighing or
pickup request twice. One row per (account, operation, key), kept for 7 days.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.SmallInteger, nullable=True),
        sa.Column("response_body", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("uq_idempotency_keys_user_operation_key", "idempotency_keys",
                    ["user_id", "operation", "key"], unique=True)
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_index("uq_idempotency_keys_user_operation_key", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
