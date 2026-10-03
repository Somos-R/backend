"""Migration 0024: the applicant's own data, the reviewer and the append-only history of reviews."""
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
    name = f"{BASE_URL.database}_m24_{uuid.uuid4().hex[:8]}"
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


def _columns(eng, table):
    with eng.connect() as conn:
        return {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table})}


def test_upgrade_adds_the_columns_and_the_reviews_table(migrated_db):
    _, eng = migrated_db
    assert {"applicant_id_type", "applicant_id_number", "applicant_phone", "reviewer_id",
            "review_started_at"} <= _columns(eng, "organization_applications")
    assert {"decision", "summary", "submission_number", "reviewer_id"} <= _columns(eng, "organization_reviews")


def test_a_review_decision_must_be_one_of_the_three(migrated_db):
    _, eng = migrated_db
    org, user = uuid.uuid4(), uuid.uuid4()
    with eng.connect() as conn:
        conn.execute(text("INSERT INTO organizations (id, type, status, legal_name) VALUES (:i, 'eca', 'submitted', 'O')"), {"i": org})
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, full_name, id_type, id_number, user_type_code) "
            "VALUES (:i, 'r@x.test', '', 'R', 'CC', '777', 'platform')"), {"i": user})
        with pytest.raises(DBAPIError):
            conn.execute(text("INSERT INTO organization_reviews (id, organization_id, reviewer_id, decision, submission_number) "
                              "VALUES (:i, :o, :u, 'maybe', 1)"), {"i": uuid.uuid4(), "o": org, "u": user})


def test_downgrade_removes_them_and_upgrade_brings_them_back(migrated_db):
    name, eng = migrated_db
    assert _alembic(name, "downgrade", "0023").returncode == 0
    assert "applicant_id_number" not in _columns(eng, "organization_applications")
    assert _columns(eng, "organization_reviews") == set()
    done = _alembic(name, "upgrade", "head")
    assert done.returncode == 0, done.stderr
    assert "applicant_id_number" in _columns(eng, "organization_applications")
