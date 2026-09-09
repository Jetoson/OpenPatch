"""The .env file that should be created from .env.example
"""

import os
import sys
import pytest
import textwrap
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
SERVER = os.path.join(ROOT, "server")
AGENT = os.path.join(ROOT, "agent")


@pytest.fixture
def dotenv():
    """Write a .env for the duration of a test.
    """
    existing = None
    if os.path.exists(ENV):
        with open(ENV, encoding="utf-8") as handle:
            existing = handle.read()

    def _write(text):
        with open(ENV, "w", encoding="utf-8") as handle:
            handle.write(text)

    yield _write

    if existing is not None:
        _write(existing)
    elif os.path.exists(ENV):
        os.remove(ENV)


def run(code, cwd, env=None):
    """Run a snippet in a clean process with no inherited OPENPATCH_* values."""
    merged = {k: v for k, v in os.environ.items() if not k.startswith("OPENPATCH_")}
    merged.pop("PYTHONPATH", None)
    merged.update(env or {})
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=cwd, capture_output=True, text=True, env=merged,
    )


def server_host(cwd, env=None):
    result = run(
        f"import sys; sys.path.insert(0, {SERVER!r})\n"
        "import config\n"
        "print(config.HOST, config.PORT)\n",
        cwd=cwd, env=env,
    )
    assert result.returncode == 0, result.stderr[-400:]
    return result.stdout.split()


def elevation_check(cwd):
    return run(
        f"import sys; sys.path.insert(0, {AGENT!r})\n"
        "import agent_service, telemetry\n"
        "telemetry.is_elevated = lambda: False\n"
        "agent_service.require_elevation()\n"
        "print('STARTED')\n",
        cwd=cwd,
    )


def test_the_server_reads_it(dotenv):
    dotenv("OPENPATCH_HOST=10.9.9.9\nOPENPATCH_PORT=9443\n")
    assert server_host(SERVER) == ["10.9.9.9", "9443"]


def test_it_is_found_from_any_working_directory(dotenv):
    """The API starts from server/, the dashboard from wherever streamlit was
    launched, and the agent as a task starting in the system directory."""
    dotenv("OPENPATCH_HOST=10.9.9.9\n")
    assert server_host(ROOT)[0] == "10.9.9.9"


def test_a_real_environment_variable_wins(dotenv):
    """So a .env in a checkout can never quietly override how something is
    deployed."""
    dotenv("OPENPATCH_HOST=10.9.9.9\n")
    assert server_host(SERVER, env={"OPENPATCH_HOST": "127.0.0.1"})[0] == "127.0.0.1"


def test_no_env_file_falls_back_to_defaults(dotenv):
    if os.path.exists(ENV):
        os.remove(ENV)
    assert server_host(SERVER)[0] == "127.0.0.1"


def test_the_agent_reads_it(dotenv):
    dotenv("OPENPATCH_ALLOW_UNELEVATED=1\n")

    result = run(
        f"import sys, os; sys.path.insert(0, {AGENT!r})\n"
        "import agent_config\n"
        "print(os.environ.get('OPENPATCH_ALLOW_UNELEVATED'))\n",
        cwd=AGENT,
    )

    assert result.stdout.strip() == "1", result.stderr[-400:]


def test_the_unelevated_override_works_from_it(dotenv):
    """The setting a user actually hit this with."""
    dotenv("OPENPATCH_ALLOW_UNELEVATED=1\n")
    assert "STARTED" in elevation_check(AGENT).stdout


def test_without_it_the_agent_still_refuses(dotenv):
    dotenv("OPENPATCH_HOST=10.9.9.9\n")
    assert "STARTED" not in elevation_check(AGENT).stdout
