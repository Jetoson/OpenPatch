"""The agent's startup gates and poll loop."""

import os
import sys

import agent_config
import agent_service
import pytest
import requests
import tasks
import telemetry


class TestElevation:
    def test_an_elevated_agent_starts(self, monkeypatch):
        monkeypatch.setattr(telemetry, "is_elevated", lambda: True)
        agent_service.require_elevation()   # must not raise

    def test_an_unelevated_agent_refuses_to_start(self, monkeypatch):
        monkeypatch.setattr(telemetry, "is_elevated", lambda: False)

        with pytest.raises(SystemExit) as exit_info:
            agent_service.require_elevation()

        message = str(exit_info.value)
        assert "must run elevated" in message
        assert "install_agent_task.ps1" in message, "it must say how to fix it"

    @pytest.mark.parametrize("value", ["1", "true", "True"])
    def test_the_development_override_is_honoured(self, monkeypatch, value):
        monkeypatch.setattr(telemetry, "is_elevated", lambda: False)
        monkeypatch.setenv(agent_service.ALLOW_UNELEVATED_ENV, value)
        agent_service.require_elevation()   # must not raise

    @pytest.mark.parametrize("value", ["0", "no", "", "yes"])
    def test_anything_else_still_refuses(self, monkeypatch, value):
        monkeypatch.setattr(telemetry, "is_elevated", lambda: False)
        monkeypatch.setenv(agent_service.ALLOW_UNELEVATED_ENV, value)
        with pytest.raises(SystemExit):
            agent_service.require_elevation()


class TestEnrolmentGate:
    def test_an_unenrolled_agent_explains_how_to_enrol(self, monkeypatch):
        monkeypatch.setattr(agent_config, "load", lambda: None)

        with pytest.raises(SystemExit) as exit_info:
            agent_service.load_config()

        assert "not enrolled" in str(exit_info.value)
        assert "enroll --server" in str(exit_info.value)

    def test_an_enrolled_agent_gets_its_config(self, monkeypatch):
        config = agent_config.AgentConfig("https://x:8000", "tok", "Finance")
        monkeypatch.setattr(agent_config, "load", lambda: config)
        assert agent_service.load_config() is config


class TestPollInterval:
    def test_the_server_sets_the_cadence(self):
        assert agent_service.apply_poll_interval({"poll_interval": 60}, 30) == 60

    def test_no_instruction_keeps_the_current_cadence(self):
        assert agent_service.apply_poll_interval({}, 45) == 45

    @pytest.mark.parametrize("bad", ["soon", None, {"a": 1}, [], True])
    def test_a_nonsense_value_is_ignored(self, bad):
        assert agent_service.apply_poll_interval({"poll_interval": bad}, 30) == 30

    def test_it_is_clamped_so_the_server_cannot_hammer_itself(self):
        assert agent_service.apply_poll_interval({"poll_interval": 0}, 30) == \
            agent_service.MIN_POLL_INTERVAL

    def test_it_is_clamped_so_an_agent_cannot_be_silenced(self):
        assert agent_service.apply_poll_interval({"poll_interval": 999999}, 30) == \
            agent_service.MAX_POLL_INTERVAL

    def test_the_default_is_not_the_old_five_seconds(self):
        assert agent_service.DEFAULT_POLL_INTERVAL == 30


class TestJitter:
    def test_sleep_never_undershoots_the_interval(self):
        assert all(agent_service.next_sleep(30) >= 30 for _ in range(200))

    def test_sleep_adds_at_most_ten_percent(self):
        assert all(agent_service.next_sleep(30) <= 33 for _ in range(200))

    def test_it_actually_varies(self):
        assert len({agent_service.next_sleep(30) for _ in range(200)}) > 100


