"""Headless enrolment - the command that replaced the setup window."""

import agent_config
import enrollment
import pytest
import requests


@pytest.fixture
def server(monkeypatch, fake_response):
    """Capture the enrolment request instead of making it."""
    sent = {}

    class Session:
        def post(self, url, json=None, timeout=None):
            sent.update(url=url, json=json, timeout=timeout)
            return sent.get("response") or fake_response(200, {"token": "issued-token"})

    monkeypatch.setattr(enrollment.http_client, "build_session", lambda: Session())
    monkeypatch.setattr(enrollment.os_info, "get_version", lambda: "10.0.26100")
    monkeypatch.setattr(enrollment.os_info, "get_name", lambda: "Windows 11 Pro")
    return sent


class TestRegister:
    def test_it_saves_the_issued_token(self, server, agent_config_file):
        config = enrollment.register("https://patch.example:8000", department="Finance")

        assert config.token == "issued-token"
        assert agent_config.load().token == "issued-token"

    def test_it_identifies_the_machine(self, server, agent_config_file):
        enrollment.register("https://patch.example:8000")

        assert server["json"]["device_id"] == agent_config.DEVICE_ID
        assert server["json"]["os_version"] == "10.0.26100"
        assert server["json"]["os_name"] == "Windows 11 Pro"

    def test_it_posts_to_the_register_route(self, server, agent_config_file):
        enrollment.register("https://patch.example:8000/")
        assert server["url"] == "https://patch.example:8000/api/v1/agent/register"

    def test_the_enrolment_secret_is_sent_but_never_stored(self, server, agent_config_file):
        """Storing it would put the fleet's join credential on every endpoint."""
        enrollment.register("https://x:8000", enrollment_secret="fleet-secret")

        assert server["json"]["enrollment_secret"] == "fleet-secret"
        assert "fleet-secret" not in agent_config_file.read_text()

    def test_empty_optional_fields_are_sent_as_null_not_blank(self, server, agent_config_file):
        """The server treats absent as "unknown" and must not overwrite a
        department an admin set."""
        enrollment.register("https://x:8000")
        assert server["json"]["department"] is None
        assert server["json"]["enrollment_secret"] is None

    def test_a_hostname_override_is_honoured(self, server, agent_config_file):
        enrollment.register("https://x:8000", hostname="CUSTOM-NAME")
        assert server["json"]["hostname"] == "CUSTOM-NAME"


class TestCommandLine:
    def test_re_enrolling_against_the_same_server_succeeds_and_changes_nothing(
        self, server, agent_config_file, capsys
    ):
        agent_config.save("https://x:8000", "existing-token")

        code = enrollment.main(["--server", "https://x:8000"])

        assert code == 0
        assert "Already enrolled" in capsys.readouterr().out
        assert agent_config.load().token == "existing-token", "the old token must survive"

    def test_a_trailing_slash_does_not_look_like_a_different_server(
        self, server, agent_config_file
    ):
        agent_config.save("https://x:8000", "existing-token")
        assert enrollment.main(["--server", "https://x:8000/"]) == 0

    def test_enrolment_against_a_different_server_is_refused(
        self, server, agent_config_file, capsys
    ):

        agent_config.save("https://old:8000", "existing-token")

        code = enrollment.main(["--server", "https://new:8000"])

        assert code == 2, "distinct from both success and a transport failure"
        out = capsys.readouterr().out
        assert "already enrolled against https://old:8000" in out
        assert agent_config.load().token == "existing-token"

    def test_force_re_enrols_against_a_different_server(self, server, agent_config_file):
        agent_config.save("https://old:8000", "existing-token")

        assert enrollment.main(["--server", "https://new:8000", "--force"]) == 0
        assert agent_config.load().token == "issued-token"

    def test_force_re_enrols(self, server, agent_config_file):
        agent_config.save("https://x:8000", "existing-token")

        code = enrollment.main(["--server", "https://x:8000", "--force"])

        assert code == 0
        assert agent_config.load().token == "issued-token"

    def test_a_successful_enrolment_says_what_to_do_next(self, server, agent_config_file, capsys):
        enrollment.main(["--server", "https://x:8000"])
        out = capsys.readouterr().out

        assert "Enrolled as device" in out
        assert "install-task" in out
        assert "install_agent_task.ps1" not in out

    def test_the_server_argument_is_required(self, server, agent_config_file):
        with pytest.raises(SystemExit):
            enrollment.main([])

    def test_department_and_secret_are_forwarded(self, server, agent_config_file):
        enrollment.main([
            "--server", "https://x:8000", "--department", "HR",
            "--enrollment-secret", "s3cret",
        ])
        assert server["json"]["department"] == "HR"
        assert server["json"]["enrollment_secret"] == "s3cret"


