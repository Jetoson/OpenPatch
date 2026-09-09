"""Server suite setup.
"""

import os
import sys
import tempfile

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER = os.path.join(ROOT, "server")

if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

import env_file  # noqa: E402

env_file.load = lambda: False

_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="openpatch-tests-"), "test.db")

ADMIN_KEY = "test-admin-key"
ADMIN_HEADERS = {"X-Admin-Key": ADMIN_KEY}

DASHBOARD_PASSWORD = "test-dashboard-password"
ENROLLMENT_SECRET = "test-enrollment-secret"
TASK_SIGNING_SECRET = "test-task-signing-secret"

os.environ["OPENPATCH_DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["OPENPATCH_ADMIN_API_KEY"] = ADMIN_KEY
os.environ["OPENPATCH_DASHBOARD_PASSWORD"] = DASHBOARD_PASSWORD
os.environ["OPENPATCH_ENROLLMENT_SECRET"] = ENROLLMENT_SECRET
os.environ["OPENPATCH_TASK_SIGNING_SECRET"] = TASK_SIGNING_SECRET
os.environ.pop("OPENPATCH_ENROLLMENT_OPEN", None)
os.environ.pop("OPENPATCH_DASHBOARD_USERNAME", None)
os.environ.pop("OPENPATCH_DASHBOARD_PASSWORD_FILE", None)
os.environ["OPENPATCH_ENTRYPOINT"] = "pytest"
os.environ["OPENPATCH_SCAN_ENABLED"] = "0"
os.environ["OPENPATCH_MAINTENANCE_INTERVAL"] = "0"
os.environ.pop("OPENPATCH_NVD_API_KEY", None)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Build the schema by running the migrations, once.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(SERVER, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(SERVER, "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _clean_state():
    """Empty every table and drop every in-process cache between tests.
    """
    from database import Base, engine

    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())

    import api.routers.dashboard as dashboard
    import api.services.lifecycle as lifecycle
    import api.services.scanner as scanner

    dashboard.invalidate_rollup()
    lifecycle._memo.clear()
    scanner._set_status(
        running=False, started_at=None, finished_at=None, last_result=None, last_error=None
    )
    yield


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Fail loudly if a test reaches NVD or endoflife.date for real.
    """
    def blocked(*args, **kwargs):
        raise AssertionError("a test tried to make a real HTTP request; install a fake instead")

    monkeypatch.setattr(requests, "get", blocked)
    monkeypatch.setattr(requests, "post", blocked)


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    """A TestClient over the real application, admin key not attached."""
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def admin():
    return dict(ADMIN_HEADERS)


@pytest.fixture
def enrol(client):
    """Enrol a device and return (device_id, auth headers)."""
    def _enrol(device_id="dev-1", hostname=None, ring=None, department=None):
        payload = {
            "device_id": device_id,
            "hostname": hostname or device_id.upper(),
            "enrollment_secret": ENROLLMENT_SECRET,
        }
        if department:
            payload["department"] = department
        response = client.post("/api/v1/agent/register", json=payload)
        response.raise_for_status()
        if ring:
            client.patch(
                f"/api/v1/agent/{device_id}/ring",
                json={"deployment_ring": ring}, headers=ADMIN_HEADERS,
            )
        return device_id, {"Authorization": f"Bearer {response.json()['token']}"}

    return _enrol


@pytest.fixture
def heartbeat(client):
    def _heartbeat(device_id, auth, **overrides):
        payload = {"device_id": device_id, "cpu_usage": 5.0, "ram_usage": 20.0, **overrides}
        return client.post("/api/v1/agent/heartbeat", json=payload, headers=auth)

    return _heartbeat


@pytest.fixture
def queue_task(client):
    def _queue(device_id, action="UPDATE_WINGET", target=None):
        params = {"action": action}
        if target:
            params["target"] = target
        response = client.post(
            f"/api/v1/agent/{device_id}/queue_task", params=params, headers=ADMIN_HEADERS
        )
        response.raise_for_status()
        return response.json()["task_id"]

    return _queue
