"""Bearer-token authentication dependency for v1 routes.

When ``SPM_AUTH_ENABLED=false`` the dependency short-circuits and returns
a synthetic anonymous user so the existing dev workflow keeps working
without changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from backend.core.config import settings
from backend.services.auth_service import (
    AuthenticatedUser,
    AuthenticationError,
    AuthService,
)


_ANONYMOUS = AuthenticatedUser(username="anonymous", issued_at=0, expires_at=0)


@dataclass(frozen=True)
class CurrentUser:
    """User returned by :func:`require_user`.

    Two flavours coexist:
      * ``CurrentUser(authenticated=True,  username="admin", ...)`` — real session
      * ``CurrentUser(authenticated=False, username="anonymous", ...)`` — auth disabled
    """

    username: str
    authenticated: bool
    issued_at: int
    expires_at: int


def get_auth_service() -> AuthService:
    """Default DI factory — overridable in tests via ``dependency_overrides``."""
    return AuthService()


def require_user(
    authorization: str | None = Header(default=None),
    service: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    """Validate the ``Authorization: Bearer …`` header and return the user.

    When auth is globally disabled via ``SPM_AUTH_ENABLED=false`` the
    dependency returns a synthetic anonymous user — no header is required.
    """
    if not settings.AUTH_ENABLED:
        return CurrentUser(
            username=_ANONYMOUS.username,
            authenticated=False,
            issued_at=_ANONYMOUS.issued_at,
            expires_at=_ANONYMOUS.expires_at,
        )

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    try:
        user = service.authenticate_token(token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    return CurrentUser(
        username=user.username,
        authenticated=True,
        issued_at=user.issued_at,
        expires_at=user.expires_at,
    )
