"""Which API the dashboard looks for when nothing said.
"""

import os
import types

import pytest


@pytest.fixture
def default_url(app_functions, tmp_path):
    """_default_server_url, over a data directory the test controls."""
    namespace = app_functions(
        ["_default_server_url"],
        {"os": os, "paths": types.SimpleNamespace(data_dir=lambda: str(tmp_path))},
    )
    return namespace["_default_server_url"]


def _issue_ca(tmp_path):
    certs = tmp_path / "certs"
    certs.mkdir()
    (certs / "ca.crt").write_text("-----BEGIN CERTIFICATE-----\n")


def test_a_self_issued_ca_means_the_api_is_on_https(default_url, tmp_path):
    _issue_ca(tmp_path)

    assert default_url() == "https://127.0.0.1:8000"


def test_no_ca_means_it_is_not(default_url):
    """OPENPATCH_TLS_AUTO=0, or a deployment behind a proxy - nothing was
    issued, so nothing is being served with it."""
    assert default_url() == "http://127.0.0.1:8000"


def test_a_configured_host_is_used(default_url, monkeypatch):
    """A server bound to a specific address (install.ps1's LAN case) does not
    also listen on 127.0.0.1 - guessing loopback there would still be
    refused."""
    monkeypatch.setenv("OPENPATCH_HOST", "192.168.43.125")
    assert default_url() == "http://192.168.43.125:8000"


def test_a_wildcard_host_still_targets_loopback(default_url, monkeypatch):
    """0.0.0.0 means "every interface", including loopback - unlike a
    specific address, it is still reachable at 127.0.0.1."""
    monkeypatch.setenv("OPENPATCH_HOST", "0.0.0.0")
    assert default_url() == "http://127.0.0.1:8000"


def test_it_is_only_a_default(app_source):
    """An explicit OPENPATCH_SERVER_URL has to win outright: compose always
    sets one."""
    assert (
        'SERVER_URL = os.environ.get("OPENPATCH_SERVER_URL", "").strip() '
        "or _default_server_url()"
    ) in app_source
