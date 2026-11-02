"""Migration 0032: pickup request points become geography without moving, and recyclers and ECAs get an indexed
point of their own. Reversible."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

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
    name = f"{BASE_URL.database}_mig32_template"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(BASE_URL.set(database=name), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    eng.dispose()
    done = _alembic(name, "upgrade", "0031")
    assert done.returncode == 0, done.stderr
    yield name
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def old_db(template_db):
    name = f"{BASE_URL.database}_m32_{uuid.uuid4().hex[:8]}"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"'))
    eng = create_engine(BASE_URL.set(database=name))
    yield name, eng
    eng.dispose()
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _pickup_request(eng) -> uuid.UUID:
    user_id, collection_id = uuid.uuid4(), uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, full_name, id_type, id_number, user_type_code) "
            "VALUES (:i, 'mig32@test.com', 'x', 'Migración', 'CC', '32003200', 'citizen')"), {"i": user_id})
        conn.execute(text(
            "INSERT INTO collections (id, requester_id, status, address, latitude, longitude, location, materials) "
            "VALUES (:i, :u, 'pending_assignment', 'Usme', 4.4766, -74.1262, "
            "ST_SetSRID(ST_MakePoint(-74.1262, 4.4766), 4326), '[\"paper\"]')"),
            {"i": collection_id, "u": user_id})
    return collection_id


def _location(eng, collection_id) -> tuple[str, float, float]:
    with eng.connect() as conn:
        return tuple(conn.execute(text(
            "SELECT pg_typeof(location)::text, ST_X(location::geometry), ST_Y(location::geometry) "
            "FROM collections WHERE id = :i"), {"i": collection_id}).one())


def _columns(eng, table: str) -> set[str]:
    with eng.connect() as conn:
        return set(conn.scalars(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table}))


def test_existing_pickup_points_become_geography_in_place(old_db):
    name, eng = old_db
    collection_id = _pickup_request(eng)
    done = _alembic(name, "upgrade", "head")
    assert done.returncode == 0, done.stderr
    assert _location(eng, collection_id) == pytest.approx(("geography", -74.1262, 4.4766))
    assert "start_location" in _columns(eng, "users") and "location" in _columns(eng, "organizations")


def test_downgrade_restores_geometry_and_the_migration_can_be_reapplied(old_db):
    name, eng = old_db
    collection_id = _pickup_request(eng)
    assert _alembic(name, "upgrade", "head").returncode == 0
    done = _alembic(name, "downgrade", "0031")
    assert done.returncode == 0, done.stderr
    assert _location(eng, collection_id) == pytest.approx(("geometry", -74.1262, 4.4766))
    assert "start_location" not in _columns(eng, "users") and "location" not in _columns(eng, "organizations")
    assert _alembic(name, "upgrade", "head").returncode == 0