class TestFailureMessages:
    def _fail_with(self, monkeypatch, exc):
        class Session:
            def post(self, *args, **kwargs):
                raise exc

        monkeypatch.setattr(enrollment.http_client, "build_session", lambda: Session())

    def test_a_rejected_secret_says_so(self, server, agent_config_file, monkeypatch, capsys,
                                       fake_response):
        error = requests.HTTPError("401")
        error.response = fake_response(401)
        self._fail_with(monkeypatch, error)

        assert enrollment.main(["--server", "https://x:8000"]) == 1
        assert "enrolment secret" in capsys.readouterr().out

    def test_an_unreachable_server_suggests_the_usual_causes(self, server, agent_config_file,
                                                             monkeypatch, capsys):
        self._fail_with(monkeypatch, requests.ConnectionError("refused"))

        assert enrollment.main(["--server", "https://x:8000"]) == 1
        out = capsys.readouterr().out
        assert "Could not reach the server" in out
        assert "OPENPATCH_CA_BUNDLE" in out, "TLS against an internal CA is the common trap"

    def test_a_ca_bundle_that_is_not_there_is_reported_not_raised(
        self, agent_config_file, monkeypatch, capsys, tmp_path
    ):
        monkeypatch.setenv("OPENPATCH_CA_BUNDLE", str(tmp_path / "absent.crt"))

        assert enrollment.main(["--server", "https://x:8000"]) == 1

        out = capsys.readouterr().out
        assert "absent.crt" in out
        assert "OPENPATCH_CA_BUNDLE" in out
        assert "Traceback" not in out

    def test_a_failed_enrolment_writes_no_config(self, server, agent_config_file, monkeypatch):
        self._fail_with(monkeypatch, requests.ConnectionError("refused"))
        enrollment.main(["--server", "https://x:8000"])
        assert agent_config.load() is None


class TestSecretSources:

    def test_the_flag_is_honoured(self, server, agent_config_file):
        enrollment.main(["--server", "https://x:8000", "--enrollment-secret", "from-flag"])
        assert server["json"]["enrollment_secret"] == "from-flag"

    def test_a_file_keeps_it_out_of_the_command_line(self, server, agent_config_file, tmp_path):
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("  from-file  \n", encoding="utf-8")

        enrollment.main([
            "--server", "https://x:8000", "--enrollment-secret-file", str(secret_file),
        ])

        assert server["json"]["enrollment_secret"] == "from-file", "and whitespace trimmed"

    def test_the_environment_works_too(self, server, agent_config_file, monkeypatch):
        monkeypatch.setenv(enrollment.SECRET_ENV, "from-env")
        enrollment.main(["--server", "https://x:8000"])
        assert server["json"]["enrollment_secret"] == "from-env"

    def test_an_explicit_flag_wins_over_the_environment(
        self, server, agent_config_file, monkeypatch
    ):
        monkeypatch.setenv(enrollment.SECRET_ENV, "from-env")
        enrollment.main(["--server", "https://x:8000", "--enrollment-secret", "from-flag"])
        assert server["json"]["enrollment_secret"] == "from-flag"

    def test_a_file_wins_over_the_environment(
        self, server, agent_config_file, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(enrollment.SECRET_ENV, "from-env")
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("from-file", encoding="utf-8")

        enrollment.main([
            "--server", "https://x:8000", "--enrollment-secret-file", str(secret_file),
        ])

        assert server["json"]["enrollment_secret"] == "from-file"

    def test_no_secret_anywhere_sends_none(self, server, agent_config_file):
        enrollment.main(["--server", "https://x:8000"])
        assert server["json"]["enrollment_secret"] is None

    def test_an_unreadable_secret_file_stops_rather_than_enrolling_without_it(
        self, server, agent_config_file, tmp_path
    ):

        with pytest.raises(SystemExit):
            enrollment.main([
                "--server", "https://x:8000",
                "--enrollment-secret-file", str(tmp_path / "missing.txt"),
            ])

    def test_the_secret_is_never_written_to_disk(self, server, agent_config_file, monkeypatch):
        monkeypatch.setenv(enrollment.SECRET_ENV, "fleet-secret")
        enrollment.main(["--server", "https://x:8000"])
        assert "fleet-secret" not in agent_config_file.read_text()
