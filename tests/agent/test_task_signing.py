"""HMAC verification of the task list.
"""

import json
import time

import task_signing

SECRET = "shared-secret"
TASKS = [{"task_id": 1, "action": "UPDATE_WINGET", "target": "Acme.App"}]


def headers_for(device_id=" dev-1", tasks=None, secret=SECRET, timestamp=None):
    device_id = device_id.strip()
    timestamp = timestamp or str(int(time.time()))
    return {
        task_signing.TIMESTAMP_HEADER: timestamp,
        task_signing.SIGNATURE_HEADER: task_signing.compute_signature(
            device_id, timestamp, TASKS if tasks is None else tasks, secret
        ),
    }


def test_a_valid_signature_is_accepted():
    accepted, _ = task_signing.verify("dev-1", TASKS, headers_for(), SECRET)
    assert accepted is True


def test_missing_headers_are_refused():
    accepted, reason = task_signing.verify("dev-1", TASKS, {}, SECRET)
    assert accepted is False
    assert reason


def test_a_signature_from_the_wrong_secret_is_refused():
    accepted, _ = task_signing.verify("dev-1", TASKS, headers_for(secret="wrong"), SECRET)
    assert accepted is False


def test_a_signature_for_another_device_is_refused():
    """device_id is signed so a payload captured for one endpoint cannot be
    replayed at a different one."""
    accepted, _ = task_signing.verify("dev-1", TASKS, headers_for(device_id="dev-2"), SECRET)
    assert accepted is False


def test_a_modified_task_list_is_refused():
    accepted, _ = task_signing.verify(
        "dev-1", [{"task_id": 1, "action": "ROLLBACK", "target": None}], headers_for(), SECRET
    )
    assert accepted is False


def test_an_old_signature_is_refused():
    """timestamp is signed so a captured payload stops being valid once it
    ages out - otherwise that RESTART is authorised forever."""
    stale = str(int(time.time()) - 86400)
    accepted, _ = task_signing.verify("dev-1", TASKS, headers_for(timestamp=stale), SECRET)
    assert accepted is False


def test_a_future_timestamp_is_refused():
    ahead = str(int(time.time()) + 86400)
    accepted, _ = task_signing.verify("dev-1", TASKS, headers_for(timestamp=ahead), SECRET)
    assert accepted is False


def test_a_non_numeric_timestamp_is_refused_not_crashed():
    headers = headers_for()
    headers[task_signing.TIMESTAMP_HEADER] = "not-a-number"
    accepted, reason = task_signing.verify("dev-1", TASKS, headers, SECRET)
    assert accepted is False
    assert reason


def test_an_empty_task_list_still_verifies():
    accepted, _ = task_signing.verify("dev-1", [], headers_for(tasks=[]), SECRET)
    assert accepted is True


class TestCanonicalForm:
    """The agent re-serialises the list it parsed out of the response, so
    anything that lets two equal task lists serialise differently would break
    every signature. server/api/services/task_signing.py must produce
    byte-identical output - the two cannot import each other."""

    def test_key_order_does_not_matter(self):
        a = task_signing.canonical_payload("dev-1", "100", [{"a": 1, "b": 2}])
        b = task_signing.canonical_payload("dev-1", "100", [{"b": 2, "a": 1}])
        assert a == b

    def test_the_body_is_compact_json(self):
        payload = task_signing.canonical_payload("dev-1", "100", TASKS)
        body = payload.decode("utf-8").split("\n", 2)[2]
        assert body == json.dumps(TASKS, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        assert ", " not in body, "incidental whitespace would break the signature"

    def test_device_and_timestamp_lead_the_payload(self):
        payload = task_signing.canonical_payload("dev-1", "100", TASKS).decode("utf-8")
        assert payload.startswith("dev-1\n100\n")

    def test_the_signature_changes_with_every_signed_input(self):
        base = task_signing.compute_signature("dev-1", "100", TASKS, SECRET)
        assert task_signing.compute_signature("dev-2", "100", TASKS, SECRET) != base
        assert task_signing.compute_signature("dev-1", "101", TASKS, SECRET) != base
        assert task_signing.compute_signature("dev-1", "100", [], SECRET) != base
        assert task_signing.compute_signature("dev-1", "100", TASKS, "other") != base


def test_the_agent_and_server_agree_byte_for_byte(monkeypatch):
    """The one thing that cannot be caught by testing either side alone."""
    import importlib.util
    import os
    import sys
    import types

    server_module_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "server", "api", "services", "task_signing.py",
    )
    spec = importlib.util.spec_from_file_location("server_task_signing", server_module_path)
    server_signing = importlib.util.module_from_spec(spec)

    fleet_secrets = types.ModuleType("api.services.fleet_secrets")
    fleet_secrets.task_signing_secret = lambda: SECRET
    services = types.ModuleType("api.services")
    services.fleet_secrets = fleet_secrets
    api_package = types.ModuleType("api")
    api_package.services = services
    monkeypatch.setitem(sys.modules, "api", api_package)
    monkeypatch.setitem(sys.modules, "api.services", services)
    monkeypatch.setitem(sys.modules, "api.services.fleet_secrets", fleet_secrets)
    spec.loader.exec_module(server_signing)

    for device, timestamp, tasks in [
        ("dev-1", "100", TASKS),
        ("dev-2", "999", []),
        ("dev-3", "1", [{"task_id": 2, "action": "UPDATE_OS", "target": None}]),
    ]:
        assert server_signing.canonical_payload(device, timestamp, tasks) == \
            task_signing.canonical_payload(device, timestamp, tasks)
        assert server_signing.compute_signature(device, timestamp, tasks, SECRET) == \
            task_signing.compute_signature(device, timestamp, tasks, SECRET)
