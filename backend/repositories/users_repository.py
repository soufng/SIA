"""Persistance des utilisateurs dans MongoDB.

Avant cette version, il n'y avait qu'un admin unique défini en
variables d'environnement. On garde la compat — si la collection est
vide, l'auth tombe sur l'admin env (seed automatique à la première
connexion). Une fois qu'au moins un utilisateur DB existe, la DB devient
autorité.

Rôles supportés :

- ``admin``    : tout, gestion des utilisateurs, audit log
- ``reviewer`` : upload, analyse, validation des scénarios
- ``viewer``   : lecture seule
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo.database import Database

from backend.core.config import settings
from backend.db.mongodb import get_database


logger = logging.getLogger(__name__)

USERS_COLLECTION_NAME = "users"


ROLE_ADMIN = "admin"
ROLE_REVIEWER = "reviewer"
ROLE_VIEWER = "viewer"
ALL_ROLES: tuple[str, ...] = (ROLE_ADMIN, ROLE_REVIEWER, ROLE_VIEWER)


class UserAlreadyExistsError(Exception):
    """Raised by ``create_user`` when the username is already taken."""


class UsersRepository:
    """Read/write user accounts in MongoDB."""

    def __init__(
        self,
        mongodb_url: str | None = None,
        database_name: str | None = None,
        collection_name: str = USERS_COLLECTION_NAME,
        database: Database | None = None,
    ) -> None:
        self.mongodb_url = mongodb_url or settings.MONGODB_URL
        self.database_name = database_name or settings.MONGO_DB_NAME
        self.collection_name = collection_name
        self.database = database
        # On créé l'index unique sur username au premier accès. C'est
        # ``ensure_index`` côté Mongo : idempotent et bon marché.
        self._username_index_ensured = False

    # ---------- Public API ----------

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = ROLE_VIEWER,
    ) -> dict[str, Any]:
        """Insert a new user. Raises ``UserAlreadyExistsError`` on duplicate."""
        if not username or not username.strip():
            raise ValueError("username must not be empty")
        if not password_hash or not password_hash.strip():
            raise ValueError("password_hash must not be empty")
        if role not in ALL_ROLES:
            raise ValueError(
                f"role must be one of {ALL_ROLES}, got {role!r}"
            )

        username = username.strip()
        collection = self._collection()
        if collection.find_one({"username_lower": username.lower()}):
            raise UserAlreadyExistsError(
                f"L'utilisateur '{username}' existe déjà."
            )

        document = {
            "user_id": str(uuid4()),
            "username": username,
            "username_lower": username.lower(),
            "password_hash": password_hash,
            "role": role,
            "created_at": _utcnow_iso(),
            "last_login_at": None,
            "disabled": False,
        }
        collection.insert_one(document)
        logger.info(
            "UsersRepository: created user_id=%s username=%s role=%s",
            document["user_id"],
            username,
            role,
        )
        return _serialize(document)

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        """Case-insensitive lookup by username (sans password_hash)."""
        if not username or not username.strip():
            return None
        document = self._collection().find_one(
            {"username_lower": username.strip().lower()}
        )
        return _serialize(document) if document else None

    def get_for_auth(self, username: str) -> dict[str, Any] | None:
        """Comme ``get_by_username`` mais conserve ``password_hash``.

        Réservé à l'AuthService : tout autre appelant doit utiliser
        ``get_by_username`` qui retourne une vue sans le hash.
        """
        if not username or not username.strip():
            return None
        document = self._collection().find_one(
            {"username_lower": username.strip().lower()}
        )
        return serialize_with_hash(document) if document else None

    def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        if not user_id or not user_id.strip():
            return None
        document = self._collection().find_one({"user_id": user_id})
        return _serialize(document) if document else None

    def list_users(self, limit: int = 200) -> list[dict[str, Any]]:
        cursor = (
            self._collection()
            .find({})
            .sort([("created_at", 1)])
            .limit(max(1, int(limit)))
        )
        return [_serialize(doc) for doc in cursor]

    def count(self) -> int:
        return self._collection().count_documents({})

    def update_last_login(self, user_id: str) -> None:
        if not user_id:
            return
        self._collection().update_one(
            {"user_id": user_id},
            {"$set": {"last_login_at": _utcnow_iso()}},
        )

    def set_password(self, user_id: str, password_hash: str) -> None:
        if not password_hash:
            raise ValueError("password_hash must not be empty")
        self._collection().update_one(
            {"user_id": user_id},
            {"$set": {"password_hash": password_hash}},
        )

    def set_role(self, user_id: str, role: str) -> None:
        if role not in ALL_ROLES:
            raise ValueError(f"role must be one of {ALL_ROLES}, got {role!r}")
        self._collection().update_one(
            {"user_id": user_id},
            {"$set": {"role": role}},
        )

    def disable(self, user_id: str) -> None:
        self._collection().update_one(
            {"user_id": user_id}, {"$set": {"disabled": True}}
        )

    def delete(self, user_id: str) -> bool:
        result = self._collection().delete_one({"user_id": user_id})
        return result.deleted_count > 0

    # ---------- Internals ----------

    def _collection(self):
        coll = self._get_database()[self.collection_name]
        if not self._username_index_ensured:
            try:
                coll.create_index("username_lower", unique=True)
                coll.create_index("user_id", unique=True)
                self._username_index_ensured = True
            except Exception:  # pragma: no cover - index creation tolerated
                logger.exception("UsersRepository: index creation failed")
        return coll

    def _get_database(self) -> Database:
        if self.database is not None:
            return self.database
        return get_database(self.mongodb_url, self.database_name)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _serialize(document: dict[str, Any]) -> dict[str, Any]:
    """Drop Mongo's ``_id`` AND the sensitive ``password_hash``."""
    out = dict(document)
    out.pop("_id", None)
    # On NE renvoie JAMAIS le password_hash via cette méthode publique —
    # les consommateurs API ne doivent jamais le voir. Pour l'auth, on
    # utilise une méthode dédiée ci-dessous qui le conserve.
    out.pop("password_hash", None)
    return out


def serialize_with_hash(document: dict[str, Any]) -> dict[str, Any]:
    """Variante utilisée uniquement par AuthService pour vérifier le mot de passe."""
    out = dict(document)
    out.pop("_id", None)
    return out
