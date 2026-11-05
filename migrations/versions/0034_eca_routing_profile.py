"""eca routing profile: the reception hours and accepted materials route planning needs from an ECA

`organizations.reception_hours` (when the ECA receives material: day, opening and closing time) and
`organizations.accepted_materials` (codes of the `materials` catalog it takes) are new, nullable JSONB columns.
NULL means "not declared yet"; existing rows keep it. `organizations.location` (migration 0032) is unchanged:
the same endpoint now fills it.

Additive only. Downgrade drops both columns and whatever was stored in them.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: Union[str, Sequence[str], None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_COLUMNS = ("reception_hours", "accepted_materials")


def upgrade() -> None:
    for column in NEW_COLUMNS:
        op.add_column("organizations", sa.Column(column, postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    for column in NEW_COLUMNS:
        op.drop_column("organizations", column)
