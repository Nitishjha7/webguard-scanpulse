"""Shared test fixtures.

Tests run against a real Postgres database (``webguard_test``), not SQLite —
see the note on :class:`TestingConfig`. Tables are created once per session and
truncated between tests.

Truncation rather than a per-test transaction rollback is deliberate: the
incident state machine calls ``session.rollback()`` itself when it loses an
insert race, which would silently discard an enclosing test transaction and
make the failure look like a passing test.
"""
import os
import uuid

import pytest
import sqlalchemy as sa

os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app  # noqa: E402
from app.extensions import db as _db  # noqa: E402
from app.models import Monitor, Organization, User, UserRole  # noqa: E402


@pytest.fixture(scope="session")
def app():
    application = create_app("testing")
    with application.app_context():
        _ensure_database(application)
        _db.drop_all()
        _db.create_all()
        # create_all() builds ping_logs as a partitioned parent with no
        # partitions, and a parent with no matching partition rejects every
        # insert. Production gets these from a beat task; tests need them now.
        from app.services import partitions

        partitions.ensure_partitions()
        partitions.ensure_default_partition()
        _db.session.commit()

        yield application
        _db.session.remove()


def _ensure_database(application) -> None:
    """Create the test database if it does not exist yet."""
    url = sa.engine.make_url(application.config["SQLALCHEMY_DATABASE_URI"])
    admin_url = url.set(database="postgres")
    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{url.database}"'))
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(app):
    """Wipe every table after each test so cases cannot leak into each other.

    DELETE rather than TRUNCATE. The tables hold a handful of rows each, and
    TRUNCATE takes an ACCESS EXCLUSIVE lock and rewrites files — across a few
    hundred tests that alone cost minutes. Deleting in reverse dependency order
    keeps the foreign keys happy without needing CASCADE.
    """
    yield
    _db.session.rollback()
    statements = ";".join(
        f'DELETE FROM "{t.name}"' for t in reversed(_db.metadata.sorted_tables)
    )
    _db.session.connection().exec_driver_sql(statements)
    _db.session.commit()
    _db.session.remove()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def client(app):
    return app.test_client()


# --- Domain factories ------------------------------------------------------


@pytest.fixture
def make_org(db):
    def _make(name: str = "Acme Corp") -> Organization:
        org = Organization(name=name, slug=f"{Organization.slugify(name)}-{uuid.uuid4().hex[:6]}")
        db.session.add(org)
        db.session.commit()
        return org

    return _make


@pytest.fixture
def make_user(db):
    def _make(org, email: str | None = None, role: UserRole = UserRole.ADMIN, password="password123"):
        user = User(
            organization=org,
            email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
            role=role,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    return _make


@pytest.fixture
def make_monitor(db):
    def _make(org, url: str | None = None, **kwargs):
        monitor = Monitor(
            org_id=org.id,
            name=kwargs.pop("name", "Test monitor"),
            url=url or f"https://{uuid.uuid4().hex[:8]}.example.com",
            interval_seconds=kwargs.pop("interval_seconds", 300),
            timeout_seconds=kwargs.pop("timeout_seconds", 10),
            failure_threshold=kwargs.pop("failure_threshold", 2),
            **kwargs,
        )
        db.session.add(monitor)
        db.session.commit()
        return monitor

    return _make


@pytest.fixture
def auth_headers(client):
    """Register a fresh tenant through the API and return its bearer headers."""

    def _make(email: str | None = None, org_name: str = "Test Org"):
        email = email or f"owner-{uuid.uuid4().hex[:8]}@example.com"
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "password123", "org_name": org_name},
        )
        assert resp.status_code == 201, resp.get_json()
        body = resp.get_json()
        return {"Authorization": f"Bearer {body['access_token']}"}, body

    return _make


# --- Probe result helpers --------------------------------------------------


def probe_up(latency_ms: float = 120.0, region: str = "default") -> dict:
    return {
        "region": region,
        "status_code": 200,
        "latency_ms": latency_ms,
        "is_up": True,
        "error": None,
    }


def probe_down(error: str = "Connection refused", region: str = "default") -> dict:
    return {
        "region": region,
        "status_code": None,
        "latency_ms": None,
        "is_up": False,
        "error": error,
    }
