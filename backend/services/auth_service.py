"""Single-admin authentication service.

This deliberately keeps things simple for the PFE scope: there is one admin
user whose credentials live in the environment. The service abstracts the
``authenticate`` and ``issue_session_token`` operations so the route layer
stays declarative.

Design rationale
----------------
* No database for users — credentials are settings-backed, so resetting them
  is just an env-var change + restart.
* The hash is always read from settings, never embedded in code paths that
  would log it.
* Tokens use HS256 with a shared secret (``SPM_JWT_SECRET``). When the
  secret is the default placeholder, the service logs a stern warning so
  the operator notices in production.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.core.auth import (
    TokenError,
    TokenPayload,
    decode_token,
    issue_token,
    verify_password,
)
from backend.core.config import settings
from backend.core.totp import provisioning_uri, verify_code as verify_totp_code


logger = logging.getLogger(__name__)

_DEFAULT_SECRET_MARKER = "change-me-in-production"


class AuthenticationError(Exception):
    """Raised when credentials are rejected."""


class OTPRequiredError(Exception):
    """Raised after a valid username/password pair when a TOTP code is missing.

    The route layer turns this into a 401 with a structured body carrying
    ``requires_otp: true`` so the frontend knows to show the 6-digit input.
    """


@dataclass(frozen=True)
class AuthenticatedUser:
    """Subject returned by :func:`AuthService.authenticate_token`."""

    username: str
    issued_at: int
    expires_at: int


class AuthService:
    """Stateless auth helper used by the API layer."""

    def __init__(
        self,
        *,
        admin_username: str | None = None,
        admin_password_hash: str | None = None,
        jwt_secret: str | None = None,
        jwt_expiry_minutes: int | None = None,
        otp_enabled: bool | None = None,
        otp_secret: str | None = None,
        otp_issuer: str | None = None,
    ) -> None:
        self.admin_username = (admin_username or settings.AUTH_ADMIN_USERNAME).strip()
        self.admin_password_hash = (
            admin_password_hash or settings.AUTH_ADMIN_PASSWORD_HASH
        ).strip()
        self.jwt_secret = (jwt_secret or settings.AUTH_JWT_SECRET).strip()
        self.jwt_expiry_minutes = int(
            jwt_expiry_minutes or settings.AUTH_JWT_EXPIRY_MINUTES
        )
        self.otp_enabled = bool(
            settings.AUTH_OTP_ENABLED if otp_enabled is None else otp_enabled
        )
        self.otp_secret = (
            otp_secret if otp_secret is not None else settings.AUTH_OTP_SECRET
        ).strip()
        self.otp_issuer = (
            otp_issuer if otp_issuer is not None else settings.AUTH_OTP_ISSUER
        ).strip() or "SPM-CCM"
        if _DEFAULT_SECRET_MARKER in self.jwt_secret:
            logger.warning(
                "SPM_JWT_SECRET uses the default placeholder. "
                "Set a real secret in production!"
            )
        if self.otp_enabled and not self.otp_secret:
            logger.warning(
                "SPM_OTP_ENABLED=true but SPM_OTP_SECRET is empty. "
                "Generate one with `python -m backend.core.totp generate`."
            )

    def login(
        self,
        username: str,
        password: str,
        otp_code: str | None = None,
    ) -> str:
        """Verify credentials and return a signed JWT.

        Raises:
            AuthenticationError: wrong username, wrong password, wrong OTP.
                The message is intentionally opaque (no factor leak).
            OTPRequiredError: credentials are valid but OTP is enabled and
                no code was provided. The route layer converts this into a
                structured 401 so the frontend can prompt for a code.
        """
        if not isinstance(username, str) or not isinstance(password, str):
            raise AuthenticationError("Identifiants invalides")
        if not username.strip() or not password:
            raise AuthenticationError("Identifiants invalides")

        # Compare usernames in lowercase to avoid trivial typos blocking login,
        # but use a constant-time-ish check on the password regardless of the
        # username match so the response time doesn't leak which one failed.
        username_ok = username.strip().lower() == self.admin_username.lower()
        password_ok = verify_password(password, self.admin_password_hash)
        if not (username_ok and password_ok):
            raise AuthenticationError("Identifiants invalides")

        # Password OK — now consider the OTP factor if enabled.
        if self.otp_enabled and self.otp_secret:
            if not otp_code or not str(otp_code).strip():
                raise OTPRequiredError(
                    "Code de vérification à deux facteurs requis"
                )
            if not verify_totp_code(self.otp_secret, str(otp_code)):
                raise AuthenticationError("Code OTP invalide")

        return issue_token(
            subject=self.admin_username,
            secret=self.jwt_secret,
            expires_in_minutes=self.jwt_expiry_minutes,
        )

    def build_otp_provisioning_uri(self, *, account_override: str | None = None) -> str:
        """Return the ``otpauth://`` URI to scan in Google Authenticator.

        Raises:
            AuthenticationError: when OTP is disabled or the secret is empty.
        """
        if not self.otp_secret:
            raise AuthenticationError(
                "Aucun secret OTP configuré côté serveur. "
                "Génère-en un avec `python -m backend.core.totp generate` puis "
                "exporte-le via SPM_OTP_SECRET."
            )
        return provisioning_uri(
            self.otp_secret,
            account_name=(account_override or self.admin_username),
            issuer=self.otp_issuer,
        )

    def authenticate_token(self, token: str) -> AuthenticatedUser:
        """Validate a bearer token and return the matching user.

        Raises :class:`AuthenticationError` on any verification failure.
        """
        if not isinstance(token, str) or not token.strip():
            raise AuthenticationError("Token absent")
        try:
            payload: TokenPayload = decode_token(token.strip(), secret=self.jwt_secret)
        except TokenError as exc:
            # We forward the human-readable reason from the JWT layer because
            # it's already safe (no secret material in the strings).
            raise AuthenticationError(str(exc)) from None
        if payload.sub.lower() != self.admin_username.lower():
            raise AuthenticationError("Sujet du token inconnu")
        return AuthenticatedUser(
            username=payload.sub,
            issued_at=payload.iat,
            expires_at=payload.exp,
        )
