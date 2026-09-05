"""Authentication for operator ("admin") routes.
"""

import hmac

import generated_secrets
from config import ADMIN_API_KEY, ADMIN_KEY_FILE
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ADMIN_HEADER = "X-Admin-Key"


def admin_key() -> str:
    """The expected admin key, generating and persisting one if needed.
    """
    return generated_secrets.resolve(ADMIN_API_KEY, ADMIN_KEY_FILE)


def describe_admin_key() -> str:
    """One line for the startup banner, so that whoever is running the server knows which key
    the dashboard needs."""
    return generated_secrets.describe(
        "Admin API key",
        ADMIN_API_KEY,
        admin_key(),
        ADMIN_KEY_FILE,
        "OPENPATCH_ADMIN_API_KEY",
    )

_bearer_scheme = HTTPBearer(auto_error=False)


def require_admin(
    x_admin_key: str | None = Header(default=None, alias=ADMIN_HEADER),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Dependency guarding every operator route.
    """
    presented = x_admin_key or (credentials.credentials if credentials else None)
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing admin credentials. Send the admin key as {ADMIN_HEADER}.",
        )

    if not hmac.compare_digest(presented, admin_key()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin key"
        )
