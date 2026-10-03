"""Test infrastructure.

- A dedicated `<db>_test` database is dropped/recreated once per session and
  migrated with Alembic, so tests exercise the real schema and seed data.
- Each test runs inside a transaction that is rolled back at the end; the
  application's `db.commit()` calls only release a SAVEPOINT.

The target database comes from TEST_DATABASE_URL, falling back to DATABASE_URL
with `_test` appended to the database name. It never touches the dev database.
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

DEFAULT_URL = "postgresql://postgres:postgres@localhost:5432/somos_r_dev"


def _resolve_test_url() -> str:
    url = make_url(
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or DEFAULT_URL
    )
    if not (url.database or "").endswith("_test"):
        url = url.set(database=f"{url.database}_test")
    return url.render_as_string(hide_password=False)


# Must be set before `app` is imported: Settings() reads these at import time.
TEST_DATABASE_URL = _resolve_test_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production-use-0123456789"
os.environ["EMAIL_BACKEND"] = "memory"
os.environ["RATE_LIMIT_ENABLED"] = "false"
# A valid Fernet key (32 bytes, url-safe base64) so settings built for staging/prod are complete.
os.environ["MFA_ENCRYPTION_KEY"] = "dGVzdC1tZmEta2V5LW5vdC1mb3ItcHJvZHVjdGlvbiE="

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import email as email_module  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from tests import factories  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _recreate_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()

    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    engine.dispose()


@pytest.fixture(scope="session")
def engine():
    _recreate_database(TEST_DATABASE_URL)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")

    eng = create_engine(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db):
    """Unauthenticated client wired to the per-test session."""
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client_as(db):
    """Factory: `client_as(user)` returns a client authenticated as that user."""
    app.dependency_overrides[get_db] = lambda: db

    def _make(user) -> TestClient:
        return TestClient(app, headers=factories.auth_headers(user))

    try:
        yield _make
    finally:
        app.dependency_overrides.clear()


# --- Users ------------------------------------------------------------------

@pytest.fixture
def citizen(db):
    return factories.make_user(db, "citizen")


@pytest.fixture
def eca_admin(db):
    return factories.make_user(db, "eca", role_code="eca_admin")


@pytest.fixture
def association_admin(db):
    return factories.make_user(db, "association", role_code="association_admin")


@pytest.fixture
def recycler(db):
    return factories.make_user(db, "recycler")


# --- Inventory seed (materials and warehouses come from migration 0004) ------

@pytest.fixture
def warehouse(db):
    return factories.first_warehouse(db)


@pytest.fixture(autouse=True)
def outbox():
    """Emails captured by the "memory" backend; emptied around every test."""
    email_module.outbox.clear()
    yield email_module.outbox
    email_module.outbox.clear()


@pytest.fixture(autouse=True)
def _private_storage(tmp_path, monkeypatch):
    """Uploaded documents go to a throwaway directory: tests never write inside the repository."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path / "storage"))


@pytest.fixture
def count_queries(db):
    """Context manager that records the SELECT/INSERT/UPDATE/DELETE statements a block executes."""
    from contextlib import contextmanager

    from sqlalchemy import event

    @contextmanager
    def _count():
        statements: list[str] = []
        connection = db.get_bind()

        def record(conn, cursor, statement, *args):
            if statement.lstrip().split(None, 1)[0].upper() in {"SELECT", "INSERT", "UPDATE", "DELETE"}:
                statements.append(" ".join(statement.split())[:160])

        event.listen(connection, "before_cursor_execute", record)
        try:
            yield statements
        finally:
            event.remove(connection, "before_cursor_execute", record)

    return _count
