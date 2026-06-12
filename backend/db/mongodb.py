from __future__ import annotations

from pymongo import MongoClient
from pymongo.database import Database

from backend.core.config import settings


_client: MongoClient | None = None


def _is_client_usable(client: MongoClient) -> bool:
    """Return ``False`` if the client has been closed (lifespan shutdown,
    hot-reload, explicit ``close()``). PyMongo 4.x exposes the closed
    state via the topology's ``_closed`` flag; we also fall back to a
    ``__del__``-safe attribute probe so a partially-finalised client is
    treated as unusable."""
    topology = getattr(client, "_topology", None)
    if topology is None:
        return False
    if getattr(topology, "_closed", False):
        return False
    return True


def get_mongodb_client() -> MongoClient:
    """Return a process-wide MongoDB client.

    Recreates the client transparently if the previous one was closed
    (typically by ``close_mongodb_client`` during a hot-reload cycle).
    Without this guard, the next request would surface PyMongo's
    "Cannot send a request, as the client has been closed" error.
    """
    global _client

    if _client is not None and not _is_client_usable(_client):
        _client = None

    if _client is None:
        _client = MongoClient(
            settings.MONGODB_URL,
            serverSelectionTimeoutMS=5000,
        )

    return _client


def get_database() -> Database:
    """Return the configured MongoDB database."""
    return get_mongodb_client()[settings.MONGO_DB_NAME]


def close_mongodb_client() -> None:
    """Close the process-wide MongoDB client on application shutdown."""
    global _client

    if _client is not None:
        try:
            _client.close()
        except Exception:
            # Already half-closed by a concurrent reload — nothing to do.
            pass
        _client = None
