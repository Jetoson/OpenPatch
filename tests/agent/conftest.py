"""Agent test suite setup.
"""

import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AGENT = os.path.join(ROOT, "agent")

if AGENT not in sys.path:
    sys.path.insert(0, AGENT)


@pytest.fixture
def agent_config_file(tmp_path, monkeypatch):
    """Point agent_config at a throwaway config.ini.
    """
    import agent_config

    path = tmp_path / "config.ini"
    monkeypatch.setattr(agent_config, "CONFIG_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def _no_stray_env(monkeypatch):
    """Start every agent test from a clean OpenPatch environment.
    """
    for name in list(os.environ):
        if name.startswith("OPENPATCH_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def api(fake_session):
    """A ServerAPI wired to a recording session."""
    import server_api

    session = fake_session()
    return server_api.ServerAPI(session, "https://patch.example:8000", "dev-1", "tok-1")
