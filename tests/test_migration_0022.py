"""Migration 0022: existing weighings are classified by how the seller related to the ECA."""
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
    name = f"{BASE_URL.database}_mig22_template"
    _drop(name)
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(BASE_URL.set(database=name), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    eng.dispose()
    done = _alembic(name, "upgrade", "0021")
    assert done.returncode == 0, done.stderr
    yield name
    _drop(name)


@pytest.fixture
def old_db(template_db):
    name = f"{BASE_URL.database}_m22_{uuid.uuid4().hex[:8]}"
    with _admin_engine().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"'))
    eng = create_engine(BASE_URL.set(database=name))
    yield name, eng
    eng.dispose()
    _drop(name)


_n = iter(range(1, 100_000))


def _org(eng, org_type):
    org_id = uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO organizations (id, type, status, legal_name) "
                          "VALUES (:id, :t, 'approved', 'Org')"), {"id": org_id, "t": org_type})
    return org_id


def _recycler(eng, organization_id):
    n = next(_n)
    user_id = uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, full_name, id_type, id_number, user_type_code, organization_id) "
            "VALUES (:id, :e, '', 'Reci', 'CC', :i, 'recycler', :o)"),
            {"id": user_id, "e": f"r{n}@old.test", "i": f"300{n}", "o": organization_id})
    return user_id


def _warehouse_of(eng, eca_id):
    with eng.begin() as conn:
        conn.execute(text("UPDATE warehouses SET organization_id = :o WHERE id = (SELECT id FROM warehouses LIMIT 1)"),
                     {"o": eca_id})
        return conn.execute(text("SELECT id FROM warehouses WHERE organization_id = :o"), {"o": eca_id}).scalar()


def _weighing(eng, recycler_id, warehouse_id):
    wid = uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO weighings (id, recycler_id, material_code, warehouse_id, kg, price_per_kg, status) "
            "VALUES (:id, :r, 'plastic', :w, 10, 100, 'pending_validation')"), {"id": wid, "r": recycler_id, "w": warehouse_id})
    return wid


def _affiliation(eng, weighing_id):
    with eng.connect() as conn:
        return conn.execute(text("SELECT affiliation_status::text FROM weighings WHERE id = :i"), {"i": weighing_id}).scalar()


class TestClassifyingExistingWeighings:
    def test_each_one_by_how_the_recycler_related_to_the_eca(self, old_db):
        name, eng = old_db
        eca, linked_assoc, other_assoc = _org(eng, "eca"), _org(eng, "association"), _org(eng, "association")
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO eca_association_links (id, eca_id, association_id, status) "
                              "VALUES (gen_random_uuid(), :e, :a, 'active')"), {"e": eca, "a": linked_assoc})
        wh = _warehouse_of(eng, eca)
        linked = _weighing(eng, _recycler(eng, linked_assoc), wh)
        unlinked = _weighing(eng, _recycler(eng, other_assoc), wh)
        loose = _weighing(eng, _recycler(eng, None), wh)
        assert _alembic(name, "upgrade", "0022").returncode == 0
        assert _affiliation(eng, linked) == "linked"
        assert _affiliation(eng, unlinked) == "unlinked_association"
        assert _affiliation(eng, loose) == "independent"

    def test_a_link_that_was_not_active_does_not_count(self, old_db):
        name, eng = old_db
        eca, assoc = _org(eng, "eca"), _org(eng, "association")
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO eca_association_links (id, eca_id, association_id, status) "
                              "VALUES (gen_random_uuid(), :e, :a, 'removed')"), {"e": eca, "a": assoc})
        wid = _weighing(eng, _recycler(eng, assoc), _warehouse_of(eng, eca))
        assert _alembic(name, "upgrade", "0022").returncode == 0
        assert _affiliation(eng, wid) == "unlinked_association"

    def test_the_schema_now_allows_a_seller_who_is_not_registered_and_requires_someone(self, old_db):
        name, eng = old_db
        assert _alembic(name, "upgrade", "0022").returncode == 0
        with eng.begin() as conn:
            wh = conn.execute(text("SELECT id FROM warehouses LIMIT 1")).scalar()
            conn.execute(text(
                "INSERT INTO weighings (id, recycler_id, seller_name, seller_id_type, seller_id_number, "
                "affiliation_status, material_code, warehouse_id, kg, price_per_kg, status) "
                "VALUES (gen_random_uuid(), NULL, 'Ana', 'CC', '5212', 'independent', 'plastic', :w, 1, 1, "
                "'pending_validation')"), {"w": wh})
        with pytest.raises(Exception, match="ck_weighings_has_seller"):
            with eng.begin() as conn:
                conn.execute(text(
                    "INSERT INTO weighings (id, affiliation_status, material_code, warehouse_id, kg, price_per_kg, status) "
                    "VALUES (gen_random_uuid(), 'independent', 'plastic', :w, 1, 1, 'pending_validation')"), {"w": wh})

    def test_reversing_drops_the_weighings_of_people_who_are_not_registered_and_keeps_the_rest(self, old_db):
        name, eng = old_db
        eca = _org(eng, "eca")
        wh = _warehouse_of(eng, eca)
        keep = _weighing(eng, _recycler(eng, None), wh)
        assert _alembic(name, "upgrade", "0022").returncode == 0
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO weighings (id, seller_name, seller_id_type, seller_id_number, affiliation_status, "
                "material_code, warehouse_id, kg, price_per_kg, status) VALUES (gen_random_uuid(), 'Ana', 'CC', "
                "'5212', 'independent', 'plastic', :w, 1, 1, 'pending_validation')"), {"w": wh})
        assert _alembic(name, "downgrade", "0021").returncode == 0
        with eng.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM weighings")).scalar() == 1
            assert conn.execute(text("SELECT id FROM weighings")).scalar() == keep

    def test_an_empty_database_migrates_and_reverses(self, old_db):
        name, _ = old_db
        assert _alembic(name, "upgrade", "0022").returncode == 0
        assert _alembic(name, "downgrade", "0021").returncode == 0
