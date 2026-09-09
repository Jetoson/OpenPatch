"""The one session the agent uses, and what it verifies against.
"""

import ssl
import sys

import agent_paths
import http_client
import pytest


@pytest.fixture(autouse=True)
def _no_bundled_ca(monkeypatch):
    """Start from a build that carries no CA of its own.
    """
    monkeypatch.setattr(agent_paths, "bundled_ca_path", lambda: "")


class TestTheCaBundle:
    def test_the_environment_is_used_when_set(self, monkeypatch):
        """An internal CA can be provisioned by whatever manages the
        endpoint, without writing to config.ini."""
        monkeypatch.setenv("OPENPATCH_CA_BUNDLE", r"C:\ProgramData\OpenPatch\ca.crt")

        assert http_client.ca_bundle() == r"C:\ProgramData\OpenPatch\ca.crt"

    def test_config_ini_is_the_fallback(self, agent_config_file):
        agent_config_file.write_text("[agent]\nca_bundle = C:\\certs\\ca.crt\n")

        assert http_client.ca_bundle() == "C:\\certs\\ca.crt"

    def test_no_bundle_configured_is_empty_not_an_error(self, agent_config_file):
        assert http_client.ca_bundle() == ""


class TestTheCaBuiltIntoTheExecutable:
    """A CA baked in at build time, which is how the root of trust reaches an
    endpoint without a second distribution step.
    """

    @pytest.fixture
    def baked_in(self, tmp_path, monkeypatch):
        path = tmp_path / "ca.crt"
        path.write_text("-----BEGIN CERTIFICATE-----\n")
        monkeypatch.setattr(agent_paths, "bundled_ca_path", lambda: str(path))
        return str(path)

    def test_it_is_used_when_nothing_is_provisioned(self, baked_in, agent_config_file):
        assert http_client.ca_bundle() == baked_in
        assert http_client.build_session().verify == baked_in

    def test_it_is_a_pin_like_any_other(self, baked_in, agent_config_file):
        """Trusting the machine store as well would give back exactly what
        naming a CA was meant to exclude."""
        session = http_client.build_session()

        mounted = session.get_adapter("https://patch.example:8000")
        assert not isinstance(mounted, http_client.SystemTrustAdapter)

    def test_the_environment_overrides_it_without_a_rebuild(
        self, baked_in, tmp_path, monkeypatch
    ):
        """An endpoint moved to another server, or a fleet whose CA was
        rotated, must not be waiting on a new executable."""
        provisioned = tmp_path / "other-ca.crt"
        provisioned.write_text("-----BEGIN CERTIFICATE-----\n")
        monkeypatch.setenv("OPENPATCH_CA_BUNDLE", str(provisioned))

        assert http_client.ca_bundle() == str(provisioned)

    def test_the_banner_says_it_came_from_the_build(self, baked_in, agent_config_file):
        """The path is inside a temporary extraction directory, so printing
        it would send an operator looking for a file that is not there."""
        banner = http_client.describe_tls("https://patch.example:8000")

        assert "built into this agent" in banner
        assert baked_in not in banner


class TestBuildSession:
    def test_a_configured_bundle_is_what_the_session_verifies_against(
        self, tmp_path, monkeypatch
    ):
        bundle = tmp_path / "ca.crt"
        bundle.write_text("-----BEGIN CERTIFICATE-----\n")
        monkeypatch.setenv("OPENPATCH_CA_BUNDLE", str(bundle))

        assert http_client.build_session().verify == str(bundle)

    def test_a_configured_bundle_is_the_only_thing_trusted(
        self, tmp_path, monkeypatch
    ):
        """Naming a CA is a pin, and a pin that quietly also accepts every
        root on the machine is not one.
        """
        bundle = tmp_path / "ca.crt"
        bundle.write_text("-----BEGIN CERTIFICATE-----\n")
        monkeypatch.setenv("OPENPATCH_CA_BUNDLE", str(bundle))

        session = http_client.build_session()

        mounted = session.get_adapter("https://patch.example:8000")
        assert not isinstance(mounted, http_client.SystemTrustAdapter)

    def test_without_a_bundle_the_machine_store_is_consulted(self, agent_config_file):
        """An organisation that distributes its internal CA the normal
        Windows way - Group Policy, into the machine's root store - should
        not also have to set an OpenPatch-specific variable on every
        endpoint."""
        session = http_client.build_session()

        mounted = session.get_adapter("https://patch.example:8000")
        assert isinstance(mounted, http_client.SystemTrustAdapter)

    def test_a_bundle_that_is_not_there_stops_the_agent(self, tmp_path, monkeypatch):
        """Named at startup."""
        monkeypatch.setenv("OPENPATCH_CA_BUNDLE", str(tmp_path / "absent.crt"))

        with pytest.raises(FileNotFoundError) as failure:
            http_client.build_session()

        assert "absent.crt" in str(failure.value)

    def test_verification_is_never_switched_off(self, agent_config_file):
        assert http_client.build_session().verify is not False


class TestTheStartupBanner:
    def test_plain_http_is_called_out(self):
        assert "not enabled" in http_client.describe_tls("http://patch.example:8000")

    def test_a_configured_bundle_is_named(self, monkeypatch, tmp_path):
        bundle = tmp_path / "ca.crt"
        monkeypatch.setenv("OPENPATCH_CA_BUNDLE", str(bundle))

        assert str(bundle) in http_client.describe_tls("https://patch.example:8000")

    def test_the_default_names_both_sources(self, agent_config_file):
        banner = http_client.describe_tls("https://patch.example:8000")

        assert "certificate store" in banner
        assert "certifi" in banner
        assert "OPENPATCH_CA_BUNDLE" in banner


class TestTheSystemTrustContext:
    def test_it_verifies_and_checks_hostnames(self):
        context = http_client.system_trust_context()

        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True

    def test_certifi_is_loaded_on_top_of_the_machine_store(self):
        import certifi

        machine_only = ssl.create_default_context()
        combined = http_client.system_trust_context()

        certifi_roots = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        certifi_roots.load_verify_locations(cafile=certifi.where())

        assert len(combined.get_ca_certs()) >= len(certifi_roots.get_ca_certs())
        if sys.platform == "win32":
            assert len(combined.get_ca_certs()) > len(machine_only.get_ca_certs())
