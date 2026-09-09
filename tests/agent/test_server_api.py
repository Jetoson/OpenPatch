"""Every call the agent makes to the server.
"""

import pytest
import server_api

ALL_CALLS = [
    ("heartbeat", lambda a: a.heartbeat({"device_id": "dev-1", "cpu_usage": 1.0})),
    ("report_task_result", lambda a: a.report_task_result(7, "SUCCESS", "done")),
    ("task_status", lambda a: a.task_status(7)),
    ("send_inventory", lambda a: a.send_inventory([{"name": "App", "version": "1.0"}])),
    ("send_pending_updates", lambda a: a.send_pending_updates([{"source": "winget"}])),
]


@pytest.mark.parametrize("name,invoke", ALL_CALLS, ids=[c[0] for c in ALL_CALLS])
def test_every_call_carries_the_bearer_token(api, name, invoke):
    invoke(api)
    assert api.session.calls[-1]["headers"] == {"Authorization": "Bearer tok-1"}


@pytest.mark.parametrize("name,invoke", ALL_CALLS, ids=[c[0] for c in ALL_CALLS])
def test_every_call_goes_through_the_shared_session(api, name, invoke):
    before = len(api.session.calls)
    invoke(api)
    assert len(api.session.calls) == before + 1


@pytest.mark.parametrize("name,invoke", ALL_CALLS, ids=[c[0] for c in ALL_CALLS])
def test_every_call_targets_the_agent_api(api, name, invoke):
    invoke(api)
    assert api.session.calls[-1]["url"].startswith("https://patch.example:8000/api/v1/agent/")


class TestUrls:
    def test_a_trailing_slash_does_not_double_up(self, fake_session):
        api = server_api.ServerAPI(fake_session(), "https://x:8000/", "dev-1", "t")
        assert api._url("/heartbeat") == "https://x:8000/api/v1/agent/heartbeat"

    def test_task_status_addresses_the_task(self, api):
        api.task_status(42)
        assert api.session.calls[-1]["url"].endswith("/task/42/status")


class TestPayloads:
    def test_heartbeat_sends_the_telemetry_unchanged(self, api):
        payload = {"device_id": "dev-1", "cpu_usage": 12.5, "ram_usage": 40.0}
        api.heartbeat(payload)
        assert api.session.calls[-1]["json"] == payload

    def test_task_result_identifies_the_device_and_the_task(self, api):
        api.report_task_result(7, "FAILED", "log text")
        assert api.session.calls[-1]["json"] == {
            "device_id": "dev-1", "task_id": 7, "status": "FAILED", "output": "log text",
        }

    def test_inventory_is_wrapped_with_the_device_id(self, api):
        items = [{"name": "App", "version": "1.0", "publisher": "Acme"}]
        api.send_inventory(items)
        assert api.session.calls[-1]["json"] == {"device_id": "dev-1", "software_list": items}

    def test_updates_are_wrapped_with_the_device_id(self, api):
        updates = [{"source": "winget", "name": "App"}]
        api.send_pending_updates(updates)
        assert api.session.calls[-1]["json"] == {"device_id": "dev-1", "updates": updates}


class TestTimeouts:
    def test_the_update_scan_post_has_one(self, api):
        api.send_pending_updates([])
        assert api.session.calls[-1]["timeout"] == 30

    def test_the_cancellation_check_has_one(self, api):
        """It runs between tasks; a hung request would stall the queue."""
        api.task_status(1)
        assert api.session.calls[-1]["timeout"] == 10


class TestReEnrolment:
    def test_new_credentials_are_adopted(self, api):
        api.set_credentials("https://new-server:9000", "tok-2")

        api.heartbeat({})

        call = api.session.calls[-1]
        assert call["url"].startswith("https://new-server:9000/")
        assert call["headers"] == {"Authorization": "Bearer tok-2"}

    def test_the_device_id_survives_re_enrolment(self, api):
        """It is derived from the machine, not issued by the server."""
        api.set_credentials("https://new-server:9000", "tok-2")
        assert api.device_id == "dev-1"
