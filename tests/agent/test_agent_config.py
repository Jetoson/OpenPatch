"""Local agent configuration: what survives a re-enrolment, and what wins."""

import pytest
import agent_config
import configparser

class TestRoundTrip:
    def test_saving_then_loading_returns_the_same_settings(self, agent_config_file):
        agent_config.save("https://patch.example:8000", "tok-1", department="Finance")

        loaded = agent_config.load()

        assert loaded.server_url == "https://patch.example:8000"
        assert loaded.token == "tok-1"
        assert loaded.department == "Finance"

    def test_a_trailing_slash_is_normalised_away(self, agent_config_file):
        agent_config.save("https://patch.example:8000/", "tok-1")
        assert agent_config.load().server_url == "https://patch.example:8000"

    def test_no_config_means_not_enrolled(self, agent_config_file):
        assert agent_config.load() is None

    def test_a_config_without_a_token_means_not_enrolled(self, agent_config_file):
        agent_config_file.write_text("[agent]\nserver_url = https://x:8000\n")
        assert agent_config.load() is None

    def test_a_config_without_a_server_means_not_enrolled(self, agent_config_file):
        agent_config_file.write_text("[agent]\ntoken = abc\n")
        assert agent_config.load() is None

    def test_the_device_id_is_derived_from_the_machine_not_the_file(self, agent_config_file):
        """A stale or hand-edited file must not be able to desync a device's
        identity from the machine it is running on."""
        agent_config.save("https://x:8000", "tok")

        assert agent_config.load().device_id == agent_config.DEVICE_ID


class TestConfigurationFromAnotherMachine:
    """A config.ini recording a different device id cannot work."""

    def _foreign(self, agent_config_file):
        """A config enrolled by one machine, sitting on another."""
        agent_config.save("https://x:8000", "tok")
        agent_config_file.write_text(
            agent_config_file.read_text().replace(agent_config.DEVICE_ID, "999999"),
            encoding="utf-8",
        )

    def test_it_is_refused_rather_than_used(self, agent_config_file):
        self._foreign(agent_config_file)
        assert agent_config.load() is None

    def test_the_explanation_names_both_causes(self, agent_config_file, capsys):
        self._foreign(agent_config_file)
        agent_config.load()

        out = capsys.readouterr().out
        assert "cloned" in out
        assert "network adapter" in out
        assert "Delete the file" in out

    def test_a_matching_device_id_is_accepted(self, agent_config_file):
        agent_config.save("https://x:8000", "tok")
        assert agent_config.load() is not None

    def test_a_config_without_a_device_id_is_still_accepted(self, agent_config_file):
        agent_config_file.write_text(
            chr(10).join(["[agent]", "server_url = https://x:8000", "token = tok"]),
            encoding="utf-8",
        )
        assert agent_config.load() is not None


class TestManualOptionsSurviveReEnrolment:
    """Re-enrolment rewrites config.ini and happens automatically on a 401.
    """

    @pytest.mark.parametrize("option", agent_config.MANUAL_OPTIONS)
    def test_each_one_is_preserved(self, agent_config_file, option):
        parser = configparser.ConfigParser()
        parser["agent"] = {
            "server_url": "https://x:8000", "device_id": agent_config.DEVICE_ID,
            "token": "tok-1", "department": "", option: "provisioned-value",
        }
        with open(agent_config_file, "w") as handle:
            parser.write(handle)

        agent_config.save("https://x:8000", "tok-2")

        assert agent_config.get_option(option) == "provisioned-value"

    def test_the_new_token_still_replaces_the_old_one(self, agent_config_file):
        agent_config.save("https://x:8000", "tok-1")
        agent_config.save("https://x:8000", "tok-2")
        assert agent_config.load().token == "tok-2"


