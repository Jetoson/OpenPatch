"""The server issuing its own TLS material.
"""

import os
import sys
import stat
import importlib

import pytest

ENV_KEYS = (
    "OPENPATCH_TLS_AUTO",
    "OPENPATCH_PUBLIC_HOST",
    "OPENPATCH_SSL_CERTFILE",
    "OPENPATCH_SSL_KEYFILE",
    "OPENPATCH_DATA_DIR",
    "OPENPATCH_TLS_DIR",
)


@pytest.fixture
def configured(tmp_path):
    """Re-import config and tls under a supplied environment.
    """
    import config
    import tls

    saved = {key: os.environ.get(key) for key in ENV_KEYS}

    def _configure(**env):
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.setdefault("OPENPATCH_DATA_DIR", str(tmp_path))
        os.environ.update(env)
        importlib.reload(config)
        return importlib.reload(tls)

    yield _configure

    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    importlib.reload(config)
    importlib.reload(tls)


class TestWhenItActs:
    def test_an_unconfigured_server_issues_its_own(self, configured, tmp_path):
        result = configured().ensure_certificate()

        assert result is not None
        assert os.path.exists(os.path.join(tmp_path, "certs", "ca.crt"))

    def test_it_can_be_turned_off(self, configured, tmp_path):
        module = configured(OPENPATCH_TLS_AUTO="0")

        assert module.ensure_certificate() is None
        assert not os.path.exists(os.path.join(tmp_path, "certs"))

    def test_the_ca_lands_where_the_dashboard_looks_for_it(self, configured, tmp_path):
        module = configured()
        module.ensure_certificate()

        # The literal the dashboard resolves: <data dir>/certs/ca.crt.
        assert os.path.exists(os.path.join(tmp_path, "certs", "ca.crt"))

    def test_a_configured_certificate_wins(self, configured, tmp_path):
        module = configured(
            OPENPATCH_TLS_AUTO="1",
            OPENPATCH_SSL_CERTFILE=str(tmp_path / "mine.crt"),
            OPENPATCH_SSL_KEYFILE=str(tmp_path / "mine.key"),
        )

        assert module.ensure_certificate() is None

    def test_it_issues_one_when_asked(self, configured, tmp_path):
        module = configured(OPENPATCH_TLS_AUTO="1")

        result = module.ensure_certificate()

        assert result is not None
        certfile, keyfile = result
        assert os.path.exists(certfile) and os.path.exists(keyfile)
        assert os.path.exists(os.path.join(tmp_path, "certs", "ca.crt"))

    def test_it_writes_into_the_data_directory(self, configured, tmp_path):
        module = configured()

        certfile, _ = module.ensure_certificate()

        assert certfile.startswith(str(tmp_path))

    def test_a_restart_reuses_what_it_issued(self, configured, tmp_path):
        module = configured()
        module.ensure_certificate()
        ca = os.path.join(tmp_path, "certs", "ca.crt")
        original = open(ca, "rb").read()

        module = configured()
        module.ensure_certificate()

        assert open(ca, "rb").read() == original


class TestTheKeysAreNotHandedAround:

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; Windows uses ACLs")
    def test_private_keys_are_owner_only(self, configured, tmp_path):
        module = configured()
        module.ensure_certificate()

        for key in ("ca.key", "server.key"):
            mode = os.stat(os.path.join(tmp_path, "certs", key)).st_mode
            assert not mode & (stat.S_IRGRP | stat.S_IROTH), key

    def test_the_keys_can_live_somewhere_the_dashboard_cannot_see(
        self, configured, tmp_path
    ):
        private = tmp_path / "tls"
        module = configured(OPENPATCH_TLS_DIR=str(private))

        certfile, keyfile = module.ensure_certificate()

        assert certfile.startswith(str(private))
        assert keyfile.startswith(str(private))
        assert not os.path.exists(os.path.join(tmp_path, "certs", "ca.key"))
        assert not os.path.exists(os.path.join(tmp_path, "certs", "server.key"))

    def test_the_public_half_still_reaches_the_dashboard(self, configured, tmp_path):
        private = tmp_path / "tls"
        module = configured(OPENPATCH_TLS_DIR=str(private))
        module.ensure_certificate()

        published = os.path.join(tmp_path, "certs", "ca.crt")
        assert os.path.exists(published)
        assert open(published, "rb").read() == open(private / "ca.crt", "rb").read()

    def test_the_copy_is_refreshed_on_every_start(self, configured, tmp_path):
        private = tmp_path / "tls"
        module = configured(OPENPATCH_TLS_DIR=str(private))
        module.ensure_certificate()
        os.remove(os.path.join(tmp_path, "certs", "ca.crt"))

        module = configured(OPENPATCH_TLS_DIR=str(private))
        module.ensure_certificate()

        assert os.path.exists(os.path.join(tmp_path, "certs", "ca.crt"))


class TestWhatItCovers:
    def test_the_names_endpoints_dial_are_included(self, configured):
        module = configured(OPENPATCH_PUBLIC_HOST="patch.corp.local,10.0.0.5")

        san = module.build_san(module.PUBLIC_HOSTS)

        assert "DNS:patch.corp.local" in san
        assert "IP:10.0.0.5" in san

    def test_addresses_and_names_are_told_apart(self, configured):
        san = configured().build_san(["10.0.0.5", "patch.corp.local"])

        assert "IP:10.0.0.5" in san and "DNS:10.0.0.5" not in san

    def test_the_compose_service_name_is_always_there(self, configured):
        """Inside compose the dashboard dials the API as "api"."""
        assert "DNS:api" in configured().build_san([])

    def test_blank_entries_are_ignored(self, configured):
        san = configured().build_san(["", "  ", "patch.corp.local"])

        assert "DNS:," not in san and "DNS:patch.corp.local" in san
