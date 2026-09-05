"""The dashboard's sign-in page.
    username   "admin" by default (OPENPATCH_DASHBOARD_USERNAME)
    password   OPENPATCH_DASHBOARD_PASSWORD, or generated on first start and
               printed by the server
"""

import os
import sys
import math
import time
import base64
import secrets
import functools

import streamlit as st
import theme

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard_auth  # noqa: E402
import paths  # noqa: E402

# Session-state keys
SIGNED_IN = "_op_signed_in"
FAILURES = "_op_login_failures"
LAST_FAILURE = "_op_login_last_failure"
LAST_SEEN = "_op_last_seen"

# Attempts allowed before a doubling delay begins.
FREE_ATTEMPTS = 5
BASE_COOLDOWN_SECONDS = 15
MAX_COOLDOWN_SECONDS = 300

# The same, shared across every session at once
GLOBAL_FREE_ATTEMPTS = 10
GLOBAL_COOLDOWN_SECONDS = 30

# Checked on the next interaction rather than expired by a timer, since
# Streamlit has none
IDLE_TIMEOUT_SECONDS = 1800

# Process-wide, not per-IP
_global_failures = 0
_global_last_failure: float | None = None

# Staying signed in across a reload
COOKIE_NAME = "openpatch_session"
SESSION_TOKEN = "_op_session_token"
CLEAR_COOKIE = "_op_clear_cookie"

REMEMBER_SECONDS = 12 * 60 * 60
_sessions: dict = {}


def cooldown_remaining(failures: int, last_failure_at: float | None, now: float) -> int:
    """Seconds this session must wait before another attempt is accepted.
    """
    if failures < FREE_ATTEMPTS or not last_failure_at:
        return 0
    delay = min(
        BASE_COOLDOWN_SECONDS * 2 ** (failures - FREE_ATTEMPTS),
        MAX_COOLDOWN_SECONDS,
    )
    remaining = delay - (now - last_failure_at)
    return math.ceil(remaining) if remaining > 0 else 0


@functools.lru_cache(maxsize=1)
def _logo_data_url() -> str:
    """The openpatch logo, inlined.
    """
    path = os.path.join(paths.dashboard_assets_dir(), "logo_mark.png")
    try:
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{encoded}"


def global_cooldown_remaining(failures: int, last_failure_at: float | None, now: float) -> int:
    """Seconds every session must wait."""
    if failures < GLOBAL_FREE_ATTEMPTS or not last_failure_at:
        return 0
    remaining = GLOBAL_COOLDOWN_SECONDS - (now - last_failure_at)
    return math.ceil(remaining) if remaining > 0 else 0


def idle_seconds(last_seen: float | None, now: float) -> float:
    """How long this session has been idle.
    """
    return 0.0 if not last_seen else max(0.0, now - last_seen)


def issue_session(now: float) -> str:
    """A handle for the browser to present after a reload."""
    prune_sessions(now)
    token = secrets.token_urlsafe(32)
    _sessions[token] = now + REMEMBER_SECONDS
    return token


def session_is_live(token: str, now: float) -> bool:
    """Whether a handle names a session this process still honours.
    """
    if not token:
        return False
    expiry = _sessions.get(token)
    if expiry is None:
        return False
    if expiry <= now:
        _sessions.pop(token, None)
        return False
    return True


def forget_session(token: str) -> None:
    _sessions.pop(token or "", None)


def prune_sessions(now: float) -> None:
    for token in [t for t, expiry in _sessions.items() if expiry <= now]:
        _sessions.pop(token, None)


def cookie_script(value: str, max_age: int) -> str:
    """A component that writes the cookie on the page that hosts it.
    """
    return (
        "<script>\n"
        "  var secure = parent.location.protocol === 'https:' ? '; Secure' : '';\n"
        f"  parent.document.cookie = {value!r} + '; Path=/; Max-Age={max_age}"
        "; SameSite=Strict' + secure;\n"
        "</script>"
    )


COOKIE_FRAME_HEIGHT = 1


def remember(token: str) -> None:
    st.iframe(
        cookie_script(f"{COOKIE_NAME}={token}", REMEMBER_SECONDS),
        height=COOKIE_FRAME_HEIGHT,
    )


def forget_cookie() -> None:
    st.iframe(cookie_script(f"{COOKIE_NAME}=", 0), height=COOKIE_FRAME_HEIGHT)


def presented_token() -> str:
    """The handle this browser sent.
    """
    try:
        return st.context.cookies.get(COOKIE_NAME, "") or ""
    except Exception:
        return ""


def restore_from_cookie() -> bool:
    """Sign this session in from a live handle. Returns whether it worked."""
    token = presented_token()
    if not session_is_live(token, time.time()):
        return False

    st.session_state[SIGNED_IN] = True
    st.session_state[SESSION_TOKEN] = token
    st.session_state[LAST_SEEN] = time.time()
    return True


def signed_in() -> bool:
    """Returns whether this session is authenticated."""
    if not st.session_state.get(SIGNED_IN):
        return False

    if idle_seconds(st.session_state.get(LAST_SEEN), time.time()) > IDLE_TIMEOUT_SECONDS:
        sign_out()
        return False

    st.session_state[LAST_SEEN] = time.time()
    return True


