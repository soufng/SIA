"""Authentication routes (login + current-user introspection)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.api.v1.dependencies import (
    CurrentUser,
    get_auth_service,
    require_user,
)
from backend.core.config import settings
from backend.core.rate_limit import limiter
from backend.repositories.audit_log_repository import (
    AuditLogRepository,
    EVENT_LOGIN_FAILURE,
    EVENT_LOGIN_SUCCESS,
)
from backend.repositories.users_repository import UsersRepository
from backend.services.auth_service import (
    AuthenticationError,
    AuthService,
    OTPRequiredError,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Body for ``POST /auth/login``."""

    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=512)
    otp_code: str | None = Field(
        default=None,
        max_length=12,
        description=(
            "6-digit TOTP code from Google Authenticator. Required when "
            "SIA_OTP_ENABLED=true."
        ),
    )


class LoginResponse(BaseModel):
    """Body returned by ``POST /auth/login``."""

    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    username: str
    user_id: str
    role: str
    otp_used: bool = False


def get_audit_log_repository() -> AuditLogRepository:
    return AuditLogRepository()


def get_users_repository() -> UsersRepository:
    return UsersRepository()


def _client_ip(request: Request) -> str | None:
    fw = request.headers.get("x-forwarded-for")
    if fw:
        return fw.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/login", response_model=LoginResponse)
@limiter.limit("10/minute")
def login(
    request: Request,
    body: LoginRequest,
    service: AuthService = Depends(get_auth_service),
    audit: AuditLogRepository = Depends(get_audit_log_repository),
    users: UsersRepository = Depends(get_users_repository),
) -> LoginResponse:
    """Validate credentials and return a signed JWT.

    Two-step flow:
      1. First call with username + password only.
         * If OTP is disabled → 200 with the JWT directly.
         * If OTP is enabled → 401 with ``{"detail": "...", "requires_otp": true}``.
      2. Second call with username + password + ``otp_code`` → 200 with JWT.
    """
    try:
        token = service.login(body.username, body.password, body.otp_code)
    except OTPRequiredError as exc:
        # Structured body so the frontend can switch to the OTP step.
        # We also expose the provisioning URI here: the user has just
        # proved knowledge of the password, so showing them the QR they
        # need to enrol their authenticator app is a non-issue for our
        # threat model (single-admin scope). It enables "self-service
        # enrolment" on the first login from a new device.
        enrollment: dict[str, str] = {}
        try:
            enrollment["provisioning_uri"] = service.build_otp_provisioning_uri(
                account_override=body.username
            )
            enrollment["issuer"] = service.otp_issuer
            enrollment["account"] = service.admin_username
            enrollment["secret"] = service.otp_secret
        except Exception:  # pragma: no cover - defensive
            enrollment = {}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "message": str(exc),
                "requires_otp": True,
                **enrollment,
            },
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except AuthenticationError as exc:
        logger.info("Failed login attempt for username=%r", body.username)
        try:
            audit.append(
                event_type=EVENT_LOGIN_FAILURE,
                user_id=None,
                username=body.username,
                ip=_client_ip(request),
                details={"reason": str(exc)},
            )
        except Exception:  # pragma: no cover - audit best effort
            logger.debug("Audit append failed for login failure", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    # Audit + résolution du compte (pour le username canonique).
    canonical_username = service.admin_username
    user_id: str | None = None
    role: str = "admin"
    try:
        db_user = users.get_by_username(body.username)
        if db_user is not None:
            canonical_username = db_user.get("username") or canonical_username
            user_id = db_user.get("user_id")
            role = db_user.get("role") or role
    except Exception:  # pragma: no cover - mongo blip
        logger.debug("Could not resolve user after login", exc_info=True)
    try:
        audit.append(
            event_type=EVENT_LOGIN_SUCCESS,
            user_id=user_id,
            username=canonical_username,
            ip=_client_ip(request),
            details={"otp_used": bool(service.otp_enabled and service.otp_secret)},
        )
    except Exception:  # pragma: no cover
        logger.debug("Audit append failed for login success", exc_info=True)

    return LoginResponse(
        access_token=token,
        expires_in_minutes=service.jwt_expiry_minutes,
        username=canonical_username,
        user_id=user_id or "env-admin",
        role=role,
        otp_used=bool(service.otp_enabled and service.otp_secret),
    )


class OTPSetupResponse(BaseModel):
    """Provisioning info returned by ``GET /auth/otp/setup``."""

    enabled: bool
    issuer: str
    account: str
    secret: str
    provisioning_uri: str


@router.get("/otp/setup", response_model=OTPSetupResponse)
def otp_setup(
    user: CurrentUser = Depends(require_user),
    service: AuthService = Depends(get_auth_service),
) -> OTPSetupResponse:
    """Return the provisioning info to scan with an authenticator app.

    Protected by :func:`require_user` so anyone fishing for the secret has to
    first be authenticated. The endpoint never auto-generates a secret: the
    operator must explicitly opt in by setting ``SIA_OTP_SECRET``.
    """
    try:
        uri = service.build_otp_provisioning_uri(account_override=user.username)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from None
    return OTPSetupResponse(
        enabled=service.otp_enabled,
        issuer=service.otp_issuer,
        account=user.username,
        secret=service.otp_secret,
        provisioning_uri=uri,
    )


@router.get("/me")
def me(user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """Return the currently authenticated user.

    Useful for the frontend to verify a stored token is still valid (e.g.
    on app reload) without making a side-effecting call.
    """
    return {
        "username": user.username,
        "authenticated": user.authenticated,
        "issued_at": user.issued_at,
        "expires_at": user.expires_at,
        "auth_enabled": settings.AUTH_ENABLED,
    }
