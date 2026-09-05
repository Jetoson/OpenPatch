"""Secrets the server generates for itself when they aren't configured.
"""

import os
import stat
import secrets
import contextlib

# Cached per file, so a value is generated once per process no matter how
# many callers ask
_cache: dict[str, str] = {}


def reset_cache() -> None:
    """Forget what has been resolved. For tests, which re-import these
    modules under different environments."""
    _cache.clear()


def token() -> str:
    """Returns the machine-to-machine secret."""
    return secrets.token_urlsafe(32)


def passphrase() -> str:
    """A secret a user retypes off a terminal.
    """
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"   # no l/1, no o/0
    groups = ["".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4)]
    return "-".join(groups)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _write(path: str, value: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def store(path: str, value: str) -> None:
    """Replaces a stored secret and forget what was cached for it.
    """
    _write(path, value)
    _cache[path] = value


def resolve(configured: str | None, path: str, generate=token) -> str:
    """Returns the effective secret which is either configured, stored, or generated.
    """
    if configured:
        return configured

    cached = _cache.get(path)
    if cached:
        return cached

    value = _read(path) or generate()
    if not _read(path):
        _write(path, value)
    _cache[path] = value
    return value


def describe(label: str, configured: str | None, value: str, path: str,
             variable: str, show: bool = True) -> str:
    """Returns a banner line, describing where the value came from.
    """
    if configured:
        return f"[*] {label}: from {variable}"
    if not show:
        return (
            f"[*] {label}: generated, stored in {path}\n"
            f"    Set {variable} to manage it yourself."
        )
    return (
        f"[*] {label}: {value}\n"
        f"    (generated, stored in {path}. Set {variable} to choose it "
        "yourself.)"
    )
