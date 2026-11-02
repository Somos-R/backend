"""weighings.record_hash: SHA-256 of what was recorded when the weighing was registered

A nullable column and nothing else, so no existing row changes. Weighings registered before this have no hash and
verify as "not hashed" instead of being backfilled: the hash is only meaningful when it was computed at the moment
of registration.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("weighings", sa.Column("record_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("weighings", "record_hash")
