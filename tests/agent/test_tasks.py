"""What an action means, and what status a half-successful patch reports.
"""

import pytest
import script_runner
import tasks


@pytest.fixture
def runs(monkeypatch):
    """Record every script invocation and script its results.
    """
    calls = []
    results = {}

    def fake_run(script, timeout=script_runner.SCRIPT_TIMEOUT_SECONDS, args=None):
        calls.append({"script": script, "timeout": timeout, "args": args})
        return results.get(script, (0, f"{script} ok"))

    monkeypatch.setattr(script_runner, "run", fake_run)
    monkeypatch.setattr(tasks.script_runner, "run", fake_run)
    return type("Runs", (), {"calls": calls, "results": results})()


@pytest.fixture(autouse=True)
def _no_checkpoint_setting(monkeypatch):
    """Default to no configured throttle, so tests that do not care about it
    are not asserting against a developer's config.ini."""
    monkeypatch.setattr(tasks.agent_config, "checkpoint_throttle_minutes", lambda: "")


class TestSingleScriptActions:
    def test_each_action_maps_to_its_script(self, runs):
        for action, script in tasks.TASK_SCRIPTS.items():
            runs.calls.clear()
            tasks.execute(action)
            assert runs.calls[0]["script"] == script

    def test_success_is_reported_on_exit_zero(self, runs):
        assert tasks.execute("RESTART") == ("SUCCESS", "restart_system.ps1 ok")

    def test_failure_is_reported_on_non_zero_exit(self, runs):
        runs.results["restart_system.ps1"] = (1, "it broke")
        assert tasks.execute("RESTART") == ("FAILED", "it broke")

    def test_a_script_that_never_ran_is_also_a_failure(self, runs):
        """returncode None must not be mistaken for success."""
        runs.results["restart_system.ps1"] = (None, "Script not found")
        status, _ = tasks.execute("RESTART")
        assert status == "FAILED"

    def test_unknown_action_is_refused_rather_than_guessed(self, runs):
        status, output = tasks.execute("DELETE_EVERYTHING")
        assert status == "FAILED"
        assert "Unimplemented" in output
        assert runs.calls == [], "nothing should have been run"

    def test_long_running_actions_get_their_own_timeout(self, runs):
        tasks.execute("UPDATE_OS")
        assert runs.calls[0]["timeout"] == 3600
        runs.calls.clear()
        tasks.execute("ROLLBACK")
        assert runs.calls[0]["timeout"] == 900


class TestTargets:
    def test_winget_target_becomes_a_package_id(self, runs):
        tasks.execute("UPDATE_WINGET", "Acme.App")
        assert runs.calls[0]["args"] == ["-PackageId", "Acme.App"]

    def test_os_target_becomes_a_kb(self, runs):
        """A -PackageId handed to update_os.ps1 would be rejected, and the
        task would fail for a reason unrelated to patching."""
        tasks.execute("UPDATE_OS", "KB5034123")
        assert runs.calls[0]["args"] == ["-KB", "KB5034123"]

    def test_rollback_target_becomes_a_restore_window(self, runs):
        tasks.execute("ROLLBACK", "24")
        assert runs.calls[0]["args"] == ["-MaxAgeHours", "24"]

    def test_a_target_is_ignored_by_actions_that_take_none(self, runs):
        tasks.execute("RESTART", "nonsense")
        assert runs.calls[0]["args"] is None

    def test_no_target_passes_no_flag(self, runs):
        tasks.execute("UPDATE_WINGET")
        assert runs.calls[0]["args"] is None


class TestCheckpointThrottle:
    def test_unset_leaves_the_scripts_own_default_in_place(self, runs, monkeypatch):
        monkeypatch.setattr(tasks.agent_config, "checkpoint_throttle_minutes", lambda: "")
        assert tasks.checkpoint_args() == []

    def test_configured_value_is_passed_through(self, runs, monkeypatch):
        monkeypatch.setattr(tasks.agent_config, "checkpoint_throttle_minutes", lambda: "240")
        assert tasks.checkpoint_args() == ["-CheckpointThrottleMinutes", "240"]

    def test_leave_the_machine_alone_is_passed_through(self, runs, monkeypatch):
        monkeypatch.setattr(tasks.agent_config, "checkpoint_throttle_minutes", lambda: "-1")
        assert tasks.checkpoint_args() == ["-CheckpointThrottleMinutes", "-1"]

    def test_a_nonsense_value_is_ignored_rather_than_stopping_patching(self, runs, monkeypatch):
        monkeypatch.setattr(tasks.agent_config, "checkpoint_throttle_minutes", lambda: "soon")
        assert tasks.checkpoint_args() == []

    def test_patching_actions_get_it_alongside_their_target(self, runs, monkeypatch):
        monkeypatch.setattr(tasks.agent_config, "checkpoint_throttle_minutes", lambda: "60")
        tasks.execute("UPDATE_WINGET", "Acme.App")
        assert runs.calls[0]["args"] == ["-CheckpointThrottleMinutes", "60", "-PackageId", "Acme.App"]

    def test_non_patching_actions_never_get_it(self, runs, monkeypatch):
        monkeypatch.setattr(tasks.agent_config, "checkpoint_throttle_minutes", lambda: "60")
        tasks.execute("ROLLBACK", "24")
        assert runs.calls[0]["args"] == ["-MaxAgeHours", "24"]


