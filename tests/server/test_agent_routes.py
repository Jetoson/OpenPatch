"""The routes an agent calls: enrolment, heartbeat, inventory, results."""

from datetime import datetime, timedelta, timezone

import api.routers.agent as agent_router
import pytest
from api.models import (
    Endpoint,
    PendingUpdate,
    SoftwareInventory,
    TaskQueue,
    TelemetryHistory,
)

from tests.server.conftest import ENROLLMENT_SECRET


def registration(**fields):
    """A register payload carrying the secret enrolment now requires."""
    return {"enrollment_secret": ENROLLMENT_SECRET, **fields}


class TestEnrolment:
    def test_a_device_is_issued_a_token(self, client):
        response = client.post(
            "/api/v1/agent/register", json=registration(device_id="dev-1")
        )
        assert response.status_code == 200
        assert response.json()["token"]

    def test_only_the_hash_is_stored(self, client, db):
        """The plaintext token exists in exactly one place: the endpoint."""
        token = client.post(
            "/api/v1/agent/register", json=registration(device_id="dev-1")
        ).json()["token"]

        endpoint = db.query(Endpoint).filter_by(device_id="dev-1").one()
        assert endpoint.token_hash and endpoint.token_hash != token

    def test_re_enrolment_invalidates_the_previous_token(self, client, heartbeat):
        first = client.post(
            "/api/v1/agent/register", json=registration(device_id="dev-1")
        ).json()["token"]
        client.post("/api/v1/agent/register", json=registration(device_id="dev-1"))

        assert heartbeat("dev-1", {"Authorization": f"Bearer {first}"}).status_code == 401

    def test_an_omitted_field_does_not_erase_what_is_known(self, client, db):
        client.post("/api/v1/agent/register", json=registration(
            device_id="dev-1", hostname="WS-01", department="Finance",
        ))
        client.post("/api/v1/agent/register", json=registration(device_id="dev-1"))

        endpoint = db.query(Endpoint).filter_by(device_id="dev-1").one()
        assert endpoint.hostname == "WS-01"
        assert endpoint.department == "Finance"

    def test_joining_the_fleet_needs_the_secret(self, client):
        assert client.post(
            "/api/v1/agent/register", json={"device_id": "dev-9"}
        ).status_code == 401
        assert client.post("/api/v1/agent/register", json={
            "device_id": "dev-9", "enrollment_secret": "wrong",
        }).status_code == 401
        assert client.post(
            "/api/v1/agent/register", json=registration(device_id="dev-9")
        ).status_code == 200

    def test_the_log_says_which_kind_of_refusal_it_was(self, client, capsys):
        """The caller gets one answer either way."""
        client.post("/api/v1/agent/register", json={"device_id": "dev-9"})
        assert "no enrolment secret was presented" in capsys.readouterr().out

        client.post("/api/v1/agent/register", json={
            "device_id": "dev-9", "enrollment_secret": "not-the-one",
        })
        assert "does not match" in capsys.readouterr().out

    def test_the_log_never_prints_either_secret(self, client, capsys):
        client.post("/api/v1/agent/register", json={
            "device_id": "dev-9", "enrollment_secret": "not-the-one",
        })

        out = capsys.readouterr().out
        assert "not-the-one" not in out
        assert ENROLLMENT_SECRET not in out

    def test_enrolment_can_be_opened_deliberately(self, client, monkeypatch):
        monkeypatch.setattr(agent_router, "ENROLLMENT_SECRET", "")

        assert client.post(
            "/api/v1/agent/register", json={"device_id": "dev-9"}
        ).status_code == 200


