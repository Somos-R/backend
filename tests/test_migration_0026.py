"""Migration 0026: reviews keep which documents they sent back, and the table stays append-only."""
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


@pytest.fixture
def migrated_db(engine):
    name = f"{BASE_URL.database}_m26_{uuid.uuid4().hex[:8]}"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(BASE_URL.set(database=name), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    done = _alembic(name, "upgrade", "head")
    assert done.returncode == 0, done.stderr
    yield name, eng
    eng.dispose()
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _has_details(eng) -> bool:
    with eng.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'organization_reviews' AND column_name = 'details'")).scalar() == 1


def test_a_review_stores_the_documents_it_sent_back_and_cannot_be_edited(migrated_db):
    _, eng = migrated_db
    org, user, review = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with eng.connect() as conn:
        conn.execute(text("INSERT INTO organizations (id, type, status, legal_name) VALUES (:i, 'eca', 'submitted', 'O')"), {"i": org})
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, full_name, id_type, id_number, user_type_code) "
            "VALUES (:i, 'r@x.test', '', 'R', 'CC', '888', 'platform')"), {"i": user})
        conn.execute(text(
            "INSERT INTO organization_reviews (id, organization_id, reviewer_id, decision, submission_number, details) "
            "VALUES (:i, :o, :u, 'changes_requested', 1, CAST(:d AS jsonb))"),
            {"i": review, "o": org, "u": user, "d": '[{"code": "eca_rut", "status": "missing"}]'})
        assert conn.execute(text("SELECT details->0->>'code' FROM organization_reviews")).scalar() == "eca_rut"
        with pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text("UPDATE organization_reviews SET details = NULL WHERE id = :i"), {"i": review})


def test_downgrade_drops_the_column_and_upgrade_restores_it(migrated_db):
    name, eng = migrated_db
    assert _has_details(eng)
    assert _alembic(name, "downgrade", "0025").returncode == 0
    assert not _has_details(eng)
    assert _alembic(name, "upgrade", "head").returncode == 0
    assert _has_details(eng)
