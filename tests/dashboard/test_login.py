"""The dashboard refuses to show anything until somebody signs in.

"""

import math
import secrets
import types

import pytest


class FakeState(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


@pytest.fixture
def gate(login_functions):
    """The login's decision logic, over a fake session and a known password.
    """
    st = types.SimpleNamespace(session_state=FakeState())
    auth = types.SimpleNamespace(
        USERNAME="admin",
        verify=lambda username, password: (username, password) == ("admin", "right"),
    )
    clock = types.SimpleNamespace(now=1_000.0)
    namespace = login_functions(
        [
            "cooldown_remaining",
            "global_cooldown_remaining",
            "idle_seconds",
            "signed_in",
            "sign_out",
            "_accept",
            "_reject",
            "_attempt",
            # _accept issues the handle a reload is answered from.
            "issue_session",
            "session_is_live",
            "forget_session",
            "prune_sessions",
        ],
        {
            "st": st,
            "dashboard_auth": auth,
            "math": math,
            "secrets": secrets,
            # A clock the test drives, so a cooldown can be waited out
            # without the suite actually waiting.
            "time": types.SimpleNamespace(time=lambda: clock.now),
        },
    )
    namespace["clock"] = clock
    return types.SimpleNamespace(**namespace)


# --- The gate ----

def test_a_visitor_starts_signed_out(gate):
    assert not gate.signed_in()


def test_the_right_credentials_sign_you_in(gate):
    assert gate._attempt("admin", "right") is None
    assert gate.signed_in()


def test_a_wrong_password_does_not(gate):
    message = gate._attempt("admin", "wrong")

    assert message == "Incorrect username or password."
    assert not gate.signed_in()


def test_a_wrong_username_is_reported_identically(gate):
    assert gate._attempt("root", "right") == gate._attempt("admin", "wrong")


def test_signing_out_clears_the_session(gate):
    gate._attempt("admin", "right")

    gate.sign_out()

    assert not gate.signed_in()


def test_signing_in_forgets_earlier_failures(gate):
    """Otherwise a session that fumbled its password twice today would carry
    those attempts into its next mistake and be throttled early."""
    gate._attempt("admin", "wrong")
    gate._attempt("admin", "wrong")

    gate._attempt("admin", "right")
    gate.sign_out()

    assert gate._attempt("admin", "wrong") == "Incorrect username or password."


# --- Throttling -----

def test_a_typo_is_not_punished(gate):
    for _ in range(gate.FREE_ATTEMPTS - 1):
        assert gate._attempt("admin", "wrong") == "Incorrect username or password."


def test_guessing_starts_a_cooldown(gate):
    for _ in range(gate.FREE_ATTEMPTS - 1):
        gate._attempt("admin", "wrong")

    assert "Locked for" in gate._attempt("admin", "wrong")
    assert gate._attempt("admin", "wrong").startswith("Too many failed attempts")


def test_even_the_right_password_waits_out_a_cooldown(gate):
    for _ in range(gate.FREE_ATTEMPTS):
        gate._attempt("admin", "wrong")

    assert gate._attempt("admin", "right").startswith("Too many failed attempts")
    assert not gate.signed_in()


def test_the_cooldown_ends(gate):
    for _ in range(gate.FREE_ATTEMPTS):
        gate._attempt("admin", "wrong")

    gate.clock.now += gate.BASE_COOLDOWN_SECONDS + 1

    assert gate._attempt("admin", "right") is None
    assert gate.signed_in()


def test_each_further_failure_costs_more(gate):
    first = gate.cooldown_remaining(gate.FREE_ATTEMPTS, 1_000.0, 1_000.0)
    second = gate.cooldown_remaining(gate.FREE_ATTEMPTS + 1, 1_000.0, 1_000.0)

    assert first == gate.BASE_COOLDOWN_SECONDS
    assert second == first * 2


def test_the_wait_is_capped(gate):
    assert gate.cooldown_remaining(60, 1_000.0, 1_000.0) == gate.MAX_COOLDOWN_SECONDS


def test_a_countdown_never_reports_zero_while_it_is_still_running(gate):
    """Rounded up: "try again in 0s" followed by a refusal reads as a bug."""
    remaining = gate.cooldown_remaining(gate.FREE_ATTEMPTS, 1_000.0, 1_014.5)

    assert remaining == 1


class TestReconnectingDoesNotResetTheThrottle:

    def _new_session(self, gate):
        """A fresh browser connection against the same server process."""
        gate.st.session_state.clear()

    def _guess_until_the_shared_limit(self, gate):
        """Spend the shared allowance the only way it can be spent.
        """
        spent = 0
        while spent < gate.GLOBAL_FREE_ATTEMPTS:
            for _ in range(min(gate.FREE_ATTEMPTS, gate.GLOBAL_FREE_ATTEMPTS - spent)):
                gate._attempt("admin", "wrong")
                spent += 1
            self._new_session(gate)

    def test_the_count_survives_a_fresh_connection(self, gate):
        self._guess_until_the_shared_limit(gate)

        assert gate._attempt("admin", "wrong").startswith("Too many failed attempts")

    def test_even_the_right_password_waits(self, gate):
        self._guess_until_the_shared_limit(gate)

        assert gate._attempt("admin", "right").startswith("Too many failed attempts")
        assert not gate.signed_in()

    def test_a_refused_attempt_does_not_deepen_the_lockout(self, gate):
        for _ in range(gate.FREE_ATTEMPTS):
            gate._attempt("admin", "wrong")
        first = gate.cooldown_remaining(
            gate.st.session_state[gate.FAILURES],
            gate.st.session_state[gate.LAST_FAILURE],
            gate.clock.now,
        )

        for _ in range(20):
            gate._attempt("admin", "wrong")

        assert gate.cooldown_remaining(
            gate.st.session_state[gate.FAILURES],
            gate.st.session_state[gate.LAST_FAILURE],
            gate.clock.now,
        ) == first

    def test_the_shared_lockout_is_short_and_flat(self, gate):
        first = gate.global_cooldown_remaining(gate.GLOBAL_FREE_ATTEMPTS, 1_000.0, 1_000.0)
        later = gate.global_cooldown_remaining(gate.GLOBAL_FREE_ATTEMPTS * 9, 1_000.0, 1_000.0)

        assert first == later == gate.GLOBAL_COOLDOWN_SECONDS
        assert gate.GLOBAL_COOLDOWN_SECONDS <= 60

    def test_signing_in_clears_it(self, gate):
        for _ in range(gate.GLOBAL_FREE_ATTEMPTS):
            gate._attempt("admin", "wrong")
        gate.clock.now += gate.GLOBAL_COOLDOWN_SECONDS + 1
        gate._attempt("admin", "right")

        gate.sign_out()
        self._new_session(gate)

        assert gate._attempt("admin", "wrong") == "Incorrect username or password."


# --- Idle sessions -----

class TestAnIdleSessionSignsItselfOut:

    def test_a_long_gap_ends_the_session(self, gate):
        gate._attempt("admin", "right")
        assert gate.signed_in()

        gate.clock.now += gate.IDLE_TIMEOUT_SECONDS + 1

        assert not gate.signed_in()

    def test_activity_keeps_it_alive(self, gate):
        gate._attempt("admin", "right")

        for _ in range(5):
            gate.clock.now += gate.IDLE_TIMEOUT_SECONDS - 10
            assert gate.signed_in(), "each check is itself activity"

    def test_a_session_with_no_recorded_activity_is_not_expired(self, gate):
        """A missing key must never sign somebody out mid-click."""
        assert gate.idle_seconds(None, 5_000.0) == 0


# --- Wiring -----

def test_the_dashboard_gates_itself_before_it_fetches_anything(app_source):
    gate_at = app_source.index("login.require_login(")
    first_fetch = app_source.index('fetch("/dashboard/summary")')

    assert gate_at < first_fetch


def test_the_gate_stops_the_script(login_source):
    assert "st.stop()" in login_source


def test_the_browser_never_holds_a_credential(login_source):
    assert "query_params" not in login_source
    assert "dashboard_password()" not in login_source
    assert "secrets.token_urlsafe" in login_source


class TestStayingSignedInAcrossAReload:

    @pytest.fixture
    def sessions(self, login_functions):
        st = types.SimpleNamespace(session_state=FakeState())
        clock = types.SimpleNamespace(now=1_000.0)
        namespace = login_functions(
            ["issue_session", "session_is_live", "forget_session", "prune_sessions",
             "cookie_script"],
            {
                "st": st,
                "secrets": secrets,
                "time": types.SimpleNamespace(time=lambda: clock.now),
            },
        )
        namespace["clock"] = clock
        return types.SimpleNamespace(**namespace)

    def test_a_handle_is_honoured_until_it_expires(self, sessions):
        token = sessions.issue_session(1_000.0)

        assert sessions.session_is_live(token, 1_000.0)
        assert sessions.session_is_live(token, 1_000.0 + sessions.REMEMBER_SECONDS - 1)
        assert not sessions.session_is_live(
            token, 1_000.0 + sessions.REMEMBER_SECONDS + 1
        )

    def test_an_unknown_handle_is_not(self, sessions):
        assert not sessions.session_is_live("made-up", 1_000.0)
        assert not sessions.session_is_live("", 1_000.0)

    def test_signing_out_revokes_it(self, sessions):
        token = sessions.issue_session(1_000.0)

        sessions.forget_session(token)

        assert not sessions.session_is_live(token, 1_000.0)

    def test_handles_are_unguessable_and_distinct(self, sessions):
        tokens = {sessions.issue_session(1_000.0) for _ in range(20)}

        assert len(tokens) == 20
        assert all(len(t) > 30 for t in tokens)

    def test_expired_handles_do_not_pile_up(self, sessions):
        for _ in range(5):
            sessions.issue_session(1_000.0)

        sessions.prune_sessions(1_000.0 + sessions.REMEMBER_SECONDS + 1)
        surviving = sessions.issue_session(9_999.0)

        assert sessions.session_is_live(surviving, 9_999.0)

    def test_the_cookie_is_scoped_as_tightly_as_it_can_be(self, sessions):
        script = sessions.cookie_script("openpatch_session=abc", 3600)

        assert "SameSite=Strict" in script, "nothing ever links into this dashboard"
        assert "Path=/" in script
        assert "Max-Age=3600" in script
        assert "https:" in script and "Secure" in script

    def test_clearing_it_is_an_immediate_expiry(self, sessions):
        assert "Max-Age=0" in sessions.cookie_script("openpatch_session=", 0)