class TestHeartbeat:
    def test_an_unknown_token_is_refused(self, client):
        assert client.post(
            "/api/v1/agent/heartbeat",
            json={"device_id": "dev-1", "cpu_usage": 1.0, "ram_usage": 1.0},
            headers={"Authorization": "Bearer nonsense"},
        ).status_code == 401

    def test_a_token_cannot_speak_for_another_device(self, enrol, heartbeat):
        """Otherwise one compromised endpoint can rewrite the fleet."""
        enrol("dev-1")
        _, other_auth = enrol("dev-2")

        assert heartbeat("dev-1", other_auth).status_code == 403

    def test_the_server_dictates_the_poll_interval(self, enrol, heartbeat):
        device_id, auth = enrol("dev-1")
        assert heartbeat(device_id, auth).json()["poll_interval"] == 30

    def test_live_telemetry_is_refreshed_every_time(self, enrol, heartbeat, db):
        device_id, auth = enrol("dev-1")
        heartbeat(device_id, auth, cpu_usage=11.0)
        heartbeat(device_id, auth, cpu_usage=77.0)

        assert db.query(Endpoint).filter_by(device_id=device_id).one().cpu_usage == 77.0

    def test_history_is_sampled_not_appended_per_heartbeat(self, enrol, heartbeat, db):
        device_id, auth = enrol("dev-1")
        for _ in range(5):
            heartbeat(device_id, auth)

        assert db.query(TelemetryHistory).count() == 1

    def test_a_sample_is_stored_once_the_interval_has_passed(self, enrol, heartbeat, db):
        device_id, auth = enrol("dev-1")
        heartbeat(device_id, auth)

        endpoint = db.query(Endpoint).filter_by(device_id=device_id).one()
        endpoint.telemetry_recorded_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()

        heartbeat(device_id, auth)
        assert db.query(TelemetryHistory).count() == 2

    def test_a_reboot_flag_can_be_cleared(self, enrol, heartbeat, db):
        device_id, auth = enrol("dev-1")
        heartbeat(device_id, auth, reboot_required=True, reboot_reasons="Windows Update")
        heartbeat(device_id, auth, reboot_required=False)

        assert db.query(Endpoint).filter_by(device_id=device_id).one().reboot_required is False


