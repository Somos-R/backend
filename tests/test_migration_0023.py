"""Migration 0023: applications to join, and a tax id that is unique only among organizations that operate."""
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


def _drop(name: str) -> None:
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="module")
def template_db(engine):
    name = f"{BASE_URL.database}_mig23_template"
    _drop(name)
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(BASE_URL.set(database=name), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    eng.dispose()
    done = _alembic(name, "upgrade", "0022")
    assert done.returncode == 0, done.stderr
    yield name
    _drop(name)


@pytest.fixture
def old_db(template_db):
    name = f"{BASE_URL.database}_m23_{uuid.uuid4().hex[:8]}"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"'))
    eng = create_engine(BASE_URL.set(database=name))
    yield name, eng
    eng.dispose()
    _drop(name)


def _org(eng, status, tax_id="900111000-1", org_type="association"):
    org_id = uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO organizations (id, type, status, legal_name, tax_id) "
                          "VALUES (:id, :t, :s, 'Org', :n)"), {"id": org_id, "t": org_type, "s": status, "n": tax_id})
    return org_id


def test_before_the_migration_a_tax_id_is_unique_for_every_status(old_db):
    _, eng = old_db
    _org(eng, "approved")
    with pytest.raises(IntegrityError):
        _org(eng, "draft")


def test_after_it_only_operating_organizations_are_unique(old_db):
    name, eng = old_db
    _org(eng, "approved")
    done = _alembic(name, "upgrade", "head")
    assert done.returncode == 0, done.stderr
    _org(eng, "draft")
    _org(eng, "submitted")
    with pytest.raises(IntegrityError):
        _org(eng, "approved")
    with pytest.raises(IntegrityError):
        _org(eng, "suspended")
    _org(eng, "approved", org_type="eca")  # another type may share the number


def test_the_applications_table_is_created_and_one_per_organization(old_db):
    name, eng = old_db
    assert _alembic(name, "upgrade", "head").returncode == 0
    org = _org(eng, "draft")
    insert = text("INSERT INTO organization_applications (id, organization_id, applicant_name, applicant_email, "
                  "consent_version, consent_at) VALUES (:id, :o, 'A', 'a@x.org', 'v1', now())")
    with eng.begin() as conn:
        conn.execute(insert, {"id": uuid.uuid4(), "o": org})
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(insert, {"id": uuid.uuid4(), "o": org})


def test_downgrading_removes_applications_in_progress_and_restores_the_strict_rule(old_db):
    name, eng = old_db
    assert _alembic(name, "upgrade", "head").returncode == 0
    operating = _org(eng, "approved")
    draft = _org(eng, "draft", tax_id="900222000-1")
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO organization_applications (id, organization_id, applicant_name, "
                          "applicant_email, consent_version, consent_at) VALUES (:id, :o, 'A', 'a@x.org', 'v1', now())"),
                     {"id": uuid.uuid4(), "o": draft})
    done = _alembic(name, "downgrade", "0022")
    assert done.returncode == 0, done.stderr
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM organizations WHERE id = :i"), {"i": draft}).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM organizations WHERE id = :i"), {"i": operating}).scalar() == 1
        assert conn.execute(text("SELECT to_regclass('organization_applications')")).scalar() is None
    with pytest.raises(IntegrityError):
        _org(eng, "draft")  # same tax id as the operating one: refused again
