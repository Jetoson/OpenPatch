"""The two secrets shared with agents, and the fact that they now exist.
"""

import os
import importlib

import pytest

ENV_KEYS = (
    "OPENPATCH_ENROLLMENT_SECRET",
    "OPENPATCH_ENROLLMENT_SECRET_FILE",
    "OPENPATCH_ENROLLMENT_OPEN",
    "OPENPATCH_TASK_SIGNING_SECRET",
    "OPENPATCH_TASK_SIGNING_SECRET_FILE",
    "OPENPATCH_DATA_DIR",
)


@pytest.fixture
def configured(tmp_path):
    """Re-import config and fleet_secrets under a supplied environment."""
    import config
    import generated_secrets
    from api.services import fleet_secrets

    saved = {key: os.environ.get(key) for key in ENV_KEYS}

    def _configure(**env):
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.setdefault("OPENPATCH_DATA_DIR", str(tmp_path))
        os.environ.update(env)
        generated_secrets.reset_cache()
        importlib.reload(config)
        return importlib.reload(fleet_secrets)

    yield _configure

    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    generated_secrets.reset_cache()
    importlib.reload(config)
    importlib.reload(fleet_secrets)


class TestTheEnrolmentSecret:
    def test_an_unconfigured_server_generates_one(self, configured, tmp_path):
        module = configured()

        secret = module.enrollment_secret()

        assert secret
        assert (tmp_path / "enrollment_secret").read_text().strip() == secret

    def test_it_survives_a_restart(self, configured):
        """A value invented per process would stop every rollout already
        holding the old one."""
        first = configured().enrollment_secret()

        assert configured().enrollment_secret() == first

    def test_the_environment_wins(self, configured):
        module = configured(OPENPATCH_ENROLLMENT_SECRET="chosen-by-the-operator")

        assert module.enrollment_secret() == "chosen-by-the-operator"

    def test_enrolment_can_be_opened_on_purpose(self, configured, tmp_path):
        module = configured(OPENPATCH_ENROLLMENT_OPEN="1")

        assert module.enrollment_secret() == ""
        assert not (tmp_path / "enrollment_secret").exists(), "nothing to generate"

    def test_opening_it_is_announced_as_the_risk_it_is(self, configured):
        banner = configured(OPENPATCH_ENROLLMENT_OPEN="1").describe_enrollment_secret()

        assert "OPEN" in banner
        assert "can enrol a device" in banner


class TestTheTaskSigningSecret:
    def test_an_unconfigured_server_generates_one(self, configured, tmp_path):
        module = configured()

        secret = module.task_signing_secret()

        assert secret
        assert (tmp_path / "task_signing_secret").read_text().strip() == secret

    def test_it_survives_a_restart(self, configured):
        first = configured().task_signing_secret()

        assert configured().task_signing_secret() == first

    def test_the_environment_wins(self, configured):
        module = configured(OPENPATCH_TASK_SIGNING_SECRET="shared-with-the-fleet")

        assert module.task_signing_secret() == "shared-with-the-fleet"

    def test_the_banner_says_the_agents_need_it_too(self, configured):
        banner = configured().describe_task_signing_secret()

        assert "Every agent needs this same value" in banner


class TestTheyAreDistinct:
    def test_one_secret_is_not_reused_for_the_other(self, configured):
        module = configured()

        assert module.enrollment_secret() != module.task_signing_secret()

    def test_neither_is_the_admin_key(self, configured):
        module = configured()
        from api.services import admin_auth

        assert module.enrollment_secret() != admin_auth.admin_key()
        assert module.task_signing_secret() != admin_auth.admin_key()
