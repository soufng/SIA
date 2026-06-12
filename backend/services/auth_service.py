"""Multi-user authentication service (rétrocompat avec l'admin env).

Évolution depuis la v1 (un seul admin lu depuis l'env) :

* Les utilisateurs vivent maintenant en MongoDB (``users`` collection).
* L'admin env reste disponible **en fallback** quand la collection est
  vide — c'est ce qui permet d'avoir un premier accès sans cold-start
  ritual : à la première connexion réussie de l'admin env, on **seed**
  son compte dans MongoDB avec le rôle ``admin``.
* Le JWT embarque ``user_id`` et ``role`` en plus du ``sub`` (username),
  pour que ``require_role`` puisse vérifier les permissions sans
  retoucher la DB à chaque requête.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.core.auth import (
    TokenError,
    TokenPayload,
    decode_token,
    issue_token,
    verify_password,
)
from backend.core.config import settings
from backend.core.totp import provisioning_uri, verify_code as verify_totp_code
from backend.repositories.users_repository import (
    ROLE_ADMIN,
    UserAlreadyExistsError,
    UsersRepository,
)


logger = logging.getLogger(__name__)

_DEFAULT_SECRET_MARKER = "change-me-in-production"


class AuthenticationError(Exception):
    """Raised when credentials are rejected."""


class OTPRequiredError(Exception):
    """Valid username/password but OTP code missing (and OTP enabled)."""


@dataclass(frozen=True)
class AuthenticatedUser:
    """Subject returned by :func:`AuthService.authenticate_token`."""

    username: str
    user_id: str
    role: str
    issued_at: int
    expires_at: int


class AuthService:
    """Stateless auth helper used by the API layer."""

    def __init__(
        self,
        *,
        users_repository: UsersRepository | None = None,
        admin_username: str | None = None,
        admin_password_hash: str | None = None,
        jwt_secret: str | None = None,
        jwt_expiry_minutes: int | None = None,
        otp_enabled: bool | None = None,
        otp_secret: str | None = None,
        otp_issuer: str | None = None,
    ) -> None:
        self.users_repository = users_repository or UsersRepository()
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
        ).strip() or "SIA-CCM"
        if _DEFAULT_SECRET_MARKER in self.jwt_secret:
            logger.warning(
                "SIA_JWT_SECRET uses the default placeholder. "
                "Set a real secret in production!"
            )
        if self.otp_enabled and not self.otp_secret:
            logger.warning(
                "SIA_OTP_ENABLED=true but SIA_OTP_SECRET is empty. "
                "Generate one with `python -m backend.core.totp generate`."
            )

    # ---------- Login ----------

    def login(
        self,
        username: str,
        password: str,
        otp_code: str | None = None,
    ) -> str:
        """Verify credentials and return a signed JWT.

        Order of resolution :

        1. Username/password sont validés contre la collection ``users``.
        2. Si la DB ne connaît pas le compte ET que c'est l'admin env,
           on accepte ET on seed le compte en DB avec le rôle ``admin``.
        3. Sinon : ``AuthenticationError``.

        Toutes les branches renvoient le **même** message d'erreur public
        pour ne pas leaker si c'est le username ou le password qui a
        échoué.
        """
        if not isinstance(username, str) or not isinstance(password, str):
            raise AuthenticationError("Identifiants invalides")
        if not username.strip() or not password:
            raise AuthenticationError("Identifiants invalides")

        username = username.strip()

        user = self._resolve_user(username, password)
        if user is None:
            raise AuthenticationError("Identifiants invalides")

        if user.get("disabled"):
            raise AuthenticationError("Compte désactivé")

        if self.otp_enabled and self.otp_secret:
            if not otp_code or not str(otp_code).strip():
                raise OTPRequiredError(
                    "Code de vérification à deux facteurs requis"
                )
            if not verify_totp_code(self.otp_secret, str(otp_code)):
                raise AuthenticationError("Code OTP invalide")

        # Mise à jour discrète de ``last_login_at``. Si Mongo est down on
        # n'empêche pas la connexion — on aurait un effet bord
        # contre-productif.
        try:
            self.users_repository.update_last_login(user["user_id"])
        except Exception:  # pragma: no cover - mongo blip tolerated
            logger.debug("Could not update last_login_at", exc_info=True)

        return issue_token(
            subject=user["username"],
            secret=self.jwt_secret,
            expires_in_minutes=self.jwt_expiry_minutes,
            extra={
                "user_id": user["user_id"],
                "role": user.get("role") or ROLE_ADMIN,
            },
        )

    def _resolve_user(
        self, username: str, password: str
    ) -> dict[str, Any] | None:
        """Trouve l'utilisateur, vérifie le password et retourne le doc DB.

        Renvoie ``None`` si l'authentification échoue (en suivant la
        logique de seed env-admin décrite dans ``login``).
        """
        # Tentative DB-first.
        try:
            db_user = self.users_repository.get_for_auth(username)
        except Exception:  # pragma: no cover - mongo down → fallback env
            logger.warning(
                "UsersRepository.get_for_auth failed; falling back to env admin",
                exc_info=True,
            )
            db_user = None

        if db_user is not None:
            if not verify_password(password, str(db_user.get("password_hash") or "")):
                return None
            # On nettoie le hash avant de le passer en aval.
            db_user.pop("password_hash", None)
            return db_user

        # Fallback admin env, valable uniquement quand la DB est vide.
        try:
            empty_db = self.users_repository.count() == 0
        except Exception:  # pragma: no cover - mongo down
            empty_db = True

        if not empty_db:
            return None

        username_ok = username.lower() == self.admin_username.lower()
        password_ok = verify_password(password, self.admin_password_hash)
        if not (username_ok and password_ok):
            return None

        return self._seed_env_admin()

    def _seed_env_admin(self) -> dict[str, Any]:
        """Crée le compte admin DB à partir des variables d'environnement.

        Idempotent côté DB grâce à l'index unique sur ``username_lower``.
        Si la création échoue (collision pendant un seed concurrent), on
        relit le doc existant.
        """
        try:
            seeded = self.users_repository.create_user(
                username=self.admin_username,
                password_hash=self.admin_password_hash,
                role=ROLE_ADMIN,
            )
            logger.info(
                "AuthService: seeded env-admin into users collection "
                "(user_id=%s)",
                seeded.get("user_id"),
            )
            return seeded
        except UserAlreadyExistsError:
            existing = self.users_repository.get_by_username(self.admin_username)
            return existing or {
                "user_id": "env-admin",
                "username": self.admin_username,
                "role": ROLE_ADMIN,
            }
        except Exception:  # pragma: no cover - mongo down
            # En dernier recours, on renvoie un "shadow user" pour ne pas
            # bloquer la connexion admin pendant une panne Mongo.
            logger.exception("AuthService: failed to seed env-admin into DB")
            return {
                "user_id": "env-admin",
                "username": self.admin_username,
                "role": ROLE_ADMIN,
            }

    # ---------- TOTP / token ----------

    def build_otp_provisioning_uri(
        self, *, account_override: str | None = None
    ) -> str:
        if not self.otp_secret:
            raise AuthenticationError(
                "Aucun secret OTP configuré côté serveur. "
                "Génère-en un avec `python -m backend.core.totp generate` puis "
                "exporte-le via SIA_OTP_SECRET."
            )
        return provisioning_uri(
            self.otp_secret,
            account_name=(account_override or self.admin_username),
            issuer=self.otp_issuer,
        )

    def authenticate_token(self, token: str) -> AuthenticatedUser:
        """Validate a bearer token and return the matching user."""
        if not isinstance(token, str) or not token.strip():
            raise AuthenticationError("Token absent")
        try:
            payload: TokenPayload = decode_token(
                token.strip(), secret=self.jwt_secret
            )
        except TokenError as exc:
            raise AuthenticationError(str(exc)) from None

        role = str(payload.extra.get("role") or ROLE_ADMIN)
        user_id = str(payload.extra.get("user_id") or "")

        # Quand le token n'a pas de user_id (vieux JWT issus de la version
        # mono-admin), on retombe sur le bootstrap env. Pas de DB lookup
        # ici : authenticate_token tourne à chaque requête, on garde ça
        # rapide. Les permissions fines s'appuient sur ``role``.
        if not user_id:
            user_id = "env-admin"

        return AuthenticatedUser(
            username=payload.sub,
            user_id=user_id,
            role=role,
            issued_at=payload.iat,
            expires_at=payload.exp,
        )
