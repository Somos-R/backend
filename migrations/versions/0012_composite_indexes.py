"""composite indexes for the list/filter queries

Adds the composite indexes the endpoints actually use and drops the two single-column
indexes they make redundant (a composite index also serves queries on its leading column).

Already present and kept: idx_users_coverage_area (GiST, migration 0001), idx_weighings_fecha,
ix_transactions_fecha, ix_transactions_status.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_weighings_recycler_estado_fecha", "weighings", ["recycler_id", "estado", "fecha"])
    op.create_index("ix_transactions_type_status_fecha", "transactions", ["type", "status", "fecha"])
    op.create_index("ix_users_user_type_verification", "users", ["user_type_code", "verification_status"])

    # Redundant: leading columns of the composites above.
    op.drop_index("idx_weighings_recycler", table_name="weighings")
    op.drop_index("ix_transactions_type", table_name="transactions")


def downgrade() -> None:
    op.create_index("ix_transactions_type", "transactions", ["type"])
    op.create_index("idx_weighings_recycler", "weighings", ["recycler_id"])

    op.drop_index("ix_users_user_type_verification", table_name="users")
    op.drop_index("ix_transactions_type_status_fecha", table_name="transactions")
    op.drop_index("ix_weighings_recycler_estado_fecha", table_name="weighings")
