"""The credential behind the dashboard's login page.
"""

import importlib
import inspect
import os

import pytest

ENV_KEYS = (
    "OPENPATCH_DASHBOARD_USERNAME",
    "OPENPATCH_DASHBOARD_PASSWORD",
    "OPENPATCH_DASHBOARD_PASSWORD_FILE",
)


@pytest.fixture
def configured():
    import dashboard_auth
    import generated_secrets

    saved = {key: os.environ.get(key) for key in ENV_KEYS}

    def _configure(**env):
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(env)
        module = importlib.reload(dashboard_auth)
        generated_secrets.reset_cache()
        return module

    yield _configure

    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    importlib.reload(dashboard_auth)
    generated_secrets.reset_cache()


def test_the_environment_is_what_a_deployment_sets(configured):
    auth = configured(OPENPATCH_DASHBOARD_PASSWORD="from-the-compose-file")

    assert auth.dashboard_password() == "from-the-compose-file"
    assert auth.verify("admin", "from-the-compose-file")


def test_the_environment_wins_over_a_stored_password(configured, tmp_path):
    stored = tmp_path / "dashboard_password"
    stored.write_text("stale-generated-value\n", encoding="utf-8")

    auth = configured(
        OPENPATCH_DASHBOARD_PASSWORD="chosen-by-the-operator",
        OPENPATCH_DASHBOARD_PASSWORD_FILE=str(stored),
    )

    assert auth.dashboard_password() == "chosen-by-the-operator"


def test_an_unset_password_is_generated_and_persisted(configured, tmp_path):
    stored = tmp_path / "nested" / "dashboard_password"
    auth = configured(OPENPATCH_DASHBOARD_PASSWORD_FILE=str(stored))

    generated = auth.dashboard_password()

    assert generated
    assert stored.read_text(encoding="utf-8").strip() == generated

    second = configured(OPENPATCH_DASHBOARD_PASSWORD_FILE=str(stored))
    assert second.dashboard_password() == generated


def test_the_generated_password_can_be_retyped(configured, tmp_path):
    auth = configured(
        OPENPATCH_DASHBOARD_PASSWORD_FILE=str(tmp_path / "dashboard_password")
    )

    generated = auth.dashboard_password()

    assert generated.count("-") == 3
    assert not (set(generated) & set("l1O0I")), "no character that reads as another"
    assert len(generated.replace("-", "")) >= 20


def test_the_username_is_admin_unless_renamed(configured):
    assert configured(OPENPATCH_DASHBOARD_PASSWORD="x").USERNAME == "admin"
    assert configured(
        OPENPATCH_DASHBOARD_USERNAME="operator", OPENPATCH_DASHBOARD_PASSWORD="x"
    ).USERNAME == "operator"


@pytest.mark.parametrize(
    "username,password",
    [
        ("admin", "wrong"),
        ("root", "s3cret"),          # right password, wrong account
        ("admin", ""),
        ("", ""),
        ("admin", "s3cret "),        # near miss, not a match
    ],
)
def test_a_wrong_credential_is_refused(configured, username, password):
    auth = configured(OPENPATCH_DASHBOARD_PASSWORD="s3cret")

    assert not auth.verify(username, password)


def test_surrounding_whitespace_in_the_username_is_forgiven(configured):
    auth = configured(OPENPATCH_DASHBOARD_PASSWORD="s3cret")

    assert auth.verify("  admin ", "s3cret")


def test_comparison_is_constant_time():
    import dashboard_auth

    body = inspect.getsource(dashboard_auth.verify).split('"""')[-1]
    assert body.count("compare_digest") == 2, "both halves, not just one"
    assert " == " not in body


def test_there_is_no_unauthenticated_fallback(configured, tmp_path):
    auth = configured(
        OPENPATCH_DASHBOARD_PASSWORD_FILE=str(tmp_path / "dashboard_password")
    )

    assert auth.dashboard_password()
    assert not auth.verify("admin", "")


def test_the_banner_prints_a_generated_password(configured, tmp_path):
    """Otherwise a first `docker compose up` produces a login nobody can pass."""
    auth = configured(
        OPENPATCH_DASHBOARD_PASSWORD_FILE=str(tmp_path / "dashboard_password")
    )

    banner = auth.describe_dashboard_password()

    assert auth.dashboard_password() in banner
    assert "admin" in banner


def test_the_banner_never_prints_a_configured_password(configured):
    """Copying somebody's chosen secret into the container log gains nothing:
    whoever set it already has it."""
    auth = configured(OPENPATCH_DASHBOARD_PASSWORD="chosen-by-the-operator")

    banner = auth.describe_dashboard_password()

    assert "chosen-by-the-operator" not in banner
    assert "OPENPATCH_DASHBOARD_PASSWORD" in banner


class TestChangingThePassword:

    @pytest.fixture
    def auth(self, configured, tmp_path):
        return configured(
            OPENPATCH_DASHBOARD_PASSWORD_FILE=str(tmp_path / "dashboard_password")
        )

    def test_the_new_password_works_and_the_old_one_stops(self, auth):
        original = auth.dashboard_password()

        auth.set_password(original, "a-longer-chosen-password")

        assert auth.verify("admin", "a-longer-chosen-password")
        assert not auth.verify("admin", original)

    def test_a_chosen_password_is_never_stored_in_the_clear(self, auth, tmp_path):
        auth.set_password(auth.dashboard_password(), "a-longer-chosen-password")

        stored = (tmp_path / "dashboard_password").read_text()

        assert "a-longer-chosen-password" not in stored
        assert stored.startswith("pbkdf2_sha256$")

    def test_each_password_gets_its_own_salt(self, auth):
        first = auth.hash_password("a-longer-chosen-password")
        second = auth.hash_password("a-longer-chosen-password")

        assert first != second
        assert auth._hash_matches(first, "a-longer-chosen-password")
        assert auth._hash_matches(second, "a-longer-chosen-password")

    def test_the_current_password_has_to_be_right(self, auth):
        with pytest.raises(ValueError):
            auth.set_password("not-the-current-one", "a-longer-chosen-password")

    def test_a_short_password_is_refused(self, auth):
        with pytest.raises(ValueError):
            auth.set_password(auth.dashboard_password(), "short")

    def test_reusing_the_same_password_is_refused(self, auth):
        current = auth.dashboard_password()

        with pytest.raises(ValueError):
            auth.set_password(current, current)

    def test_a_generated_password_still_verifies_after_the_change_is_available(
        self, auth
    ):
        assert auth.verify("admin", auth.dashboard_password())

    def test_an_environment_managed_password_cannot_be_changed_here(self, configured):
        auth = configured(OPENPATCH_DASHBOARD_PASSWORD="from-the-compose-file")

        with pytest.raises(auth.PasswordUnchangeable):
            auth.set_password("from-the-compose-file", "a-longer-chosen-password")

    def test_the_banner_never_prints_a_hash(self, auth):
        auth.set_password(auth.dashboard_password(), "a-longer-chosen-password")

        banner = auth.describe_dashboard_password()

        assert "pbkdf2_sha256" not in banner
        assert "a-longer-chosen-password" not in banner
        assert "set from the dashboard" in banner

    def test_a_corrupt_stored_line_lets_nobody_in(self, auth, tmp_path):
        import generated_secrets

        (tmp_path / "dashboard_password").write_text("pbkdf2_sha256$not$valid\n")
        generated_secrets.reset_cache()

        assert not auth.verify("admin", "anything")
        assert not auth.verify("admin", "")
