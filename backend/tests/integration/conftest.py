"""Postgres fixtures for the mail-client integration tests.

These tests need real Postgres because the behaviour under test *is* Postgres
behaviour: the row-level-security policies that enforce per-user isolation, the
generated ``search_tsv`` column, the partial index behind the unread counts, and
the keyset pagination that keeps a 5,000-message folder from sequential-scanning.
A SQLite stand-in would pass none of that.

Set ``TEST_DATABASE_URL`` to run them, matching the convention already used by
``test_fts_postgres.py``. The suite migrates the database once at session scope
and truncates between tests, so it is safe to point at a scratch database.
"""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# The application builds its engine from DATABASE_URL at import time and caches
# Settings, so both must be pointed at the integration database before any
# ``app.*`` module is imported. Test modules import ``app.main``; this conftest
# is loaded first.
os.environ["DATABASE_URL"] = DATABASE_URL or os.environ.get("DATABASE_URL", "sqlite://")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SESSION_SECRET", "integration-session-secret-0123456789abcdef")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "integration-token-key-0123456789abcdef")
os.environ.setdefault("MS_CLIENT_ID", "integration-client-id")
os.environ.setdefault("MS_CLIENT_SECRET", "integration-client-secret")
os.environ.setdefault("MS_TENANT", "organizations")

from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TEST_DATABASE_URL is not set; skipping Postgres integration tests",
)

# Child tables first so foreign keys never block a truncate.
TABLES_IN_DELETE_ORDER = [
    "email_chunks",
    "attachments",
    "commitments",
    "search_result_events",
    "search_feedback",
    "saved_searches",
    "pinned_emails",
    "thread_summary_cache",
    "ask_history",
    "indexing_jobs",
    "search_sessions",
    "claude_daily_usage",
    "emails",
    "mailbox_connections",
    "sessions",
    "oauth_states",
    "users",
]


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(config, "head")
    yield create_engine(DATABASE_URL, pool_pre_ping=True)


@pytest.fixture(autouse=True)
def _clean_database(request: pytest.FixtureRequest) -> Iterator[None]:
    """Truncate between tests, but only for tests that actually use the database."""
    if "engine" not in request.fixturenames and "db" not in request.fixturenames:
        yield
        return
    database = request.getfixturevalue("engine")
    with database.connect() as connection:
        for table in TABLES_IN_DELETE_ORDER:
            connection.execute(text(f"DELETE FROM {table}"))
        connection.commit()
    yield


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()