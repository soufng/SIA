from __future__ import annotations

from pymongo import MongoClient
from pymongo.database import Database

from backend.core.config import settings


_client: MongoClient | None = None


def get_mongodb_client() -> MongoClient:
    """Return a process-wide MongoDB client."""
    global _client

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
        _client.close()
        _client = None
