"""Migration 0037: the history of pickup request rejections starts with every request rejected today. Reversible."""
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


@pytest.fixture
def old_db(engine):
    name = f"{BASE_URL.database}_m37_{uuid.uuid4().hex[:8]}"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(BASE_URL.set(database=name))
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    done = _alembic(name, "upgrade", "0036")
    assert done.returncode == 0, done.stderr
    yield name, eng
    eng.dispose()
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _pickup_requests(eng) -> tuple[uuid.UUID, uuid.UUID]:
    """One request rejected (with no `rejected_at`, as one changed by hand could be) and one still pending."""
    user_id, rejected_id, pending_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, full_name, id_type, id_number, user_type_code) "
            "VALUES (:i, 'mig37@test.com', 'x', 'Migración', 'CC', '37003700', 'citizen')"), {"i": user_id})
        for collection_id, state, reason in ((rejected_id, "rejected", "incomplete_address"),
                                             (pending_id, "pending_assignment", None)):
            conn.execute(text(
                "INSERT INTO collections (id, requester_id, status, rejection_reason_code, address, latitude, "
                "longitude, location, materials) VALUES (:i, :u, :s, :r, 'Usme', 4.4766, -74.1262, "
                "ST_SetSRID(ST_MakePoint(-74.1262, 4.4766), 4326)::geography, '[\"paper\"]')"),
                {"i": collection_id, "u": user_id, "s": state, "r": reason})
    return rejected_id, pending_id


def test_every_rejected_request_gets_its_row_in_the_history(old_db):
    name, eng = old_db
    rejected_id, pending_id = _pickup_requests(eng)
    done = _alembic(name, "upgrade", "head")
    assert done.returncode == 0, done.stderr
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT r.collection_id, r.reason_code, r.rejected_at = c.updated_at, r.resubmitted_at "
            "FROM collection_rejections r JOIN collections c ON c.id = r.collection_id")).all()
    assert rows == [(rejected_id, "incomplete_address", True, None)]


def test_downgrade_drops_the_history_and_the_migration_can_be_reapplied(old_db):
    name, eng = old_db
    _pickup_requests(eng)
    assert _alembic(name, "upgrade", "head").returncode == 0
    done = _alembic(name, "downgrade", "0036")
    assert done.returncode == 0, done.stderr
    with eng.connect() as conn:
        assert conn.scalar(text("SELECT to_regclass('collection_rejections')")) is None
    assert _alembic(name, "upgrade", "head").returncode == 0
