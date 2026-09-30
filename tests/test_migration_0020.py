"""Migration 0020: recyclers that already exist join the association, when that is unambiguous."""
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
    name = f"{BASE_URL.database}_mig20_template"
    _drop(name)
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(BASE_URL.set(database=name), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    eng.dispose()
    done = _alembic(name, "upgrade", "0019")
    assert done.returncode == 0, done.stderr
    yield name
    _drop(name)


@pytest.fixture
def old_db(template_db):
    name = f"{BASE_URL.database}_m20_{uuid.uuid4().hex[:8]}"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"'))
    eng = create_engine(BASE_URL.set(database=name))
    yield name, eng
    eng.dispose()
    _drop(name)


_counter = iter(range(1, 10_000))


def _association(eng, status="approved"):
    org_id = uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO organizations (id, type, status, legal_name) VALUES (:id, 'association', :s, 'Asociación')"),
            {"id": org_id, "s": status})
    return org_id


def _recycler(eng, organization_id=None):
    n = next(_counter)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, full_name, id_type, id_number, user_type_code, organization_id) "
            "VALUES (gen_random_uuid(), :e, '', 'Reci', 'CC', :i, 'recycler', :o)"),
            {"e": f"r{n}@old.test", "i": f"200{n}", "o": organization_id})
    return f"r{n}@old.test"


def _org_of(eng, email):
    with eng.connect() as conn:
        return conn.execute(text("SELECT organization_id FROM users WHERE email = :e"), {"e": email}).scalar()


class TestAssigningExistingRecyclers:
    def test_with_a_single_approved_association_the_recyclers_join_it(self, old_db):
        name, eng = old_db
        assoc = _association(eng)
        _association(eng, status="draft")  # not approved: does not count
        a, b = _recycler(eng), _recycler(eng)
        assert _alembic(name, "upgrade", "0020").returncode == 0
        assert _org_of(eng, a) == assoc and _org_of(eng, b) == assoc

    def test_a_recycler_that_already_has_one_is_left_alone(self, old_db):
        name, eng = old_db
        _association(eng)
        theirs = _association(eng, status="submitted")
        kept = _recycler(eng, theirs)
        assert _alembic(name, "upgrade", "0020").returncode == 0
        assert _org_of(eng, kept) == theirs

    def test_with_several_associations_nothing_is_guessed_and_it_warns(self, old_db):
        name, eng = old_db
        _association(eng)
        _association(eng)
        orphan = _recycler(eng)
        done = _alembic(name, "upgrade", "0020")
        assert done.returncode == 0 and _org_of(eng, orphan) is None
        assert "left without an association" in done.stderr + done.stdout

    def test_with_no_association_they_stay_without(self, old_db):
        name, eng = old_db
        orphan = _recycler(eng)
        assert _alembic(name, "upgrade", "0020").returncode == 0 and _org_of(eng, orphan) is None

    def test_an_empty_database_migrates_and_reverses(self, old_db):
        name, eng = old_db
        assert _alembic(name, "upgrade", "0020").returncode == 0
        assert _alembic(name, "downgrade", "0019").returncode == 0
