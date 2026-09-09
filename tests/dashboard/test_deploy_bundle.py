"""The deployment bundle the dashboard hands out.
"""

import io
import os
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server", "dashboard"))

import deploy  # noqa: E402


@pytest.fixture
def ca(tmp_path):
    path = tmp_path / "ca.crt"
    path.write_text("-----BEGIN CERTIFICATE-----\nnot-a-real-ca\n")
    return str(path)


def contents(archive_bytes):
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


class TestWhatIsInIt:
    def test_the_ca_travels_with_the_instructions(self, ca):
        files = contents(deploy.build_bundle("https://patch.corp.local:8000", ca))

        assert set(files) == {"ca.crt", "install.ps1", "README.txt"}
        assert "not-a-real-ca" in files["ca.crt"]

    def test_a_plaintext_deployment_still_gets_the_instructions(self):
        files = contents(deploy.build_bundle("http://patch.corp.local:8000", None))

        assert set(files) == {"install.ps1", "README.txt"}

    def test_the_server_address_reaches_both_files(self, ca):
        files = contents(deploy.build_bundle("https://patch.corp.local:8000", ca))

        for name in ("install.ps1", "README.txt"):
            assert "https://patch.corp.local:8000" in files[name], name

    def test_a_trailing_slash_does_not_reach_the_enrolment_command(self, ca):
        files = contents(deploy.build_bundle("https://patch.corp.local:8000/", ca))

        assert "https://patch.corp.local:8000/" not in files["install.ps1"]

    def test_no_secret_is_included(self, ca):
        files = contents(deploy.build_bundle("https://x:8000", ca))
        joined = " ".join(files.values())

        assert "OPENPATCH_ENROLLMENT_SECRET" in joined, "it says where to put it"
        assert "$env:OPENPATCH_ENROLLMENT_SECRET = \"...\"" in joined, "as a placeholder"
        assert "task_signing_secret = <value>" in joined


class TestTheInstaller:
    def test_it_refuses_to_run_unelevated(self, ca):
        script = contents(deploy.build_bundle("https://x:8000", ca))["install.ps1"]

        assert "IsInRole" in script
        assert "Administrator" in script

    def test_it_writes_an_absolute_ca_path(self, ca):
        script = contents(deploy.build_bundle("https://x:8000", ca))["install.ps1"]

        assert "$ca = Join-Path $here" in script
        assert "ca_bundle = $ca" in script

    def test_it_writes_config_ini_without_a_byte_order_mark(self, ca):
        script = contents(deploy.build_bundle("https://x:8000", ca))["install.ps1"]
        # What it runs, not what its comments mention.
        commands = [
            line for line in script.splitlines() if not line.strip().startswith("#")
        ]

        assert not any("Set-Content" in line for line in commands)
        assert "UTF8Encoding $false" in script, "the encoding that writes no mark"

    def test_it_provisions_before_enrolling(self, ca):
        script = contents(deploy.build_bundle("https://x:8000", ca))["install.ps1"]

        assert script.index("ca_bundle = $ca") < script.index("enroll --server")

    def test_it_does_not_overwrite_an_existing_configuration(self, ca):
        """That file may hold a token, a signing secret, or both."""
        script = contents(deploy.build_bundle("https://x:8000", ca))["install.ps1"]

        assert "if (-not (Test-Path $config))" in script

    def test_it_stops_when_a_step_fails(self, ca):
        script = contents(deploy.build_bundle("https://x:8000", ca))["install.ps1"]

        assert script.count("$LASTEXITCODE -ne 0") == 2

    def test_it_takes_the_secret_as_a_parameter(self, ca):
        script = contents(deploy.build_bundle("https://x:8000", ca))["install.ps1"]

        assert "param(" in script
        assert "[string]$EnrollmentSecret" in script
        assert "$EnrollmentSecret = $env:OPENPATCH_ENROLLMENT_SECRET" in script, (
            "the environment variable is the default, so it keeps working"
        )

    def test_a_failed_enrolment_says_how_to_supply_the_secret(self, ca):
        script = contents(deploy.build_bundle("https://x:8000", ca))["install.ps1"]
        failure = script.split("if ($LASTEXITCODE -ne 0)")[1]

        assert "OPENPATCH_ENROLLMENT_SECRET" in failure
        assert "-EnrollmentSecret" in failure

    def test_the_secret_is_never_put_on_the_command_line(self, ca):
        script = contents(deploy.build_bundle("https://x:8000", ca))["install.ps1"]

        assert "--enrollment-secret" not in script


