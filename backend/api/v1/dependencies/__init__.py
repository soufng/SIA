"""FastAPI dependencies shared across v1 routes.

Re-exports :func:`require_user` and :class:`CurrentUser` for routes that
need to guard themselves with bearer-token authentication.
"""

from backend.api.v1.dependencies.auth import (
    CurrentUser,
    get_auth_service,
    require_user,
)

__all__ = ["CurrentUser", "get_auth_service", "require_user"]
