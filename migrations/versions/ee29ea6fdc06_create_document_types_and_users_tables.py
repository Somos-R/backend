"""create document_types and users tables

Revision ID: ee29ea6fdc06
Revises:
Create Date: 2026-04-13 04:22:03.460581

"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ee29ea6fdc06"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- document_types (must be created before users due to FK) ---
    document_types_table = op.create_table(
        "document_types",
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("code"),
    )

    # Seed: initial document types — add new rows here when needed
    op.bulk_insert(
        document_types_table,
        [
            {"code": "CC",  "label": "Cédula de Ciudadanía",               "is_active": True},
            {"code": "CE",  "label": "Cédula de Extranjería",               "is_active": True},
            {"code": "NIT", "label": "Número de Identificación Tributaria", "is_active": True},
            {"code": "PA",  "label": "Pasaporte",                           "is_active": True},
            {"code": "PPT", "label": "Permiso de Protección Temporal",      "is_active": True},
        ],
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("id_type", sa.String(length=10), nullable=False),
        sa.Column("id_number", sa.String(length=20), nullable=False),
        sa.Column(
            "user_type",
            sa.Enum(
                "citizen",
                "building_admin",
                "recycler",
                "eca_operator",
                "asobeum_admin",
                "b2b_client",
                name="usertype",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # citizen / building_admin
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        # building_admin
        sa.Column("building_name", sa.String(length=255), nullable=True),
        sa.Column("num_units", sa.Integer(), nullable=True),
        sa.Column("representation_document", sa.Text(), nullable=True),
        # recycler
        sa.Column("profile_picture", sa.Text(), nullable=True),
        sa.Column("id_picture", sa.Text(), nullable=True),
        sa.Column(
            "verification_status",
            sa.Enum("pending", "verified", "rejected", name="verificationstatus"),
            nullable=True,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.UUID(), nullable=True),
        # eca_operator
        sa.Column("employee_code", sa.String(length=50), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=True),
        # asobeum_admin
        sa.Column("association_nit", sa.String(length=50), nullable=True),
        sa.Column("legal_representative", sa.String(length=255), nullable=True),
        sa.Column(
            "coverage_area",
            geoalchemy2.types.Geometry(
                geometry_type="POLYGON",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
            ),
            nullable=True,
        ),
        # b2b_client
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("tax_id", sa.String(length=50), nullable=True),
        sa.Column("commercial_contact", sa.String(length=255), nullable=True),
        sa.Column("rep_goals", sa.JSON(), nullable=True),
        # recycler / eca_operator / asobeum_admin
        sa.Column("association_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["id_type"], ["document_types.code"]),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tax_id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_id_number"), "users", ["id_number"], unique=True)
    op.execute("CREATE INDEX idx_users_coverage_area ON users USING GIST (coverage_area)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_coverage_area")
    op.drop_index(op.f("ix_users_id_number"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_table("document_types")
    op.execute("DROP TYPE IF EXISTS usertype")
    op.execute("DROP TYPE IF EXISTS verificationstatus")
