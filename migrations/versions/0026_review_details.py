"""organization_reviews.details: which documents a review sent back, and why

When a reviewer asks for changes, the documents that were missing or not compliant are saved with the review
(a snapshot of what the applicant was told). The table stays append-only: the trigger from 0024 still applies
and this only adds a column.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organization_reviews", sa.Column("details", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("organization_reviews", "details")
