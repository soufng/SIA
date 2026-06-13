"""Trace d'audit immuable des actions clés sur SIA.

Chaque entrée capture *qui* (user_id + username), *quoi* (event_type),
*sur quoi* (scenario_id ou target_id), *quand* (horodatage UTC) et
*depuis où* (IP cliente quand disponible). On ne supprime jamais d'entrée
— c'est le but. Pour la rétention long terme, faire un export + purge
contrôlée séparément (RGPD).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import DESCENDING
from pymongo.database import Database

from backend.core.config import settings
from backend.db.mongodb import get_database


logger = logging.getLogger(__name__)

AUDIT_LOG_COLLECTION_NAME = "audit_log"


# Types d'événements connus — laissez la liste extensible mais
# centralisée ici pour qu'un grep retrouve toutes les sources.
EVENT_LOGIN_SUCCESS = "login_success"
EVENT_LOGIN_FAILURE = "login_failure"
EVENT_SCENARIO_UPLOAD = "scenario_upload"
EVENT_SCENARIO_VIEW = "scenario_view"
EVENT_USER_CREATED = "user_created"
EVENT_USER_DELETED = "user_deleted"
EVENT_USER_ROLE_CHANGED = "user_role_changed"
EVENT_USER_PASSWORD_CHANGED = "user_password_changed"


class AuditLogRepository:
    """Append-only journal des actions sensibles."""

    def __init__(
        self,
        mongodb_url: str | None = None,
        database_name: str | None = None,
        collection_name: str = AUDIT_LOG_COLLECTION_NAME,
        database: Database | None = None,
    ) -> None:
        self.mongodb_url = mongodb_url or settings.MONGODB_URL
        self.database_name = database_name or settings.MONGO_DB_NAME
        self.collection_name = collection_name
        self.database = database
        self._index_ensured = False

    # ---------- Append ----------

    def append(
        self,
        *,
        event_type: str,
        user_id: str | None,
        username: str | None,
        target_id: str | None = None,
        ip: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insère une entrée. Idempotent vis-à-vis des appels concurrents."""
        if not event_type:
            raise ValueError("event_type must not be empty")

        document = {
            "event_id": str(uuid4()),
            "event_type": event_type,
            "user_id": user_id,
            "username": username,
            "target_id": target_id,
            "ip": ip,
            "details": dict(details or {}),
            "timestamp": _utcnow_iso(),
        }
        try:
            self._collection().insert_one(document)
        except Exception:
            # On NE veut PAS qu'un échec d'audit fasse échouer une vraie
            # action métier — on log et on continue.
            logger.exception(
                "AuditLog: failed to append event_type=%s user=%s",
                event_type,
                username,
            )
            return _serialize(document)
        return _serialize(document)

    # ---------- Read ----------

    def list_events(
        self,
        *,
        limit: int = 100,
        user_id: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Renvoie les événements récents (plus récent → plus ancien)."""
        query: dict[str, Any] = {}
        if user_id:
            query["user_id"] = user_id
        if event_type:
            query["event_type"] = event_type
        if since:
            query["timestamp"] = {"$gte": since}
        cursor = (
            self._collection()
            .find(query)
            .sort([("timestamp", DESCENDING)])
            .limit(max(1, int(limit)))
        )
        return [_serialize(doc) for doc in cursor]

    # ---------- Internals ----------

    def _collection(self):
        coll = self._get_database()[self.collection_name]
        if not self._index_ensured:
            try:
                coll.create_index([("timestamp", DESCENDING)])
                coll.create_index("user_id")
                coll.create_index("event_type")
                self._index_ensured = True
            except Exception:  # pragma: no cover
                logger.exception("AuditLog: index creation failed")
        return coll

    def _get_database(self) -> Database:
        if self.database is not None:
            return self.database
        # ``get_database`` reads from ``settings`` — instance-level URL /
        # name fields are only used by tests that inject ``database`` directly.
        return get_database()


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _serialize(document: dict[str, Any]) -> dict[str, Any]:
    out = dict(document)
    out.pop("_id", None)
    return out
