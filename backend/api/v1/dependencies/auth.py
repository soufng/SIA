"""Bearer-token authentication dependency for v1 routes.

When ``SIA_AUTH_ENABLED=false`` the dependency short-circuits and returns
a synthetic anonymous user so the existing dev workflow keeps working
without changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from backend.core.config import settings
from backend.repositories.users_repository import ROLE_ADMIN
from backend.services.auth_service import (
    AuthenticationError,
    AuthService,
)


@dataclass(frozen=True)
class CurrentUser:
    """User returned by :func:`require_user`.

    Two flavours coexist:
      * ``CurrentUser(authenticated=True,  username="admin", role="admin", ...)`` — real session
      * ``CurrentUser(authenticated=False, username="anonymous", role="admin", ...)`` — auth disabled
    """

    username: str
    authenticated: bool
    issued_at: int
    expires_at: int
    user_id: str = "env-admin"
    role: str = ROLE_ADMIN


def get_auth_service() -> AuthService:
    """Default DI factory — overridable in tests via ``dependency_overrides``."""
    return AuthService()


def require_user(
    authorization: str | None = Header(default=None),
    service: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    """Validate the ``Authorization: Bearer …`` header and return the user.

    When auth is globally disabled via ``SIA_AUTH_ENABLED=false`` the
    dependency returns a synthetic anonymous user — no header is required.
    """
    if not settings.AUTH_ENABLED:
        return CurrentUser(
            username="anonymous",
            authenticated=False,
            issued_at=0,
            expires_at=0,
            user_id="anonymous",
            role=ROLE_ADMIN,
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
        user_id=user.user_id,
        role=user.role,
    )


def require_role(*allowed: str):
    """Dépendance FastAPI qui restreint l'accès à certains rôles.

    Usage::

        @router.get("/admin", dependencies=[Depends(require_role("admin"))])
        def admin_only(): ...

        @router.get("/edit", dependencies=[Depends(require_role("admin", "reviewer"))])
        def edit(): ...

    Quand ``SIA_AUTH_ENABLED=false`` la dépendance laisse passer
    (utile pour le dev local).
    """
    allowed_set = {str(r).strip().lower() for r in allowed if r}

    def _checker(user: CurrentUser = Depends(require_user)) -> CurrentUser:
        if not settings.AUTH_ENABLED:
            return user
        if user.role.lower() not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Privilèges insuffisants. Rôle requis : "
                    + ", ".join(sorted(allowed_set))
                ),
            )
        return user

    return _checker