class TestTaskTrust:
    TASKS = [{"task_id": 1, "action": "RESTART", "target": None}]

    def test_without_a_configured_secret_tasks_are_allowed(self, monkeypatch):
        monkeypatch.setattr(agent_config, "task_signing_secret", lambda: "")
        assert agent_service.task_list_is_trusted("dev-1", self.TASKS, {}) is True

    def test_a_correctly_signed_list_is_accepted(self, monkeypatch):
        import time

        import task_signing

        secret = "s3cret"
        monkeypatch.setattr(agent_config, "task_signing_secret", lambda: secret)
        timestamp = str(int(time.time()))
        headers = {
            task_signing.TIMESTAMP_HEADER: timestamp,
            task_signing.SIGNATURE_HEADER: task_signing.compute_signature(
                "dev-1", timestamp, self.TASKS, secret
            ),
        }
        assert agent_service.task_list_is_trusted("dev-1", self.TASKS, headers) is True

    def test_an_unsigned_list_is_refused_when_a_secret_is_set(self, monkeypatch):
        monkeypatch.setattr(agent_config, "task_signing_secret", lambda: "s3cret")
        assert agent_service.task_list_is_trusted("dev-1", self.TASKS, {}) is False

    def test_a_signature_for_another_device_is_refused(self, monkeypatch):
        import time

        import task_signing

        secret = "s3cret"
        monkeypatch.setattr(agent_config, "task_signing_secret", lambda: secret)
        timestamp = str(int(time.time()))
        headers = {
            task_signing.TIMESTAMP_HEADER: timestamp,
            task_signing.SIGNATURE_HEADER: task_signing.compute_signature(
                "someone-else", timestamp, self.TASKS, secret
            ),
        }
        assert agent_service.task_list_is_trusted("dev-1", self.TASKS, headers) is False

    def test_tampering_with_the_task_list_invalidates_it(self, monkeypatch):
        import time

        import task_signing

        secret = "s3cret"
        monkeypatch.setattr(agent_config, "task_signing_secret", lambda: secret)
        timestamp = str(int(time.time()))
        headers = {
            task_signing.TIMESTAMP_HEADER: timestamp,
            task_signing.SIGNATURE_HEADER: task_signing.compute_signature(
                "dev-1", timestamp, self.TASKS, secret
            ),
        }
        tampered = [{"task_id": 1, "action": "ROLLBACK", "target": None}]
        assert agent_service.task_list_is_trusted("dev-1", tampered, headers) is False