class TestTaskDelivery:
    def test_pending_work_rides_the_heartbeat(self, enrol, heartbeat, queue_task):
        device_id, auth = enrol("dev-1")
        queue_task(device_id, "UPDATE_WINGET", "Acme.App")

        pending = heartbeat(device_id, auth).json()["pending_tasks"]

        assert [t["action"] for t in pending] == ["UPDATE_WINGET"]
        assert pending[0]["target"] == "Acme.App"

    def test_an_unknown_action_is_rejected_at_the_point_of_the_mistake(self, client, enrol, admin):
        device_id, _ = enrol("dev-1")
        response = client.post(
            f"/api/v1/agent/{device_id}/queue_task", params={"action": "RM_RF"}, headers=admin
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("action", [
        "UPDATE_WINGET", "UPDATE_OS", "RESTART",
        "UPDATE_AND_VERIFY", "UPDATE_VERIFY_HEAL", "ROLLBACK",
    ])
    def test_every_action_the_agent_implements_is_accepted(self, enrol, queue_task, action):
        device_id, _ = enrol("dev-1")
        assert queue_task(device_id, action)

    def test_a_result_updates_the_task(self, client, enrol, heartbeat, queue_task, db):
        device_id, auth = enrol("dev-1")
        task_id = queue_task(device_id)
        heartbeat(device_id, auth)

        client.post("/api/v1/agent/task/result", headers=auth, json={
            "device_id": device_id, "task_id": task_id,
            "status": "SUCCESS_VERIFIED", "output": "all good",
        })

        task = db.query(TaskQueue).filter_by(id=task_id).one()
        assert task.status == "SUCCESS_VERIFIED"
        assert task.output == "all good"

    def test_a_device_cannot_report_on_another_devices_task(self, client, enrol, queue_task, db):
        device_id, _ = enrol("dev-1")
        enrol("dev-2")
        _, other_auth = enrol("dev-2")
        task_id = queue_task(device_id)

        client.post("/api/v1/agent/task/result", headers=other_auth, json={
            "device_id": "dev-2", "task_id": task_id, "status": "SUCCESS", "output": "x",
        })

        assert db.query(TaskQueue).filter_by(id=task_id).one().status == "PENDING"

    def test_a_device_can_ask_whether_its_task_still_stands(self, client, enrol, queue_task):
        device_id, auth = enrol("dev-1")
        task_id = queue_task(device_id)

        response = client.get(f"/api/v1/agent/task/{task_id}/status", headers=auth)

        assert response.json() == {"task_id": task_id, "status": "PENDING"}

    def test_that_question_is_scoped_to_the_asking_device(self, client, enrol, queue_task):
        """A token must not be usable to enumerate another endpoint's tasks."""
        device_id, _ = enrol("dev-1")
        _, other_auth = enrol("dev-2")
        task_id = queue_task(device_id)

        assert client.get(
            f"/api/v1/agent/task/{task_id}/status", headers=other_auth
        ).status_code == 404


class TestCriticalProgramsCommand:
    """The PowerShell _critical_programs_command generates from a plain list."""

    def test_nothing_configured_generates_nothing(self):
        assert agent_router._critical_programs_command(None) is None
        assert agent_router._critical_programs_command("") is None
        assert agent_router._critical_programs_command("   ,  ,") is None

    def test_each_name_becomes_a_quoted_literal(self):
        command = agent_router._critical_programs_command("notepad, chrome")
        assert "'notepad'" in command
        assert "'chrome'" in command

    def test_blank_entries_and_surrounding_space_are_dropped(self):
        command = agent_router._critical_programs_command(" notepad ,, chrome ")
        assert command.count("'notepad'") == 1
        assert command.count("'chrome'") == 1

    def test_an_embedded_quote_does_not_break_out_of_the_literal(self):
        command = agent_router._critical_programs_command("Bob's App")
        assert "'Bob''s App'" in command


class TestVerificationSettings:

    def test_it_is_admin_gated(self, client, enrol):
        device_id, _ = enrol("dev-1")
        response = client.patch(
            f"/api/v1/agent/{device_id}/verification",
            json={"verify_command": "Get-Process notepad -ErrorAction Stop"},
        )
        assert response.status_code == 401

    def test_it_persists_and_reads_back(self, client, enrol, admin, db):
        device_id, _ = enrol("dev-1")
        response = client.patch(
            f"/api/v1/agent/{device_id}/verification",
            json={
                "verify_command": "Get-Process notepad -ErrorAction Stop",
                "critical_programs": "notepad, chrome",
            },
            headers=admin,
        )
        assert response.json() == {
            "device_id": device_id,
            "verify_command": "Get-Process notepad -ErrorAction Stop",
            "critical_programs": "notepad, chrome",
        }
        endpoint = db.query(Endpoint).filter_by(device_id=device_id).one()
        assert endpoint.verify_command == "Get-Process notepad -ErrorAction Stop"
        assert endpoint.critical_programs == "notepad, chrome"

    def test_blank_clears_rather_than_storing_an_empty_string(self, client, enrol, admin, db):
        device_id, _ = enrol("dev-1")
        client.patch(f"/api/v1/agent/{device_id}/verification", json={
            "verify_command": "Get-Process notepad", "critical_programs": "notepad",
        }, headers=admin)

        client.patch(f"/api/v1/agent/{device_id}/verification", json={
            "verify_command": "   ", "critical_programs": "   ",
        }, headers=admin)

        endpoint = db.query(Endpoint).filter_by(device_id=device_id).one()
        assert endpoint.verify_command is None
        assert endpoint.critical_programs is None

    def test_an_unknown_device_is_refused(self, client, admin):
        response = client.patch(
            "/api/v1/agent/no-such-device/verification",
            json={"verify_command": "x"}, headers=admin,
        )
        assert response.status_code == 404

    @pytest.mark.parametrize("action", ["UPDATE_AND_VERIFY", "UPDATE_VERIFY_HEAL"])
    def test_queueing_bare_picks_up_the_configured_command(
        self, client, enrol, queue_task, admin, db, action
    ):
        device_id, _ = enrol("dev-1")
        client.patch(f"/api/v1/agent/{device_id}/verification",
                    json={"verify_command": "Test-Path C:\\App\\app.exe"}, headers=admin)

        task_id = queue_task(device_id, action)

        assert db.query(TaskQueue).filter_by(id=task_id).one().target == "Test-Path C:\\App\\app.exe"

    @pytest.mark.parametrize("action", ["UPDATE_AND_VERIFY", "UPDATE_VERIFY_HEAL"])
    def test_the_critical_programs_list_becomes_a_generated_command(
        self, client, enrol, queue_task, admin, db, action
    ):
        device_id, _ = enrol("dev-1")
        client.patch(f"/api/v1/agent/{device_id}/verification",
                    json={"critical_programs": "AcmeApp, sqlservr"}, headers=admin)

        task_id = queue_task(device_id, action)

        target = db.query(TaskQueue).filter_by(id=task_id).one().target
        assert "'AcmeApp'" in target
        assert "'sqlservr'" in target
        assert "Get-Process" in target

    @pytest.mark.parametrize("action", ["UPDATE_AND_VERIFY", "UPDATE_VERIFY_HEAL"])
    def test_a_raw_command_wins_over_the_critical_programs_list(
        self, client, enrol, queue_task, admin, db, action
    ):
        device_id, _ = enrol("dev-1")
        client.patch(f"/api/v1/agent/{device_id}/verification", json={
            "verify_command": "my own check",
            "critical_programs": "AcmeApp",
        }, headers=admin)

        task_id = queue_task(device_id, action)

        assert db.query(TaskQueue).filter_by(id=task_id).one().target == "my own check"

    @pytest.mark.parametrize("action", ["UPDATE_AND_VERIFY", "UPDATE_VERIFY_HEAL"])
    def test_an_explicit_target_overrides_both(
        self, client, enrol, queue_task, admin, db, action
    ):
        device_id, _ = enrol("dev-1")
        client.patch(f"/api/v1/agent/{device_id}/verification", json={
            "verify_command": "configured check", "critical_programs": "AcmeApp",
        }, headers=admin)

        task_id = queue_task(device_id, action, target="one-off check")

        assert db.query(TaskQueue).filter_by(id=task_id).one().target == "one-off check"

    def test_other_actions_are_never_auto_filled(self, client, enrol, queue_task, admin, db):
        device_id, _ = enrol("dev-1")
        client.patch(f"/api/v1/agent/{device_id}/verification",
                    json={"verify_command": "configured check"}, headers=admin)

        task_id = queue_task(device_id, "UPDATE_WINGET")

        assert db.query(TaskQueue).filter_by(id=task_id).one().target is None

    @pytest.mark.parametrize("action", ["UPDATE_AND_VERIFY", "UPDATE_VERIFY_HEAL"])
    def test_nothing_configured_leaves_the_target_empty(
        self, client, enrol, queue_task, db, action
    ):
        device_id, _ = enrol("dev-1")

        task_id = queue_task(device_id, action)

        assert db.query(TaskQueue).filter_by(id=task_id).one().target is None


class TestTaskSignature:
    def test_the_task_list_is_signed_when_a_secret_is_configured(
        self, enrol, heartbeat, queue_task, monkeypatch
    ):
        import api.services.task_signing as signing

        monkeypatch.setattr(signing, "TASK_SIGNING_SECRET", "shared")
        device_id, auth = enrol("dev-1")
        queue_task(device_id)

        response = heartbeat(device_id, auth)

        assert signing.SIGNATURE_HEADER in response.headers
        assert signing.TIMESTAMP_HEADER in response.headers

    def test_the_signature_matches_what_an_agent_would_compute(
        self, enrol, heartbeat, queue_task, monkeypatch
    ):
        import api.services.task_signing as signing

        monkeypatch.setattr(signing, "TASK_SIGNING_SECRET", "shared")
        device_id, auth = enrol("dev-1")
        queue_task(device_id)

        response = heartbeat(device_id, auth)
        expected = signing.compute_signature(
            device_id,
            response.headers[signing.TIMESTAMP_HEADER],
            response.json()["pending_tasks"],
            "shared",
        )
        assert response.headers[signing.SIGNATURE_HEADER] == expected

    def test_task_lists_are_signed_without_being_asked(self, enrol, heartbeat, queue_task):
        import api.services.task_signing as signing

        device_id, auth = enrol("dev-1")
        queue_task(device_id)

        assert signing.SIGNATURE_HEADER in heartbeat(device_id, auth).headers

    def test_nothing_is_signed_without_a_secret_at_all(
        self, enrol, heartbeat, monkeypatch
    ):
        import api.services.task_signing as signing

        monkeypatch.setattr(signing, "TASK_SIGNING_SECRET", "")
        device_id, auth = enrol("dev-1")

        assert signing.SIGNATURE_HEADER not in heartbeat(device_id, auth).headers


class TestInventoryAndUpdates:
    def test_inventory_replaces_rather_than_accumulates(self, client, enrol, db):
        device_id, auth = enrol("dev-1")
        for names in (["A", "B", "C"], ["A"]):
            client.post("/api/v1/agent/inventory", headers=auth, json={
                "device_id": device_id,
                "software_list": [{"name": n, "version": "1.0"} for n in names],
            })

        assert db.query(SoftwareInventory).count() == 1

    def test_a_large_inventory_is_accepted(self, client, enrol, db):
        device_id, auth = enrol("dev-1")
        items = [{"name": f"App {i}", "version": "1.0"} for i in range(300)]

        response = client.post("/api/v1/agent/inventory", headers=auth,
                               json={"device_id": device_id, "software_list": items})

        assert response.json()["count"] == 300
        assert db.query(SoftwareInventory).count() == 300

    def test_pending_updates_record_when_they_were_collected(self, client, enrol, db):
        device_id, auth = enrol("dev-1")
        client.post("/api/v1/agent/updates", headers=auth, json={
            "device_id": device_id,
            "updates": [{"source": "winget", "name": "App", "kb": "Acme.App"}],
        })

        assert db.query(PendingUpdate).one().collected_at is not None

    def test_inventory_requires_a_device_token(self, client):
        assert client.post(
            "/api/v1/agent/inventory", json={"device_id": "dev-1", "software_list": []}
        ).status_code == 401
