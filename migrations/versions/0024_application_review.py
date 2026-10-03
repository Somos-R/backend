"""Review of applications: who applies as a person, who reviews, and the append-only review history

- The person who applies becomes the organization's first administrator when approved, so their document
  and phone are collected on the application.
- `organization_reviews` keeps every review decision (approved, changes requested, rejected) with its
  summary. Like the audit log, a trigger rejects UPDATE and DELETE: it is what the applicant is told.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DECISIONS = ("approved", "changes_requested", "rejected")


def upgrade() -> None:
    op.add_column("organization_applications", sa.Column(
        "applicant_id_type", sa.String(10), sa.ForeignKey("document_types.code"), nullable=True))
    op.add_column("organization_applications", sa.Column("applicant_id_number", sa.String(20), nullable=True))
    op.add_column("organization_applications", sa.Column("applicant_phone", sa.String(20), nullable=True))
    op.add_column("organization_applications", sa.Column(
        "reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("organization_applications", sa.Column(
        "review_started_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "organization_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("submission_number", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('" + "', '".join(DECISIONS) + "')", name="ck_organization_reviews_decision"),
    )
    op.create_index("ix_organization_reviews_organization", "organization_reviews", ["organization_id", "created_at"])
    op.execute("""
        CREATE FUNCTION organization_reviews_reject_changes() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'organization_reviews is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER organization_reviews_append_only
        BEFORE UPDATE OR DELETE ON organization_reviews
        FOR EACH ROW EXECUTE FUNCTION organization_reviews_reject_changes()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS organization_reviews_append_only ON organization_reviews")
    op.execute("DROP FUNCTION IF EXISTS organization_reviews_reject_changes()")
    op.drop_index("ix_organization_reviews_organization", table_name="organization_reviews")
    op.drop_table("organization_reviews")
    for column in ("review_started_at", "reviewer_id", "applicant_phone", "applicant_id_number", "applicant_id_type"):
        op.drop_column("organization_applications", column)
