"""english naming: columns, enum values, indexes and material codes

Renames the Spanish identifiers of the weighings, transactions and inventory domains, and the
Spanish material codes, so the whole API contract is in English. No data is lost: ALTER ... RENAME
keeps every row; material codes are moved by inserting the new code, repointing the references and
deleting the old code (so the foreign keys never see an invalid value).

Rows already written to audit_log are append-only and keep their old values in `details`.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (enum type, old value, new value)
ENUM_VALUES = [
    ("weighingstatus", "pendiente", "pending_validation"),
    ("weighingstatus", "validado", "validated"),
    ("weighingstatus", "rechazado", "rejected"),
    ("weighingstatus", "pagado", "paid"),
    ("transactiontype", "compra", "purchase"),
    ("transactiontype", "venta", "sale"),
    ("transactionstatus", "pendiente", "pending"),
    ("transactionstatus", "pagado", "paid"),
    ("transactionstatus", "cancelado", "cancelled"),
    ("transactionstatus", "entregado", "delivered"),
]

# (table, old column, new column)
COLUMNS = [
    ("weighings", "estado", "status"),
    ("weighings", "precio_kg", "price_per_kg"),
    ("weighings", "fecha", "occurred_at"),
    ("transactions", "precio_kg", "price_per_kg"),
    ("transactions", "fecha", "occurred_at"),
    ("inventory_items", "precio_kg", "price_per_kg"),
    ("inventory_items", "fecha_actualizacion", "updated_at"),
]

# (old name, new name)
INDEXES = [
    ("ix_weighings_recycler_estado_fecha", "ix_weighings_recycler_status_occurred"),
    ("idx_weighings_estado", "idx_weighings_status"),
    ("idx_weighings_fecha", "idx_weighings_occurred_at"),
    ("ix_transactions_type_status_fecha", "ix_transactions_type_status_occurred"),
    ("ix_transactions_fecha", "ix_transactions_occurred_at"),
]

# (table, old constraint, new constraint)
CONSTRAINTS = [
    ("inventory_items", "ck_inventory_price_non_negative", "ck_inventory_price_per_kg_non_negative"),
    ("weighings", "ck_weighings_price_positive", "ck_weighings_price_per_kg_positive"),
    ("transactions", "ck_transactions_price_positive", "ck_transactions_price_per_kg_positive"),
]

MATERIAL_CODES = [
    ("papel", "paper"),
    ("plastico", "plastic"),
    ("vidrio", "glass"),
    ("carton", "cardboard"),
    ("electronico", "electronic"),
    ("organico", "organic"),
]
MATERIAL_REFERENCES = ["inventory_items", "weighings", "transactions"]


def _rename_enum_values(pairs) -> None:
    for enum_type, old, new in pairs:
        op.execute(f"ALTER TYPE {enum_type} RENAME VALUE '{old}' TO '{new}'")


def _rename_columns(pairs) -> None:
    for table, old, new in pairs:
        op.alter_column(table, old, new_column_name=new)


def _rename_indexes(pairs) -> None:
    for old, new in pairs:
        op.execute(f"ALTER INDEX IF EXISTS {old} RENAME TO {new}")


def _rename_constraints(triples) -> None:
    for table, old, new in triples:
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}")


def _move_material_codes(pairs) -> None:
    for old, new in pairs:
        op.execute(f"""
            INSERT INTO materials (code, label, unit, is_active)
            SELECT '{new}', label, unit, is_active FROM materials WHERE code = '{old}'
            ON CONFLICT (code) DO NOTHING
        """)
        for table in MATERIAL_REFERENCES:
            op.execute(f"UPDATE {table} SET material_code = '{new}' WHERE material_code = '{old}'")
        op.execute(f"DELETE FROM materials WHERE code = '{old}'")


def upgrade() -> None:
    _rename_enum_values(ENUM_VALUES)
    _rename_columns(COLUMNS)
    _rename_indexes(INDEXES)
    _rename_constraints(CONSTRAINTS)
    _move_material_codes(MATERIAL_CODES)


def downgrade() -> None:
    _move_material_codes([(new, old) for old, new in MATERIAL_CODES])
    _rename_constraints([(t, new, old) for t, old, new in CONSTRAINTS])
    _rename_indexes([(new, old) for old, new in INDEXES])
    _rename_columns([(t, new, old) for t, old, new in COLUMNS])
    _rename_enum_values([(e, new, old) for e, old, new in ENUM_VALUES])
