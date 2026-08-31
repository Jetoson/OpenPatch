"""A script runner which runs the powershell scripts under ./scripts directory.
"""

import os
import subprocess
import agent_paths


SCRIPT_TIMEOUT_SECONDS = 300

# Actions which might take longer than the default timeout.
ACTION_TIMEOUTS = {
    "UPDATE_OS": 3600,
    "ROLLBACK": 900,
}


def timeout_for(action: str) -> int:
    """Returns the timeout for the given action."""
    return ACTION_TIMEOUTS.get(action, SCRIPT_TIMEOUT_SECONDS)


def script_path(name: str) -> str:
    """Returns the absolute path for the given script."""
    return os.path.join(agent_paths.scripts_dir(), name)


def run(script_name: str, timeout: int = SCRIPT_TIMEOUT_SECONDS, args: list | None = None):
    """Runs and returns the output of a script."""
    resolved = script_path(script_name)
    if not os.path.exists(resolved):
        return None, f"Script not found at path: {resolved}"

    try:
        if os.path.getsize(resolved) == 0:
            return None, f"Script is empty: {resolved}"
    except OSError as exc:
        return None, f"Script could not be read: {resolved} ({exc})"

    try:
        result = subprocess.run(
            [
                "powershell.exe", "-ExecutionPolicy", "Bypass", "-NoProfile",
                "-File", resolved, *(args or []),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",   # since winget and Windows Update print non-ASCII names
            errors="replace",
            timeout=timeout,
        )
        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return None, f"{script_name} timed out after {timeout} seconds."
    except Exception as exc:
        return None, f"{script_name} could not be run: {type(exc).__name__}: {exc}"
