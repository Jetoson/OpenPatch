"""The contract between the Python agent and the PowerShell it runs.

"""

import os
import shutil
import subprocess

import tasks
import pytest
import script_runner

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "agent", "scripts",
)


def source(name):
    with open(os.path.join(SCRIPTS, name), encoding="utf-8") as handle:
        return handle.read()


def test_every_mapped_action_has_a_script_on_disk():
    for action, script in tasks.TASK_SCRIPTS.items():
        assert os.path.exists(os.path.join(SCRIPTS, script)), f"{action} -> {script}"


def test_no_script_is_empty():
    empty = [
        name for name in os.listdir(SCRIPTS)
        if name.endswith(".ps1") and os.path.getsize(os.path.join(SCRIPTS, name)) == 0
    ]
    assert empty == []


@pytest.mark.parametrize("action,flag", sorted(tasks.TARGET_FLAGS.items()))
def test_each_target_flag_is_a_parameter_the_script_declares(action, flag):
    script = tasks.TASK_SCRIPTS[action]
    parameter = flag.lstrip("-")
    assert f"${parameter}" in source(script), f"{script} must declare -{parameter}"


@pytest.mark.parametrize("script", ["update_winget.ps1", "update_os.ps1"])
def test_patching_scripts_accept_the_checkpoint_throttle(script):
    """The agent passes -CheckpointThrottleMinutes to both; a script that did
    not declare it would reject the whole invocation."""
    assert "$CheckpointThrottleMinutes" in source(script)


@pytest.mark.parametrize("script", ["update_winget.ps1", "update_os.ps1"])
def test_patching_scripts_take_a_restore_point(script):
    assert "New-OpenPatchCheckpoint" in source(script)
    assert "_restore_point.ps1" in source(script)


class TestRollbackFailsClosed:
    def test_it_refuses_without_elevation(self):
        assert "ROLLBACK ABORTED: the agent is not elevated" in source("rollback.ps1")

    def test_it_only_targets_a_checkpoint_this_agent_created(self):
        assert 'Description -eq $CheckpointDescription' in source("rollback.ps1")

    def test_it_refuses_a_checkpoint_older_than_the_window(self):
        assert "predates the update" in source("rollback.ps1")

    def test_the_window_is_a_parameter_with_a_conservative_default(self):
        assert "[int]$MaxAgeHours = 6" in source("rollback.ps1")


class TestRestorePointHelper:
    def test_it_puts_the_machine_policy_back(self):
        helper = source("_restore_point.ps1")
        assert "finally" in helper
        assert "Restore-RestorePointThrottle" in helper

    def test_it_removes_the_value_when_it_did_not_exist(self):
        assert "Remove-ItemProperty" in source("_restore_point.ps1")

    def test_it_does_not_trust_checkpoint_computer_reporting_success(self):
        assert "$after -ne $before" in source("_restore_point.ps1")


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="needs PowerShell")
@pytest.mark.parametrize("name", sorted(
    f for f in os.listdir(SCRIPTS) if f.endswith(".ps1")
))
def test_every_script_parses(name):
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-Command",
            "$e = $null; "
            f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{os.path.join(SCRIPTS, name)}', [ref]$null, [ref]$e); "
            "if ($e) { $e | ForEach-Object { $_.ToString() }; exit 1 } else { exit 0 }",
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"{name} does not parse:\n{result.stdout}"


class TestVerifyWorkflow:
    def test_it_declares_the_flag_the_agent_passes(self):
        assert "$VerifyCommand" in source("verify_workflow.ps1")

    @pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="needs PowerShell")
    def test_nothing_configured_passes(self):
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", os.path.join(SCRIPTS, "verify_workflow.ps1"),
            ],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="needs PowerShell")
    def test_a_generated_critical_programs_command_passes_when_all_are_running(self):
        generated = (
            "$names = @('powershell'); "
            "$missing = @($names | Where-Object { -not (Get-Process -Name $_ -ErrorAction SilentlyContinue) }); "
            "if ($missing.Count) { throw ('Not running: ' + ($missing -join ', ')) }"
        )
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", os.path.join(SCRIPTS, "verify_workflow.ps1"),
                "-VerifyCommand", generated,
            ],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="needs PowerShell")
    def test_a_generated_critical_programs_command_fails_on_a_missing_one(self):
        generated = (
            "$names = @('powershell', 'OpenPatchPytestNoSuchProcess'); "
            "$missing = @($names | Where-Object { -not (Get-Process -Name $_ -ErrorAction SilentlyContinue) }); "
            "if ($missing.Count) { throw ('Not running: ' + ($missing -join ', ')) }"
        )
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", os.path.join(SCRIPTS, "verify_workflow.ps1"),
                "-VerifyCommand", generated,
            ],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1
        assert "OpenPatchPytestNoSuchProcess" in result.stdout

    @pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="needs PowerShell")
    def test_a_command_that_succeeds_passes(self):
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", os.path.join(SCRIPTS, "verify_workflow.ps1"),
                "-VerifyCommand", f"Get-Process -Id {os.getpid()} | Out-Null",
            ],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="needs PowerShell")
    def test_a_command_that_raises_fails(self):
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", os.path.join(SCRIPTS, "verify_workflow.ps1"),
                "-VerifyCommand", "Get-Process -Name OpenPatchPytestNoSuchProcess",
            ],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1

    @pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="needs PowerShell")
    def test_a_nonzero_native_exit_code_fails(self):
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", os.path.join(SCRIPTS, "verify_workflow.ps1"),
                "-VerifyCommand", "cmd.exe /c exit 3",
            ],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1


def test_the_timeout_table_covers_the_slow_actions():
    assert script_runner.timeout_for("UPDATE_OS") > script_runner.SCRIPT_TIMEOUT_SECONDS
    assert script_runner.timeout_for("ROLLBACK") > script_runner.SCRIPT_TIMEOUT_SECONDS


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="needs PowerShell")
def test_the_restore_point_throttle_is_saved_and_restored(tmp_path):
    """Exercised here against a scratch key under HKCU, so the
    machine's actual System Restore policy is never touched.
    """
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        f". '{os.path.join(SCRIPTS, '_restore_point.ps1')}'\n"
        r"$script:RestoreKey = 'HKCU:\Software\OpenPatchPytest\SystemRestore'" "\n"
        r"Remove-Item -Path 'HKCU:\Software\OpenPatchPytest' -Recurse -Force -EA SilentlyContinue" "\n"
        "if ($null -ne (Get-RestorePointThrottle)) { Write-Output 'FAIL absent-should-be-null'; exit 1 }\n"
        "Set-RestorePointThrottle -Minutes 0\n"
        "if ((Get-RestorePointThrottle) -ne 0) { Write-Output 'FAIL set-zero'; exit 1 }\n"
        "$kind = (Get-Item $script:RestoreKey).GetValueKind('SystemRestorePointCreationFrequency')\n"
        "if ($kind -ne 'DWord') { Write-Output \"FAIL not-dword:$kind\"; exit 1 }\n"
        "Restore-RestorePointThrottle -Previous 1440\n"
        "if ((Get-RestorePointThrottle) -ne 1440) { Write-Output 'FAIL restore-value'; exit 1 }\n"
        "Restore-RestorePointThrottle -Previous $null\n"
        "if ($null -ne (Get-RestorePointThrottle)) { Write-Output 'FAIL should-be-removed'; exit 1 }\n"
        r"Remove-Item -Path 'HKCU:\Software\OpenPatchPytest' -Recurse -Force -EA SilentlyContinue" "\n"
        "Write-Output 'OK'\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
        capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr
