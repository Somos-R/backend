"""Migration 0021: existing warehouses get an owner ECA when that is unambiguous."""
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


def _drop(name: str) -> None:
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="module")
def template_db(engine):
    name = f"{BASE_URL.database}_mig21_template"
    _drop(name)
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(BASE_URL.set(database=name), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    eng.dispose()
    done = _alembic(name, "upgrade", "0020")
    assert done.returncode == 0, done.stderr
    yield name
    _drop(name)


@pytest.fixture
def old_db(template_db):
    name = f"{BASE_URL.database}_m21_{uuid.uuid4().hex[:8]}"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"'))
    eng = create_engine(BASE_URL.set(database=name))
    yield name, eng
    eng.dispose()
    _drop(name)


def _eca(eng, status="approved"):
    org_id = uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO organizations (id, type, status, legal_name) VALUES (:id, 'eca', :s, 'ECA')"),
                     {"id": org_id, "s": status})
    return org_id


def _owners(eng):
    with eng.connect() as conn:
        return {row[0] for row in conn.execute(text("SELECT organization_id FROM warehouses"))}


def _count(eng):
    with eng.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM warehouses")).scalar()


class TestOwningExistingWarehouses:
    def test_with_a_single_approved_eca_every_warehouse_becomes_its_own(self, old_db):
        name, eng = old_db
        owner = _eca(eng)
        _eca(eng, status="submitted")  # not approved: does not count
        assert _count(eng) == 3  # the seeded ones
        assert _alembic(name, "upgrade", "0021").returncode == 0
        assert _owners(eng) == {owner}

    def test_with_several_ecas_nothing_is_guessed_and_it_warns(self, old_db):
        name, eng = old_db
        _eca(eng)
        _eca(eng)
        done = _alembic(name, "upgrade", "0021")
        assert done.returncode == 0 and _owners(eng) == {None}
        assert "left without an owner" in done.stderr + done.stdout

    def test_with_no_eca_they_stay_without_an_owner(self, old_db):
        name, eng = old_db
        assert _alembic(name, "upgrade", "0021").returncode == 0 and _owners(eng) == {None}

    def test_it_reverses_and_can_be_applied_again(self, old_db):
        name, eng = old_db
        owner = _eca(eng)
        assert _alembic(name, "upgrade", "0021").returncode == 0
        assert _alembic(name, "downgrade", "0020").returncode == 0
        assert _alembic(name, "upgrade", "0021").returncode == 0
        assert _owners(eng) == {owner}