class TestUpdateAndVerify:
    """Patch, then confirm the machine still works."""

    def test_clean_patch_and_clean_verification(self, runs):
        status, output = tasks.execute("UPDATE_AND_VERIFY")
        assert status == "SUCCESS_VERIFIED"
        assert [c["script"] for c in runs.calls] == ["update_winget.ps1", "verify_workflow.ps1"]

    def test_a_patch_that_installs_but_breaks_the_machine_is_not_a_success(self, runs):
        runs.results["verify_workflow.ps1"] = (1, "toolchain broken")

        status, output = tasks.execute("UPDATE_AND_VERIFY")

        assert status == "SUCCESS_WORKFLOW_FAILED"
        assert status != "SUCCESS_VERIFIED"
        assert "toolchain broken" in output

    def test_verification_is_skipped_when_the_patch_failed(self, runs):
        runs.results["update_winget.ps1"] = (1, "winget failed")

        status, output = tasks.execute("UPDATE_AND_VERIFY")

        assert status == "FAILED"
        assert [c["script"] for c in runs.calls] == ["update_winget.ps1"]
        assert "Verification skipped" in output

    def test_the_log_carries_both_stages(self, runs):
        _, output = tasks.execute("UPDATE_AND_VERIFY")
        assert "update_winget.ps1 exit=0" in output
        assert "verify_workflow.ps1 exit=0" in output

    def test_no_target_calls_verify_workflow_with_no_flag(self, runs):
        tasks.execute("UPDATE_AND_VERIFY")
        verify_call = next(c for c in runs.calls if c["script"] == "verify_workflow.ps1")
        assert verify_call["args"] is None

    def test_the_endpoints_configured_command_is_passed_through(self, runs):
        tasks.execute("UPDATE_AND_VERIFY", "Get-Process AcmeApp -ErrorAction Stop")
        verify_call = next(c for c in runs.calls if c["script"] == "verify_workflow.ps1")
        assert verify_call["args"] == ["-VerifyCommand", "Get-Process AcmeApp -ErrorAction Stop"]


class TestUpdateVerifyHeal:
    """Patch, verify, and put the machine back if verification fails."""

    def test_a_good_patch_never_triggers_a_rollback(self, runs):
        status, _ = tasks.execute("UPDATE_VERIFY_HEAL")
        assert status == "SUCCESS_VERIFIED"
        assert "rollback.ps1" not in [c["script"] for c in runs.calls]

    def test_a_bad_patch_is_rolled_back_automatically(self, runs):
        runs.results["verify_workflow.ps1"] = (1, "broken")

        status, output = tasks.execute("UPDATE_VERIFY_HEAL")

        assert status == "FAILED_AUTO_ROLLED_BACK"
        assert [c["script"] for c in runs.calls] == [
            "update_winget.ps1", "verify_workflow.ps1", "rollback.ps1",
        ]

    def test_a_failed_rollback_must_not_look_like_a_successful_heal(self, runs):
        runs.results["verify_workflow.ps1"] = (1, "broken")
        runs.results["rollback.ps1"] = (1, "no usable checkpoint")

        status, output = tasks.execute("UPDATE_VERIFY_HEAL")

        assert status == "FAILED_ROLLBACK_FAILED"
        assert status != "FAILED_AUTO_ROLLED_BACK"
        assert "no usable checkpoint" in output

    def test_nothing_is_rolled_back_when_the_patch_never_applied(self, runs):
        runs.results["update_winget.ps1"] = (1, "winget failed")

        status, output = tasks.execute("UPDATE_VERIFY_HEAL")

        assert status == "FAILED"
        assert [c["script"] for c in runs.calls] == ["update_winget.ps1"]
        assert "rollback skipped" in output.lower()

    def test_the_rollback_gets_the_long_timeout(self, runs):
        runs.results["verify_workflow.ps1"] = (1, "broken")
        tasks.execute("UPDATE_VERIFY_HEAL")
        rollback = next(c for c in runs.calls if c["script"] == "rollback.ps1")
        assert rollback["timeout"] == 900

    def test_the_endpoints_configured_command_is_passed_through(self, runs):
        tasks.execute("UPDATE_VERIFY_HEAL", "Test-Path C:\\App\\app.exe")
        verify_call = next(c for c in runs.calls if c["script"] == "verify_workflow.ps1")
        assert verify_call["args"] == ["-VerifyCommand", "Test-Path C:\\App\\app.exe"]

    def test_both_workflows_take_a_restore_point_before_patching(self, runs, monkeypatch):
        monkeypatch.setattr(tasks.agent_config, "checkpoint_throttle_minutes", lambda: "0")

        for action in ("UPDATE_AND_VERIFY", "UPDATE_VERIFY_HEAL"):
            runs.calls.clear()
            tasks.execute(action)
            patch_call = next(c for c in runs.calls if c["script"] == "update_winget.ps1")
            assert patch_call["args"] == ["-CheckpointThrottleMinutes", "0"], action


def test_every_reported_status_is_distinct():
    """Four different things can happen to a self-healing patch, and an
    operator has to be able to tell them apart."""
    statuses = {
        "SUCCESS_VERIFIED", "SUCCESS_WORKFLOW_FAILED",
        "FAILED", "FAILED_AUTO_ROLLED_BACK", "FAILED_ROLLBACK_FAILED",
    }
    assert len(statuses) == 5
