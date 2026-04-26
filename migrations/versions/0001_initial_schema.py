"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-24

"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- document_types ---
    document_types = op.create_table(
        "document_types",
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.bulk_insert(document_types, [
        {"code": "CC",  "label": "Cédula de Ciudadanía",               "is_active": True},
        {"code": "CE",  "label": "Cédula de Extranjería",               "is_active": True},
        {"code": "NIT", "label": "Número de Identificación Tributaria", "is_active": True},
        {"code": "PA",  "label": "Pasaporte",                           "is_active": True},
        {"code": "PPT", "label": "Permiso de Protección Temporal",      "is_active": True},
    ])

    # --- user_types ---
    user_types = op.create_table(
        "user_types",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.bulk_insert(user_types, [
        {"code": "citizen",     "label": "Ciudadano",                    "is_active": True},
        {"code": "building",    "label": "Administrador de Conjunto",    "is_active": True},
        {"code": "recycler",    "label": "Reciclador",                   "is_active": True},
        {"code": "eca",         "label": "Operador ECA",                 "is_active": True},
        {"code": "association", "label": "Administrador ASOBEUM",        "is_active": True},
        {"code": "b2b_client",  "label": "Cliente B2B",                  "is_active": True},
    ])

    # --- roles ---
    roles = op.create_table(
        "roles",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.bulk_insert(roles, [
        {"code": "eca_admin",        "label": "Administrador ECA",        "is_active": True},
        {"code": "eca_operator",     "label": "Operador ECA",             "is_active": True},
        {"code": "association_admin","label": "Administrador Asociación",  "is_active": True},
    ])

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("id_type", sa.String(10), nullable=False),
        sa.Column("id_number", sa.String(20), nullable=False),
        sa.Column("user_type_code", sa.String(20), nullable=False),
        sa.Column("role_code", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # citizen / building
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        # building
        sa.Column("building_name", sa.String(255), nullable=True),
        sa.Column("num_units", sa.Integer(), nullable=True),
        sa.Column("representation_document", sa.Text(), nullable=True),
        # recycler
        sa.Column("profile_picture", sa.Text(), nullable=True),
        sa.Column("id_picture", sa.Text(), nullable=True),
        sa.Column("verification_status", sa.Enum("pending", "verified", "rejected", name="verificationstatus"), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.UUID(), nullable=True),
        # eca
        sa.Column("employee_code", sa.String(50), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=True),
        # association
        sa.Column("association_nit", sa.String(50), nullable=True),
        sa.Column("legal_representative", sa.String(255), nullable=True),
        sa.Column("coverage_area", geoalchemy2.types.Geometry(
            geometry_type="POLYGON", srid=4326, dimension=2,
            spatial_index=False, from_text="ST_GeomFromEWKT", name="geometry",
        ), nullable=True),
        # b2b_client
        sa.Column("company_name", sa.String(255), nullable=True),
        sa.Column("tax_id", sa.String(50), nullable=True),
        sa.Column("commercial_contact", sa.String(255), nullable=True),
        sa.Column("rep_goals", sa.JSON(), nullable=True),
        # recycler / eca / association
        sa.Column("association_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["id_type"], ["document_types.code"]),
        sa.ForeignKeyConstraint(["user_type_code"], ["user_types.code"]),
        sa.ForeignKeyConstraint(["role_code"], ["roles.code"]),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tax_id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_id_number", "users", ["id_number"], unique=True)
    op.execute("CREATE INDEX idx_users_coverage_area ON users USING GIST (coverage_area)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_coverage_area")
    op.drop_index("ix_users_id_number", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS verificationstatus")
    op.drop_table("roles")
    op.drop_table("user_types")
    op.drop_table("document_types")
