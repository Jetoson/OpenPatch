"""Starting the app the wrong way should say so.
"""

import os

import pytest


@pytest.fixture
def entrypoint():
    """Control the marker run.py sets, and put it back afterwards."""
    saved = os.environ.get("OPENPATCH_ENTRYPOINT")

    def _set(value):
        if value is None:
            os.environ.pop("OPENPATCH_ENTRYPOINT", None)
        else:
            os.environ["OPENPATCH_ENTRYPOINT"] = value

    yield _set

    _set(saved)


def test_importing_the_app_directly_is_reported(entrypoint):
    import main

    entrypoint(None)
    warning = main.warn_if_started_directly()

    assert "run.py" in warning
    assert "migrations" in warning
    assert "TLS certificate" in warning


def test_starting_it_properly_says_nothing(entrypoint):
    import main

    entrypoint("run.py")

    assert main.warn_if_started_directly() == ""


def test_run_py_sets_the_marker():
    """Read from the source rather than by running main(), which would serve.
    """
    import inspect

    import run

    source = inspect.getsource(run.main)
    assert 'os.environ["OPENPATCH_ENTRYPOINT"] = "run.py"' in source
    assert source.index("OPENPATCH_ENTRYPOINT") < source.index("serve(")


def test_the_projects_own_launcher_uses_the_entry_point():
    """This repository shipped a launch configuration with the exact mistake
    the warning is about."""
    import json

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, ".claude", "launch.json"), encoding="utf-8") as handle:
        launcher = json.load(handle)

    api = next(c for c in launcher["configurations"] if c["name"] == "openpatch-api")
    invocation = " ".join(api["runtimeArgs"])

    # What it runs, not what its comments mention.
    assert "run.py" in invocation
    assert "uvicorn" not in invocation