class TestAnInterruptedWriteKeepsTheEnrolment:
    """save() replaces config.ini rather than truncating and rewriting it.
    """

    def test_the_previous_configuration_survives_a_failed_write(
        self, agent_config_file, monkeypatch
    ):
        agent_config.save("https://x:8000", "tok-1", department="Finance")

        def interrupted(*args, **kwargs):
            raise OSError("the machine went away mid-write")

        monkeypatch.setattr(agent_config.os, "replace", interrupted)
        with pytest.raises(OSError):
            agent_config.save("https://x:8000", "tok-2")

        still_there = agent_config.load()
        assert still_there.token == "tok-1"
        assert still_there.department == "Finance"

    def test_a_failed_write_leaves_no_temporary_behind(
        self, agent_config_file, monkeypatch
    ):
        """Each one holds a bearer token, so a directory slowly filling with
        half-written copies is its own problem."""
        monkeypatch.setattr(
            agent_config.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError())
        )
        with pytest.raises(OSError):
            agent_config.save("https://x:8000", "tok")

        assert list(agent_config_file.parent.iterdir()) == []

    def test_a_successful_write_leaves_only_the_configuration(self, agent_config_file):
        agent_config.save("https://x:8000", "tok")

        assert [p.name for p in agent_config_file.parent.iterdir()] == ["config.ini"]


class TestOptionPrecedence:
    def test_the_environment_wins_over_the_file(self, agent_config_file, monkeypatch):
        agent_config_file.write_text("[agent]\ntask_signing_secret = from-file\n")
        monkeypatch.setenv("OPENPATCH_TASK_SIGNING_SECRET", "from-env")

        assert agent_config.task_signing_secret() == "from-env"

    def test_the_file_is_used_when_the_environment_is_silent(self, agent_config_file):
        agent_config_file.write_text("[agent]\ntask_signing_secret = from-file\n")
        assert agent_config.task_signing_secret() == "from-file"

    def test_an_absent_setting_is_empty_not_an_error(self, agent_config_file):
        assert agent_config.task_signing_secret() == ""
        assert agent_config.checkpoint_throttle_minutes() == ""

    def test_whitespace_is_stripped(self, agent_config_file, monkeypatch):
        monkeypatch.setenv("OPENPATCH_TASK_SIGNING_SECRET", "  padded  ")
        assert agent_config.task_signing_secret() == "padded"

    def test_a_missing_file_does_not_raise(self, agent_config_file):
        assert agent_config.get_option("anything") == ""


def test_the_enrolment_secret_is_opt_in_not_issued():
    """It protects who may join the fleet, so a copy on every endpoint is a copy
    an attacker who owns one endpoint also has.
    """
    assert "enrollment_secret" in agent_config.MANUAL_OPTIONS
    assert agent_config.ENROLLMENT_SECRET_REQUIRED is True


class TestAFileWrittenByWindows:
    """config.ini arrives with a byte order mark more often than not.
    """

    def test_a_byte_order_mark_is_not_a_section_header_problem(self, agent_config_file):
        agent_config_file.write_bytes(
            "\ufeff[agent]\nserver_url = https://patch:8000\ntoken = tok\n".encode("utf-8")
        )

        loaded = agent_config.load()

        assert loaded is not None, "the mark should be stripped, not fatal"
        assert loaded.server_url == "https://patch:8000"

    def test_a_provisioned_option_survives_the_mark_too(self, agent_config_file):
        agent_config_file.write_bytes(
            ("\ufeff[agent]\nca_bundle = " + r"C:\op\ca.crt" + "\n").encode("utf-8")
        )

        assert agent_config.get_option("ca_bundle") == r"C:\op\ca.crt"

    def test_what_the_agent_writes_carries_no_mark(self, agent_config_file):
        agent_config.save("https://patch:8000", "tok")

        assert not agent_config_file.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_a_non_ascii_path_survives_the_round_trip(self, agent_config_file):
        agent_config_file.write_text(
            "[agent]\nca_bundle = " + r"C:\Zertifikate\Wächter\ca.crt" + "\n",
            encoding="utf-8",
        )

        agent_config.save("https://patch:8000", "tok")

        assert agent_config.get_option("ca_bundle") == r"C:\Zertifikate\Wächter\ca.crt"
