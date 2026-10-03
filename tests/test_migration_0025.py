"""Migration 0025: the catalog of documents an application is asked for, seeded, and the uploaded files."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

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
    name = f"{BASE_URL.database}_m25_{uuid.uuid4().hex[:8]}"
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


def test_the_known_documents_are_seeded_for_each_type(migrated_db):
    _, eng = migrated_db
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT organization_type::text, code FROM organization_document_types ORDER BY organization_type, sort_order")).all()
    assert [(t, c) for t, c in rows] == [
        ("association", "assoc_rut"), ("association", "assoc_legal_representative_id"),
        ("association", "assoc_legal_personality"), ("eca", "eca_rut"), ("eca", "eca_sspd_habilitation")]


def test_one_file_per_document_per_organization_and_a_valid_status(migrated_db):
    _, eng = migrated_db
    org = uuid.uuid4()
    insert = text(
        "INSERT INTO organization_documents (id, organization_id, document_type_code, storage_key, original_name, "
        "content_type, size_bytes, sha256, status) VALUES (:i, :o, 'eca_rut', 'k', 'n', 'application/pdf', 1, 'h', :s)")
    with eng.connect() as conn:
        conn.execute(text("INSERT INTO organizations (id, type, status, legal_name) VALUES (:i, 'eca', 'draft', 'O')"), {"i": org})
        conn.execute(insert, {"i": uuid.uuid4(), "o": org, "s": "pending"})
        with pytest.raises(IntegrityError):
            conn.execute(insert, {"i": uuid.uuid4(), "o": org, "s": "pending"})
    with eng.connect() as conn:
        other = uuid.uuid4()
        conn.execute(text("INSERT INTO organizations (id, type, status, legal_name) VALUES (:i, 'eca', 'draft', 'O2')"), {"i": other})
        with pytest.raises(IntegrityError):
            conn.execute(insert, {"i": uuid.uuid4(), "o": other, "s": "approved"})


def test_downgrade_removes_them_and_upgrade_brings_the_seed_back(migrated_db):
    name, eng = migrated_db
    assert _alembic(name, "downgrade", "0024").returncode == 0
    with eng.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('organization_documents')")).scalar() is None
        assert conn.execute(text("SELECT to_regclass('organization_document_types')")).scalar() is None
    assert _alembic(name, "upgrade", "head").returncode == 0
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM organization_document_types")).scalar() == 5
