"""unaccent extension for text search

`GET /users?q=` matches names, documents and emails ignoring case and accents
(`Perez` finds `Pérez`). `unaccent` is a trusted extension: the database owner can create it.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS unaccent")
