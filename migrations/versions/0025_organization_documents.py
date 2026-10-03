"""organization_document_types and organization_documents

The list of documents an application needs is a catalog (editable from the backoffice), seeded with what the
design already knows: an ECA needs its RUT and its enabling certificate from the SSPD (Decreto 596 de 2016); an
Association needs its tax id, its legal representative ID and its legal personality. The list is still being
defined with the business, so every entry can be changed, deactivated or added without code.

Uploaded files are rows of `organization_documents` (one per type); the bytes live in private storage.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEED = [
    ("eca_rut", "eca", "RUT de la ECA", 10),
    ("eca_sspd_habilitation", "eca", "Certificado de habilitación ante la SSPD", 20),
    ("assoc_rut", "association", "RUT o certificado del NIT de la asociación", 10),
    ("assoc_legal_representative_id", "association", "Documento de identidad del representante legal", 20),
    ("assoc_legal_personality", "association", "Documento de personería jurídica", 30),
]


def upgrade() -> None:
    org_type = postgresql.ENUM("association", "eca", name="organization_type", create_type=False)
    op.create_table(
        "organization_document_types",
        sa.Column("code", sa.String(40), primary_key=True),
        sa.Column("organization_type", org_type, nullable=False),
        sa.Column("label", sa.String(150), nullable=False),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_organization_document_types_type", "organization_document_types",
                    ["organization_type", "is_active"])

    op.create_table(
        "organization_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("document_type_code", sa.String(40), sa.ForeignKey("organization_document_types.code"),
                  nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("original_name", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("review_comment", sa.String(500), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "document_type_code", name="uq_organization_document_type"),
        sa.CheckConstraint("status IN ('pending', 'ok', 'missing', 'not_compliant')",
                           name="ck_organization_documents_status"),
    )
    op.create_index("ix_organization_documents_organization_id", "organization_documents", ["organization_id"])

    table = sa.table(
        "organization_document_types",
        sa.column("code", sa.String), sa.column("organization_type", org_type), sa.column("label", sa.String),
        sa.column("is_required", sa.Boolean), sa.column("is_active", sa.Boolean), sa.column("sort_order", sa.Integer))
    op.bulk_insert(table, [
        {"code": code, "organization_type": kind, "label": label, "is_required": True, "is_active": True,
         "sort_order": order}
        for code, kind, label, order in SEED])


def downgrade() -> None:
    op.drop_index("ix_organization_documents_organization_id", table_name="organization_documents")
    op.drop_table("organization_documents")
    op.drop_index("ix_organization_document_types_type", table_name="organization_document_types")
    op.drop_table("organization_document_types")
