"""Migration 0018 on data shaped like the old schema: staff are grouped into organizations.

Runs the real migration (in a subprocess, like an operator would) against throwaway databases
copied from a template that stops at 0017, so the data rules are checked and not just the schema.
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = make_url(os.environ["DATABASE_URL"])
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _admin_engine():
    return create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")


def _alembic(database: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": BASE_URL.set(database=database).render_as_string(hide_password=False)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)


def _drop(name: str) -> None:
    with _admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="module")
def template_db(engine):
    """A database migrated up to 0017, copied for every test."""
    name = f"{BASE_URL.database}_mig_template"
    _drop(name)
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(BASE_URL.set(database=name), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    eng.dispose()
    done = _alembic(name, "upgrade", "0017")
    assert done.returncode == 0, done.stderr
    yield name
    _drop(name)


@pytest.fixture
def old_db(template_db):
    """A fresh copy at 0017: returns (database name, engine)."""
    name = f"{BASE_URL.database}_mig_{uuid.uuid4().hex[:8]}"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"'))
    eng = create_engine(BASE_URL.set(database=name))
    yield name, eng
    eng.dispose()
    _drop(name)


_counter = iter(range(1, 10_000))


def _seed(eng, user_type, role=None, nit=None, rep=None, name=None, minutes=0):
    n = next(_counter)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, full_name, id_type, id_number, user_type_code, "
            "role_code, association_nit, legal_representative, created_at) "
            "VALUES (gen_random_uuid(), :email, 'x', :name, 'CC', :idn, :type, :role, :nit, :rep, :created)"),
            {"email": f"u{n}@old.test", "name": name or f"Persona {n}", "idn": f"100{n}", "type": user_type,
             "role": role, "nit": nit, "rep": rep, "created": _T0 + timedelta(minutes=minutes)})
    return f"u{n}@old.test"


def _upgrade(name):
    done = _alembic(name, "upgrade", "0018")
    assert done.returncode == 0, done.stderr
    return done


def _org_of(eng, email):
    with eng.connect() as conn:
        return conn.execute(text(
            "SELECT o.id, o.type::text, o.status::text, o.legal_name, o.tax_id, o.legal_representative, "
            "o.approved_at IS NOT NULL FROM users u LEFT JOIN organizations o ON o.id = u.organization_id "
            "WHERE u.email = :e"), {"e": email}).one()


def _count_orgs(eng, org_type=None):
    with eng.connect() as conn:
        if org_type:
            return conn.execute(text("SELECT count(*) FROM organizations WHERE type::text = :t"), {"t": org_type}).scalar()
        return conn.execute(text("SELECT count(*) FROM organizations")).scalar()


class TestAssociations:
    def test_staff_are_grouped_by_nit_ignoring_surrounding_spaces(self, old_db):
        name, eng = old_db
        a1 = _seed(eng, "association", "association_admin", nit=" 900111-1 ", rep="Ana Rep")
        a2 = _seed(eng, "association", "association_operator", nit="900111-1", rep="Ana Rep")
        b1 = _seed(eng, "association", "association_admin", nit="900222-2", rep="Beto Rep")
        _upgrade(name)
        org_a1, org_a2, org_b1 = _org_of(eng, a1), _org_of(eng, a2), _org_of(eng, b1)
        assert org_a1[0] == org_a2[0] and org_a1[0] != org_b1[0]
        assert _count_orgs(eng, "association") == 2

    def test_the_organization_takes_the_data_the_staff_repeated(self, old_db):
        name, eng = old_db
        email = _seed(eng, "association", "association_admin", nit=" 900111-1 ", rep="Ana Rep")
        _upgrade(name)
        _, org_type, status, legal_name, tax_id, rep, approved = _org_of(eng, email)
        assert (org_type, status, tax_id, rep, approved) == ("association", "approved", "900111-1", "Ana Rep", True)
        assert legal_name == "Asociación 900111-1"  # a placeholder, to be corrected in the backoffice

    def test_staff_without_a_nit_each_get_their_own_organization(self, old_db):
        name, eng = old_db
        x = _seed(eng, "association", None, nit=None, name="Xiomara Sin Nit")
        y = _seed(eng, "association", None, nit="   ", name="Yolanda Sin Nit")
        _upgrade(name)
        ox, oy = _org_of(eng, x), _org_of(eng, y)
        assert ox[0] is not None and oy[0] is not None and ox[0] != oy[0]
        assert ox[4] is None and ox[3] == "Asociación de Xiomara Sin Nit"


class TestEcas:
    def test_with_a_single_admin_the_rest_of_the_eca_staff_join_it(self, old_db):
        name, eng = old_db
        admin = _seed(eng, "eca", "eca_admin", name="Carla Admin")
        operator = _seed(eng, "eca", "eca_operator", minutes=1)
        roleless = _seed(eng, "eca", None, minutes=2)
        _upgrade(name)
        orgs = {_org_of(eng, e)[0] for e in (admin, operator, roleless)}
        assert len(orgs) == 1 and None not in orgs and _count_orgs(eng, "eca") == 1
        assert _org_of(eng, admin)[3] == "ECA de Carla Admin" and _org_of(eng, admin)[2] == "approved"

    def test_with_several_admins_each_is_an_organization_and_the_rest_are_left_out(self, old_db):
        name, eng = old_db
        a1 = _seed(eng, "eca", "eca_admin", name="Admin Uno")
        a2 = _seed(eng, "eca", "eca_admin", name="Admin Dos", minutes=1)
        operator = _seed(eng, "eca", "eca_operator", minutes=2)
        done = _upgrade(name)
        assert _org_of(eng, a1)[0] != _org_of(eng, a2)[0] and _count_orgs(eng, "eca") == 2
        assert _org_of(eng, operator)[0] is None  # ambiguous: not guessed
        assert "left without an organization" in done.stderr + done.stdout

    def test_without_any_admin_no_organization_is_invented(self, old_db):
        name, eng = old_db
        operator = _seed(eng, "eca", "eca_operator")
        _upgrade(name)
        assert _org_of(eng, operator)[0] is None and _count_orgs(eng) == 0


class TestTheRest:
    def test_other_kinds_of_user_are_untouched(self, old_db):
        name, eng = old_db
        emails = [_seed(eng, t) for t in ("citizen", "recycler", "building", "b2b_client")]
        _seed(eng, "association", "association_admin", nit="900-9")
        _upgrade(name)
        assert all(_org_of(eng, e)[0] is None for e in emails)
        assert _count_orgs(eng) == 1

    def test_an_empty_database_migrates_cleanly(self, old_db):
        name, eng = old_db
        _upgrade(name)
        assert _count_orgs(eng) == 0

    def test_downgrade_removes_the_entity_and_keeps_the_users(self, old_db):
        name, eng = old_db
        email = _seed(eng, "association", "association_admin", nit="900-9")
        _upgrade(name)
        done = _alembic(name, "downgrade", "0017")
        assert done.returncode == 0, done.stderr
        with eng.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('organizations')")).scalar() is None
            assert conn.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": email}).scalar() == 1
            cols = conn.execute(text(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = 'users' "
                "AND column_name = 'organization_id'")).scalar()
            assert cols == 0

    def test_the_migration_can_be_reapplied_after_a_downgrade(self, old_db):
        name, eng = old_db
        _seed(eng, "association", "association_admin", nit="900-9")
        _upgrade(name)
        assert _alembic(name, "downgrade", "0017").returncode == 0
        _upgrade(name)
        assert _count_orgs(eng, "association") == 1
