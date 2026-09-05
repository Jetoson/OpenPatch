import hashlib
import secrets


def generate_token() -> str:
    """Generates a random enrollment token, shown to the caller exactly once (at
    registration)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hash used both to store and to look up tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