class TestTheAddressIsTreatedAsCode:
    """It is interpolated into install.ps1, which an administrator runs
    elevated on every endpoint.
    """

    @pytest.mark.parametrize(
        "attempt",
        [
            'https://ok:8000"; Start-Process calc.exe; "',
            "https://ok:8000`nStart-Process calc.exe",
            "https://ok:8000$(whoami)",
            "https://ok:8000; rm -rf /",
            "https://user:pass@ok:8000",
            "file:///c:/windows",
            "not-a-url",
            "",
            "   ",
        ],
    )
    def test_anything_that_is_not_a_plain_address_is_refused(self, attempt, ca):
        with pytest.raises(deploy.UnsafeServerUrl):
            deploy.build_bundle(attempt, ca)

    def test_the_refusal_happens_in_the_builder(self, ca):
        import inspect

        assert "validate_server_url(server_url)" in inspect.getsource(deploy.build_bundle)

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("https://patch.corp.local:8000", "https://patch.corp.local:8000"),
            ("http://10.0.0.5:8000/", "http://10.0.0.5:8000"),
            ("https://patch.corp.local:8000/api/v1", "https://patch.corp.local:8000"),
            ("  https://patch.corp.local  ", "https://patch.corp.local"),
            ("https://[fe80::1]:8000", "https://[fe80::1]:8000"),
        ],
    )
    def test_a_real_address_survives_intact(self, given, expected):
        assert deploy.validate_server_url(given) == expected

    def test_the_script_it_produces_has_one_string_there(self, ca):
        script = contents(deploy.build_bundle("https://patch.corp.local:8000", ca))["install.ps1"]
        line = [x for x in script.splitlines() if x.startswith("$server")][0]

        assert line == '$server = "https://patch.corp.local:8000"'


class TestTheSuggestedAddress:

    def test_the_certificate_names_are_the_best_guess(self):
        assert deploy.suggested_url(
            public_url="", public_hosts="patch.corp.local,10.0.0.5",
            server_url="https://api:8000", port="8000",
        ) == "https://patch.corp.local:8000"

    def test_the_scheme_follows_the_api(self):
        assert deploy.suggested_url(
            public_url="", public_hosts="patch.corp.local",
            server_url="http://api:8000", port="8000",
        ).startswith("http://")

    def test_the_published_port_is_used(self):
        """The container always serves 8000."""
        assert deploy.suggested_url(
            public_url="", public_hosts="patch.corp.local",
            server_url="https://api:8000", port="9443",
        ).endswith(":9443")

    def test_an_explicit_url_wins(self):
        assert deploy.suggested_url(
            public_url="https://patch.example:443", public_hosts="other.local",
            server_url="https://api:8000", port="8000",
        ) == "https://patch.example:443"

    def test_it_falls_back_rather_than_offering_nothing(self):
        assert deploy.suggested_url(
            public_url="", public_hosts="", server_url="https://api:8000", port="8000",
        ) == "https://api:8000"

    def test_a_loopback_view_is_replaced_by_the_lan_address(self, monkeypatch):
        """https://127.0.0.1:8000 is what a server run outside compose falls
        back to.
        """
        monkeypatch.setattr(deploy, "local_ip", lambda: "192.168.1.50")
        assert deploy.suggested_url(
            public_url="", public_hosts="", server_url="https://127.0.0.1:8000", port="8000",
        ) == "https://192.168.1.50:8000"

    def test_localhost_counts_as_loopback_too(self, monkeypatch):
        monkeypatch.setattr(deploy, "local_ip", lambda: "192.168.1.50")
        assert deploy.suggested_url(
            public_url="", public_hosts="", server_url="http://localhost:8000", port="8000",
        ) == "http://192.168.1.50:8000"

    def test_a_loopback_view_survives_when_the_lan_address_cannot_be_found(self, monkeypatch):
        monkeypatch.setattr(deploy, "local_ip", lambda: None)
        assert deploy.suggested_url(
            public_url="", public_hosts="", server_url="https://127.0.0.1:8000", port="8000",
        ) == "https://127.0.0.1:8000"


