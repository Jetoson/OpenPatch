"""Dashboard login page credential
"""
import os
import hmac
import paths
import base64
import hashlib
import secrets

import env_file
import generated_secrets



env_file.load()

# Default username being admin it can also be set as an environment variable
USERNAME = os.environ.get("OPENPATCH_DASHBOARD_USERNAME", "").strip() or "admin"

HASH_SCHEME = "pbkdf2_sha256"
HASH_ITERATIONS = 600_000

# Minimum password length
MINIMUM_LENGTH = 12

PASSWORD = os.environ.get("OPENPATCH_DASHBOARD_PASSWORD") or None
PASSWORD_FILE = os.environ.get("OPENPATCH_DASHBOARD_PASSWORD_FILE") or os.path.join(
    paths.data_dir(), "dashboard_password"
)


def dashboard_password() -> str:
    """Returns the expected password, generating and persisting one if needed.
    """
    return generated_secrets.resolve(
        PASSWORD, PASSWORD_FILE, generate=generated_secrets.passphrase
    )


def is_hashed(stored: str) -> bool:
    return stored.startswith(HASH_SCHEME + "$")


def hash_password(password: str, salt: bytes | None = None,
                  iterations: int = HASH_ITERATIONS) -> str:
    """Returns a password at rest, as scheme$iterations$salt$digest.
    """
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join([
        HASH_SCHEME,
        str(iterations),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    ])


def _hash_matches(stored: str, password: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        expected = hashlib.pbkdf2_hmac(
            "sha256",
            (password or "").encode("utf-8"),
            base64.b64decode(salt),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, base64.b64decode(digest))


def verify(username: str, password: str) -> bool:
    """Returns whether a submitted username and password are correct.
    """
    stored = dashboard_password()
    user_ok = hmac.compare_digest((username or "").strip(), USERNAME)
    if is_hashed(stored):
        password_ok = _hash_matches(stored, password)
    else:
        password_ok = hmac.compare_digest(password or "", stored)
    return user_ok and password_ok


class PasswordUnchangeable(RuntimeError):
    """This deployment's password does not live where the dashboard can
    change it."""


def set_password(current: str, new: str) -> None:
    """Replaces the stored password, having checked the current one.
    """
    if PASSWORD:
        raise PasswordUnchangeable(
            "This deployment sets the password from OPENPATCH_DASHBOARD_PASSWORD, "
            "so it has to be changed there - a new one saved here would be "
            "overridden on the next restart."
        )
    if not verify(USERNAME, current):
        raise ValueError("The current password is not correct.")
    if len(new or "") < MINIMUM_LENGTH:
        raise ValueError(f"Use at least {MINIMUM_LENGTH} characters.")
    if new == current:
        raise ValueError("That is the password you are already using.")

    generated_secrets.store(PASSWORD_FILE, hash_password(new))


def describe_dashboard_password() -> str:
    """Returns a line for the server's startup banner."""
    if not PASSWORD and is_hashed(dashboard_password()):
        return (
            f"[*] Dashboard login ({USERNAME}): set from the dashboard, stored "
            f"hashed in\n    {PASSWORD_FILE}. Delete that file to have a new "
            "one generated."
        )
    return generated_secrets.describe(
        f"Dashboard login ({USERNAME})",
        PASSWORD,
        dashboard_password(),
        PASSWORD_FILE,
        "OPENPATCH_DASHBOARD_PASSWORD",
    )
