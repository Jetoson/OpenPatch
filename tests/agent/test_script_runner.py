
import os
import sys
import subprocess

import pytest
import script_runner


@pytest.fixture
def scripts_dir(tmp_path, monkeypatch):
    """Redirect script_path to a temporary scripts folder."""
    folder = tmp_path / "scripts"
    folder.mkdir()
    monkeypatch.setattr(script_runner, "script_path", lambda name: str(folder / name))
    return folder


def test_missing_script_reports_it_never_ran(scripts_dir):
    code, output = script_runner.run("nope.ps1")
    assert code is None
    assert "not found" in output


def test_empty_script_is_treated_as_missing(scripts_dir):
    (scripts_dir / "empty.ps1").write_text("")

    code, output = script_runner.run("empty.ps1")

    assert code is None, "an empty script must never report success"
    assert "empty" in output.lower()


def test_unreadable_script_reports_why(scripts_dir, monkeypatch):
    (scripts_dir / "x.ps1").write_text("Write-Output hi")
    monkeypatch.setattr(os.path, "getsize", lambda _: (_ for _ in ()).throw(OSError("denied")))

    code, output = script_runner.run("x.ps1")

    assert code is None
    assert "could not be read" in output


def test_timeout_reports_it_never_finished(scripts_dir, monkeypatch):
    (scripts_dir / "slow.ps1").write_text("Start-Sleep 60")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(subprocess, "run", fake_run)

    code, output = script_runner.run("slow.ps1", timeout=1)

    assert code is None
    assert "timed out" in output


def test_unexpected_failure_names_the_exception_type(scripts_dir, monkeypatch):
    (scripts_dir / "x.ps1").write_text("Write-Output hi")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))

    code, output = script_runner.run("x.ps1")

    assert code is None
    assert "OSError" in output and "boom" in output


def test_stderr_is_kept_when_stdout_is_empty(scripts_dir, monkeypatch):
    """A failure must never be logged as an empty string."""
    (scripts_dir / "x.ps1").write_text("x")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="   ", stderr="the real error"),
    )

    code, output = script_runner.run("x.ps1")

    assert code == 1
    assert output == "the real error"


def test_stdout_wins_when_both_are_present(scripts_dir, monkeypatch):
    (scripts_dir / "x.ps1").write_text("x")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="detail", stderr="noise"),
    )

    assert script_runner.run("x.ps1") == (0, "detail")


def test_arguments_are_separate_argv_entries_not_a_command_string(scripts_dir, monkeypatch):
    """A package id must not be able to break out into extra PowerShell."""
    (scripts_dir / "x.ps1").write_text("x")
    seen = {}
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **k: seen.update(cmd=cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    hostile = r"Acme.App; Remove-Item C:\ -Recurse"
    script_runner.run("x.ps1", args=["-PackageId", hostile])

    assert hostile in seen["cmd"], "the argument should be passed through verbatim"
    assert seen["cmd"].count(hostile) == 1
    assert seen["cmd"][-1] == hostile
    assert "-File" in seen["cmd"] and "-NoProfile" in seen["cmd"]


def test_execution_policy_is_bypassed_so_a_locked_down_host_still_patches(scripts_dir, monkeypatch):
    (scripts_dir / "x.ps1").write_text("x")
    seen = {}
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **k: seen.update(cmd=cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    script_runner.run("x.ps1")

    assert seen["cmd"][:4] == ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NoProfile"]


class TestTimeouts:
    def test_default_applies_to_ordinary_actions(self):
        assert script_runner.timeout_for("UPDATE_WINGET") == script_runner.SCRIPT_TIMEOUT_SECONDS

    def test_os_updates_get_an_hour(self):
        """Cumulative updates are hundreds of megabytes; the default would
        kill one partway through an install."""
        assert script_runner.timeout_for("UPDATE_OS") == 3600

    def test_rollback_gets_its_own_allowance(self):
        assert script_runner.timeout_for("ROLLBACK") == 900

    def test_unknown_action_falls_back_to_the_default(self):
        assert script_runner.timeout_for("SOMETHING_NEW") == script_runner.SCRIPT_TIMEOUT_SECONDS


class TestScriptPath:
    def test_resolved_from_the_module_not_the_working_directory(self, monkeypatch, tmp_path):
        """A scheduled task starts in the system directory, so a relative
        path would resolve there and every task would fail."""
        monkeypatch.chdir(tmp_path)
        resolved = script_runner.script_path("update_winget.ps1")

        assert os.path.isabs(resolved)
        assert resolved.endswith(os.path.join("scripts", "update_winget.ps1"))
        assert os.path.exists(resolved), "the real script should be found from any cwd"

    def test_pyinstaller_bundle_is_honoured(self, monkeypatch):
        monkeypatch.setattr(sys, "_MEIPASS", os.path.join("C:", "temp", "_MEI123"), raising=False)
        assert script_runner.script_path("x.ps1").startswith(os.path.join("C:", "temp", "_MEI123"))


@pytest.mark.skipif(sys.platform != "win32", reason="needs a real PowerShell")
def test_really_runs_powershell_end_to_end(scripts_dir):
    (scripts_dir / "real.ps1").write_text("param([string]$Note)\nWrite-Output \"got:$Note\"\nexit 3\n")

    code, output = script_runner.run("real.ps1", args=["-Note", "hello"])

    assert code == 3, "the script's own exit code must reach the caller"
    assert "got:hello" in output, "arguments must reach the script"