class TestTheFilename:
    def test_it_names_the_fleet(self):
        assert deploy.bundle_name("https://patch.corp.local:8000") == (
            "openpatch-agent-patch.corp.local-8000.zip"
        )

    def test_it_survives_an_address_with_nothing_usable_in_it(self):
        assert deploy.bundle_name("https://").endswith(".zip")


class TestTheAgentItself:

    @pytest.fixture
    def shipped(self, tmp_path):
        payload = tmp_path / "agent-payload"
        payload.mkdir()
        (payload / "openpatch-agent.exe").write_bytes(b"MZ-not-really-an-executable")
        return deploy.agent_path(str(tmp_path))

    def test_it_travels_in_the_bundle(self, ca, shipped):
        files = contents(deploy.build_bundle("https://x:8000", ca, shipped))

        assert "openpatch-agent.exe" in files
        assert files["openpatch-agent.exe"] == "MZ-not-really-an-executable"

    def test_the_instructions_stop_asking_for_it(self, ca, shipped):
        readme = contents(deploy.build_bundle("https://x:8000", ca, shipped))["README.txt"]

        assert "already in this folder" in readme

    def test_a_source_checkout_finds_what_the_build_produced(self, tmp_path):
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "openpatch-agent.exe").write_bytes(b"MZ-just-built")

        found = deploy.agent_path(str(tmp_path / "server"), str(tmp_path))

        assert open(found, "rb").read() == b"MZ-just-built"

    def test_the_payload_directory_wins_over_dist(self, tmp_path):
        """The one an image build would ship is the deliberate choice; dist/
        is whatever was built last."""
        for where, payload in (
            (tmp_path / "packaging" / "agent-payload", b"MZ-for-the-image"),
            (tmp_path / "dist", b"MZ-just-built"),
        ):
            where.mkdir(parents=True)
            (where / "openpatch-agent.exe").write_bytes(payload)

        found = deploy.agent_path(str(tmp_path / "server"), str(tmp_path))

        assert open(found, "rb").read() == b"MZ-for-the-image"

    def test_the_image_wins_over_both(self, tmp_path):
        bundle = tmp_path / "app"
        (bundle / "agent-payload").mkdir(parents=True)
        (bundle / "agent-payload" / "openpatch-agent.exe").write_bytes(b"MZ-in-the-image")
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "openpatch-agent.exe").write_bytes(b"MZ-just-built")

        found = deploy.agent_path(str(bundle), str(tmp_path))

        assert open(found, "rb").read() == b"MZ-in-the-image"

    def test_a_container_looks_in_one_place_only(self, tmp_path):
        assert deploy.agent_candidates(str(tmp_path)) == [
            os.path.join(str(tmp_path), "agent-payload", "openpatch-agent.exe")
        ]

    def test_a_build_that_shipped_none_says_where_to_get_one(self, ca, tmp_path):
        (tmp_path / "agent-payload").mkdir()

        assert deploy.agent_path(str(tmp_path)) == ""

        readme = contents(deploy.build_bundle("https://x:8000", ca))["README.txt"]
        assert "Put openpatch-agent.exe in this folder" in readme

    def test_the_page_reports_how_old_it_is(self, app_source):
        assert "os.path.getmtime(agent)" in app_source

    def test_the_dashboard_cannot_be_talked_into_replacing_it(self):
        assert not hasattr(deploy, "store_agent")
        assert not hasattr(deploy, "stored_agent_path")
        assert not hasattr(deploy, "available_agent")

