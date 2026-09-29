"""audit_log: append-only trail of security-relevant events

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-29

The trigger makes the table append-only at the database level, so not even a bug (or an SQL
injection running as the application user) can rewrite history through UPDATE or DELETE.
TRUNCATE and DROP remain available to the table owner: use a separate, more privileged role
for migrations and revoke those from the application role in production.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False, server_default="success"),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("actor_role", sa.String(20), nullable=True),
        sa.Column("target_type", sa.String(30), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("outcome IN ('success', 'failure')", name="ck_audit_log_outcome"),
    )
    op.create_index("ix_audit_log_occurred_at", "audit_log", ["occurred_at"])
    op.create_index("ix_audit_log_actor_occurred", "audit_log", ["actor_id", "occurred_at"])
    op.create_index("ix_audit_log_action_occurred", "audit_log", ["action", "occurred_at"])
    op.create_index("ix_audit_log_target", "audit_log", ["target_type", "target_id"])

    op.execute("""
        CREATE FUNCTION audit_log_reject_changes() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER audit_log_append_only
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_reject_changes()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_reject_changes()")
    op.drop_table("audit_log")
