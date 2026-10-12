"""Migration 0027: the stock that already exists enters the ledger as an opening balance, and the ledger is
append-only."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = make_url(os.environ["DATABASE_URL"])


def _admin_engine():
    return create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")


def _alembic(database: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": BASE_URL.set(database=database).render_as_string(hide_password=False)}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)


@pytest.fixture(scope="module")
def template_db(engine):
    name = f"{BASE_URL.database}_mig27_template"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(BASE_URL.set(database=name), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    eng.dispose()
    done = _alembic(name, "upgrade", "0026")
    assert done.returncode == 0, done.stderr
    yield name
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def old_db(template_db):
    name = f"{BASE_URL.database}_m27_{uuid.uuid4().hex[:8]}"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"'))
    eng = create_engine(BASE_URL.set(database=name))
    yield name, eng
    eng.dispose()
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _warehouse(eng):
    warehouse_id = uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO warehouses (id, name, is_active) VALUES (:i, 'B', true)"), {"i": warehouse_id})
    return warehouse_id


def _item(eng, warehouse_id, material, stock):
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO inventory_items (id, material_code, warehouse_id, stock_kg, stock_min_kg, price_per_kg) "
            "VALUES (:i, :m, :w, :s, 50, 450)"),
            {"i": uuid.uuid4(), "m": material, "w": warehouse_id, "s": stock})


def test_existing_stock_becomes_an_opening_movement_and_empty_items_get_none(old_db):
    name, eng = old_db
    warehouse_id = _warehouse(eng)
    _item(eng, warehouse_id, "plastic", "120.50")
    _item(eng, warehouse_id, "glass", "0")
    done = _alembic(name, "upgrade", "head")
    assert done.returncode == 0, done.stderr
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT material_code, movement_type, kg_delta, balance_after_kg, price_per_kg, source_type, actor_id "
            "FROM inventory_movements ORDER BY material_code")).all()
    assert len(rows) == 1  # the empty item (glass) has nothing to open with
    material, movement_type, delta, balance, price, source_type, actor_id = rows[0]
    assert (material, movement_type, source_type, actor_id) == ("plastic", "opening", None, None)
    assert (float(delta), float(balance), float(price)) == (120.50, 120.50, 450.0)


def test_the_ledger_of_every_item_adds_up_to_its_stock_after_the_migration(old_db):
    name, eng = old_db
    warehouse_id = _warehouse(eng)
    for material, stock in (("plastic", "10.25"), ("glass", "7"), ("paper", "0.01")):
        _item(eng, warehouse_id, material, stock)
    assert _alembic(name, "upgrade", "head").returncode == 0
    with eng.connect() as conn:
        mismatches = conn.execute(text(
            "SELECT i.material_code FROM inventory_items i "
            "LEFT JOIN (SELECT material_code, warehouse_id, sum(kg_delta) AS total FROM inventory_movements "
            "           GROUP BY material_code, warehouse_id) m "
            "  ON m.material_code = i.material_code AND m.warehouse_id = i.warehouse_id "
            "WHERE i.stock_kg <> COALESCE(m.total, 0)")).all()
    assert mismatches == []


def test_the_ledger_cannot_be_edited_and_downgrading_drops_it(old_db):
    name, eng = old_db
    warehouse_id = _warehouse(eng)
    _item(eng, warehouse_id, "plastic", "5")
    assert _alembic(name, "upgrade", "head").returncode == 0
    with eng.connect() as conn:
        with pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text("UPDATE inventory_movements SET kg_delta = 1"))
    assert _alembic(name, "downgrade", "0026").returncode == 0
    with eng.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('inventory_movements')")).scalar() is None
    assert _alembic(name, "upgrade", "head").returncode == 0