def sign_out() -> None:
    """Runs as a widget callback, so it only clears state.
    """
    forget_session(st.session_state.get(SESSION_TOKEN, ""))
    for key in (SIGNED_IN, FAILURES, LAST_FAILURE, LAST_SEEN, SESSION_TOKEN):
        st.session_state.pop(key, None)
    st.session_state[CLEAR_COOKIE] = True


def _accept() -> None:
    global _global_failures, _global_last_failure

    st.session_state[SIGNED_IN] = True
    st.session_state[LAST_SEEN] = time.time()
    st.session_state[SESSION_TOKEN] = issue_session(time.time())
    st.session_state.pop(FAILURES, None)
    st.session_state.pop(LAST_FAILURE, None)
    _global_failures = 0
    _global_last_failure = None


def _reject() -> None:
    global _global_failures, _global_last_failure

    st.session_state[FAILURES] = st.session_state.get(FAILURES, 0) + 1
    st.session_state[LAST_FAILURE] = time.time()
    _global_failures += 1
    _global_last_failure = time.time()


def _attempt(username: str, password: str) -> str | None:
    """Handle one submission. Returns the message to show, or None on success.
    """
    waiting = max(
        cooldown_remaining(
            st.session_state.get(FAILURES, 0),
            st.session_state.get(LAST_FAILURE),
            time.time(),
        ),
        global_cooldown_remaining(_global_failures, _global_last_failure, time.time()),
    )
    if waiting:
        return f"Too many failed attempts. Try again in {waiting}s."

    if dashboard_auth.verify(username, password):
        _accept()
        return None

    _reject()
    waiting = max(
        cooldown_remaining(
            st.session_state[FAILURES], st.session_state[LAST_FAILURE], time.time()
        ),
        global_cooldown_remaining(_global_failures, _global_last_failure, time.time()),
    )
    if waiting:
        return f"Incorrect username or password. Locked for {waiting}s."
    return "Incorrect username or password."


def _note_html() -> str:
    """Describes where the password comes from & how to retrieve it again.
    """
    return (
        '<div class="op-login-note">'
        "The password is <code>OPENPATCH_DASHBOARD_PASSWORD</code>, or the one "
        "generated & printed on the console on the first run "
        "Use the command (<code>docker compose logs api</code>) to retrieve it"
        "</div>"
    )


def render(server_url: str) -> None:
    """Draw the signed-out page."""
    st.markdown(theme.login_css(), unsafe_allow_html=True)

    if st.session_state.pop(CLEAR_COOKIE, False):
        forget_cookie()

    _, middle, _ = st.columns([1, 1.25, 1])
    with middle:
        with st.container(key="op_login_card"):
            st.markdown(
                theme.login_header(_logo_data_url(), server_url), unsafe_allow_html=True
            )

            with st.form("op_login_form", clear_on_submit=False, border=False):
                username = st.text_input(
                    "Username",
                    value=dashboard_auth.USERNAME,
                    autocomplete="username",
                    key="op_login_username",
                )
                password = st.text_input(
                    "Password",
                    type="password",
                    placeholder="Enter your password",
                    autocomplete="current-password",
                    key="op_login_password",
                )
                submitted = st.form_submit_button(
                    "Sign in", type="primary", width="stretch"
                )

            if submitted:
                message = _attempt(username, password)
                if message is None:
                    st.rerun()
                kind = "wait" if message.startswith("Too many") else "error"
                st.markdown(theme.login_alert(message, kind), unsafe_allow_html=True)

        st.markdown(_note_html(), unsafe_allow_html=True)


def render_password_change() -> None:
    with st.popover("\U0001F511 Change password", width="stretch"):
        if dashboard_auth.PASSWORD:
            st.info(
                "This deployment sets the password from "
                "`OPENPATCH_DASHBOARD_PASSWORD`, so it has to be changed "
                "there.",
                icon="ℹ️",
            )
            return

        with st.form("op_change_password", clear_on_submit=False, border=False):
            current = st.text_input("Current password", type="password")
            new = st.text_input("New password", type="password")
            again = st.text_input("New password again", type="password")
            st.caption(f"At least {dashboard_auth.MINIMUM_LENGTH} characters.")
            submitted = st.form_submit_button(
                "Save", type="primary", width="stretch"
            )

        if not submitted:
            return

        if new != again:
            st.error("The two new passwords do not match.", icon="\U0001F6AB")
            return

        try:
            dashboard_auth.set_password(current, new)
        except (ValueError, dashboard_auth.PasswordUnchangeable) as refusal:
            st.error(str(refusal), icon="\U0001F6AB")
            return
        st.success("Password changed. It takes effect at the next sign-in.")


def require_login(server_url: str) -> None:
    """Gate the page. Renders the login and stops the script unless signed in.
    """
    if signed_in():
        remember(st.session_state[SESSION_TOKEN])
        return

    if restore_from_cookie():
        remember(st.session_state[SESSION_TOKEN])
        return

    render(server_url)
    st.stop()
