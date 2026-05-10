"""verification_status column: restore string enum (pending/verified/rejected)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TYPE IF EXISTS verificationstatus")
    op.execute("CREATE TYPE verificationstatus AS ENUM ('pending', 'verified', 'rejected')")
    op.add_column(
        "users",
        sa.Column(
            "verification_status",
            sa.Enum("pending", "verified", "rejected", name="verificationstatus"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "verification_status")
    op.execute("DROP TYPE IF EXISTS verificationstatus")