class TestCancellationCheck:
    def test_a_cancelled_task_is_detected(self, api, fake_response):
        api.session.responses = fake_response(200, {"status": "CANCELLED"})
        assert agent_service.task_was_cancelled(api, 7) is True

    def test_a_still_pending_task_runs(self, api, fake_response):
        api.session.responses = fake_response(200, {"status": "PENDING"})
        assert agent_service.task_was_cancelled(api, 7) is False

    def test_an_unknown_task_is_treated_as_runnable(self, api, fake_response):
        api.session.responses = fake_response(404)
        assert agent_service.task_was_cancelled(api, 7) is False

    def test_it_fails_open_when_the_server_is_unreachable(self, api, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.ConnectionError("down")

        monkeypatch.setattr(api.session, "get", boom)
        assert agent_service.task_was_cancelled(api, 7) is False


class TestRunningPendingTasks:
    @pytest.fixture
    def executed(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            tasks, "execute",
            lambda action, target=None: (calls.append((action, target)), ("SUCCESS", "ok"))[1],
        )
        return calls

    def test_the_first_task_is_not_re_checked(self, api, executed, monkeypatch):
        """It was fetched milliseconds ago in the heartbeat that delivered it."""
        checked = []
        monkeypatch.setattr(
            agent_service, "task_was_cancelled",
            lambda _api, task_id: (checked.append(task_id), False)[1],
        )

        agent_service.run_pending_tasks(api, [
            {"task_id": 1, "action": "A"}, {"task_id": 2, "action": "B"},
            {"task_id": 3, "action": "C"},
        ])

        assert checked == [2, 3]
        assert [a for a, _ in executed] == ["A", "B", "C"]

    def test_a_task_cancelled_while_queued_is_skipped(self, api, executed, monkeypatch):
        """An UPDATE_OS ahead of it can take an hour, which is exactly when a
        cancellation lands."""
        monkeypatch.setattr(
            agent_service, "task_was_cancelled", lambda _api, task_id: task_id == 2
        )

        agent_service.run_pending_tasks(api, [
            {"task_id": 1, "action": "A"}, {"task_id": 2, "action": "B"},
            {"task_id": 3, "action": "C"},
        ])

        assert [a for a, _ in executed] == ["A", "C"]

    def test_a_skipped_task_reports_nothing(self, api, executed, monkeypatch):
        monkeypatch.setattr(agent_service, "task_was_cancelled", lambda _api, _id: True)

        agent_service.run_pending_tasks(api, [
            {"task_id": 1, "action": "A"}, {"task_id": 2, "action": "B"},
        ])

        reported = [c for c in api.session.calls if c["url"].endswith("/task/result")]
        assert len(reported) == 1, "only the first task, which is never re-checked"

    def test_each_result_is_reported_back(self, api, executed, monkeypatch):
        monkeypatch.setattr(agent_service, "task_was_cancelled", lambda _api, _id: False)
        monkeypatch.setattr(tasks, "execute", lambda action, target=None: ("FAILED", "why"))

        agent_service.run_pending_tasks(api, [{"task_id": 9, "action": "RESTART"}])

        body = api.session.calls[-1]["json"]
        assert body == {
            "device_id": "dev-1", "task_id": 9, "status": "FAILED", "output": "why",
        }

    def test_the_target_reaches_the_executor(self, api, executed, monkeypatch):
        monkeypatch.setattr(agent_service, "task_was_cancelled", lambda _api, _id: False)
        agent_service.run_pending_tasks(
            api, [{"task_id": 1, "action": "UPDATE_WINGET", "target": "Acme.App"}]
        )
        assert executed == [("UPDATE_WINGET", "Acme.App")]


class TestScriptJson:
    def test_a_utf8_bom_does_not_break_parsing(self):
        """PowerShell may prefix its output with a BOM, which is not
        whitespace and so survives an ordinary strip."""
        assert agent_service._parse_script_json('\ufeff{"a": 1}', "x") == {"a": 1}

    def test_ordinary_json_parses(self):
        assert agent_service._parse_script_json('[{"name": "App"}]', "x") == [{"name": "App"}]

    def test_output_that_is_not_json_is_reported_not_raised(self, capsys):
        assert agent_service._parse_script_json("Access denied", "get_software.ps1") is None
        assert "not JSON" in capsys.readouterr().out


class TestConsoleEncoding:
    """A Windows console defaults to cp1252, which cannot encode the tick the
    agent prints after each task."""

    def test_a_cp1252_console_does_not_abort_the_batch(self, api, monkeypatch, capsys):
        import io

        monkeypatch.setattr(agent_service, "task_was_cancelled", lambda _api, _id: False)
        monkeypatch.setattr(tasks, "execute", lambda action, target=None: ("SUCCESS", "ok"))

        cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        monkeypatch.setattr("sys.stdout", cp1252)

        agent_service.run_pending_tasks(api, [
            {"task_id": 1, "action": "A"}, {"task_id": 2, "action": "B"},
            {"task_id": 3, "action": "C"},
        ])

        reported = [c for c in api.session.calls if c["url"].endswith("/task/result")]
        assert len(reported) == 3, "every task in the batch must still run and report"

    def test_startup_switches_the_console_to_utf8(self, monkeypatch):
        reconfigured = []

        class Stream:
            def reconfigure(self, **kwargs):
                reconfigured.append(kwargs)

        monkeypatch.setattr("sys.stdout", Stream())
        monkeypatch.setattr("sys.stderr", Stream())

        agent_service.use_utf8_output()

        assert reconfigured == [{"encoding": "utf-8", "errors": "replace"}] * 2

    def test_a_stream_that_cannot_be_reconfigured_is_not_fatal(self, monkeypatch):
        """Redirected to a pipe, or already wrapped."""
        class Stubborn:
            def reconfigure(self, **kwargs):
                raise OSError("not a console")

        monkeypatch.setattr("sys.stdout", Stubborn())
        monkeypatch.setattr("sys.stderr", Stubborn())

        agent_service.use_utf8_output()   # must not raise


class TestSelfInstallation:

    @pytest.fixture
    def ran(self, monkeypatch):
        monkeypatch.setattr(telemetry, "is_elevated", lambda: True)
        calls = []
        monkeypatch.setattr(
            agent_service.script_runner, "run",
            lambda script, timeout=300, args=None: (
                calls.append({"script": script, "args": args}), (0, "installed")
            )[1],
        )
        return calls

    def test_it_runs_the_bundled_installer(self, ran):
        assert agent_service.install_task([]) == 0
        assert ran[0]["script"] == "install_agent_task.ps1"

    def test_the_executable_passes_its_own_path(self, ran, monkeypatch):
        monkeypatch.setattr(agent_service.agent_paths, "is_frozen", lambda: True)
        monkeypatch.setattr(sys, "executable", os.path.join("C:", "op", "openpatch-agent.exe"))

        agent_service.install_task([])

        args = ran[0]["args"]
        assert "-AgentExe" in args
        assert args[args.index("-AgentExe") + 1].endswith("openpatch-agent.exe")

    def test_uninstalling_does_not_pass_an_executable(self, ran):
        agent_service.install_task(["--uninstall"])

        args = ran[0]["args"]
        assert "-Uninstall" in args
        assert "-AgentExe" not in args

    def test_the_task_name_is_configurable(self, ran):
        agent_service.install_task(["--task-name", "OpenPatch (Pilot)"])
        args = ran[0]["args"]
        assert args[args.index("-TaskName") + 1] == "OpenPatch (Pilot)"

    def test_a_script_that_could_not_run_is_a_failure(self, monkeypatch):
        monkeypatch.setattr(telemetry, "is_elevated", lambda: True)
        """returncode None means it never ran at all, which the caller must
        not read as success."""
        monkeypatch.setattr(
            agent_service.script_runner, "run",
            lambda script, timeout=300, args=None: (None, "Script not found"),
        )
        assert agent_service.install_task([]) == 1

    def test_a_failing_script_is_a_failure(self, monkeypatch):
        monkeypatch.setattr(telemetry, "is_elevated", lambda: True)
        monkeypatch.setattr(
            agent_service.script_runner, "run",
            lambda script, timeout=300, args=None: (1, "Access denied"),
        )
        assert agent_service.install_task([]) == 1

    @pytest.mark.parametrize("subcommand,expect_uninstall", [
        ("install-task", False), ("uninstall-task", True),
    ])
    def test_both_subcommands_are_routed(self, ran, subcommand, expect_uninstall):
        agent_service.main([subcommand])
        assert ("-Uninstall" in ran[0]["args"]) is expect_uninstall

    def test_the_usage_text_mentions_them(self):
        assert "install-task" in agent_service.USAGE
        assert "uninstall-task" in agent_service.USAGE

    def test_installing_without_elevation_says_so_plainly(self, monkeypatch, capsys):
        monkeypatch.setattr(telemetry, "is_elevated", lambda: False)
        monkeypatch.setattr(
            agent_service.script_runner, "run",
            lambda *a, **k: pytest.fail("the script should not have been reached"),
        )

        assert agent_service.install_task([]) == 1
        assert "requires an elevated console" in capsys.readouterr().out

    def test_uninstalling_does_not_require_elevation_up_front(self, ran, monkeypatch):
        """Removal is delegated; the script refuses if it genuinely cannot."""
        monkeypatch.setattr(telemetry, "is_elevated", lambda: False)
        agent_service.install_task(["--uninstall"])
        assert ran, "the script should still have been invoked"
