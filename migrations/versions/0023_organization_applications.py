"""organization_applications: the public request to join, before there is an account

An application is an organization in `draft` plus this row (applicant, magic-link token hash, consent).
Because anyone can now start an application with any tax id, uniqueness of (type, tax_id) only applies to
organizations that operate (approved or suspended): a draft cannot squat someone's NIT. Whether a request
duplicates an operating organization is checked when it is submitted.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"),
                  nullable=False, unique=True),
        sa.Column("applicant_name", sa.String(255), nullable=False),
        sa.Column("applicant_email", sa.String(255), nullable=False),
        sa.Column("access_token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_version", sa.String(20), nullable=False),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_organization_applications_applicant_email", "organization_applications", ["applicant_email"])

    op.drop_index("uq_organizations_type_tax_id", table_name="organizations")
    op.create_index(
        "uq_organizations_type_tax_id", "organizations", ["type", "tax_id"], unique=True,
        postgresql_where=sa.text("tax_id IS NOT NULL AND status IN ('approved', 'suspended')"))
    op.create_index("ix_organizations_type_tax_id", "organizations", ["type", "tax_id"])


def downgrade() -> None:
    op.drop_index("ix_organizations_type_tax_id", table_name="organizations")
    op.drop_index("uq_organizations_type_tax_id", table_name="organizations")
    # Applications in progress may now repeat a tax id: drop those before restoring the stricter rule.
    op.execute(
        "DELETE FROM organization_applications WHERE organization_id IN "
        "(SELECT id FROM organizations WHERE status NOT IN ('approved', 'suspended'))")
    op.execute("DELETE FROM organizations WHERE status NOT IN ('approved', 'suspended') "
               "AND id NOT IN (SELECT organization_id FROM users WHERE organization_id IS NOT NULL)")
    op.create_index("uq_organizations_type_tax_id", "organizations", ["type", "tax_id"], unique=True,
                    postgresql_where=sa.text("tax_id IS NOT NULL"))
    op.drop_index("ix_organization_applications_applicant_email", table_name="organization_applications")
    op.drop_table("organization_applications")
