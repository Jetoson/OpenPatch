"""Operator routes are gated, and a device token is not a key to them."""

import inspect

import pytest

from tests.server.conftest import ADMIN_KEY

ADMIN_ROUTES = [
    ("GET", "/api/v1/dashboard/summary", None),
    ("GET", "/api/v1/dashboard/endpoints", None),
    ("GET", "/api/v1/dashboard/findings", None),
    ("GET", "/api/v1/dashboard/tasks", None),
    ("GET", "/api/v1/dashboard/scan-status", None),
    ("GET", "/api/v1/dashboard/rings", None),
    ("GET", "/api/v1/dashboard/departments", None),
    ("POST", "/api/v1/tasks/remediate/ring", {"ring_name": "Test-Ring", "software_name": "x"}),
    ("POST", "/api/v1/tasks/revert/ring", {"ring_name": "Test-Ring"}),
    ("POST", "/api/v1/tasks/cancel", {"all_pending": True}),
]
IDS = [f"{m} {p}" for m, p, _ in ADMIN_ROUTES]


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES, ids=IDS)
def test_an_unauthenticated_call_is_refused(client, method, path, body):
    assert client.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES, ids=IDS)
def test_a_wrong_key_is_refused(client, method, path, body):
    response = client.request(method, path, json=body, headers={"X-Admin-Key": "nope"})
    assert response.status_code == 403


def test_the_admin_key_is_accepted(client, admin):
    assert client.get("/api/v1/dashboard/summary", headers=admin).status_code == 200


def test_the_key_may_also_be_sent_as_a_bearer_token(client):
    """Purely so curl and the OpenAPI docs work the usual way."""
    response = client.get(
        "/api/v1/dashboard/rings", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
    )
    assert response.status_code == 200


def test_a_device_token_is_not_an_admin_key(client, enrol):
    """A device token lives in a file on a machine that may itself be the
    thing that got compromised, and an endpoint has no business queueing work
    on its neighbours."""
    _, auth = enrol("dev-1")
    token = auth["Authorization"].removeprefix("Bearer ")

    assert client.get(
        "/api/v1/dashboard/summary", headers={"X-Admin-Key": token}
    ).status_code == 403


def test_queueing_work_is_an_operator_action(client, enrol):
    device_id, auth = enrol("dev-1")

    assert client.post(
        f"/api/v1/agent/{device_id}/queue_task", params={"action": "RESTART"}
    ).status_code == 401
    # ...and a device cannot queue work on itself either.
    assert client.post(
        f"/api/v1/agent/{device_id}/queue_task", params={"action": "RESTART"}, headers=auth
    ).status_code == 403


def test_moving_a_device_between_rings_is_an_operator_action(client, enrol):
    """An endpoint must not be able to promote itself out of the test ring."""
    device_id, auth = enrol("dev-1")
    assert client.patch(
        f"/api/v1/agent/{device_id}/ring", json={"deployment_ring": "Production-Ring"},
        headers=auth,
    ).status_code == 403


def test_key_comparison_is_constant_time():
    """This value is compared directly rather than looked up by hash, so the
    comparison itself has to not leak the length of a matching prefix."""
    from api.services import admin_auth

    assert "compare_digest" in inspect.getsource(admin_auth.require_admin)


def test_there_is_no_unconfigured_fallback(tmp_path):
    """Falling back to "no key set, so allow everything" is how an open admin
    API survives into production. An unset key is generated instead.
    """
    import generated_secrets

    key_file = tmp_path / "admin_key"
    generated_secrets.reset_cache()

    key = generated_secrets.resolve(None, str(key_file))

    assert key
    assert key_file.read_text().strip() == key, "persisted, not invented per process"
    generated_secrets.reset_cache()
    assert generated_secrets.resolve(None, str(key_file)) == key
