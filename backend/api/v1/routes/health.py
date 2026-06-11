from fastapi import APIRouter, HTTPException, status
from pymongo.errors import PyMongoError

from backend.core.config import settings
from backend.db.mongodb import get_database


router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def get_health() -> dict[str, str]:
    """Return the backend health status."""
    return {"status": "ok", "message": "Backend FastAPI operationnel"}


@router.get("/mongodb")
def get_mongodb_health() -> dict[str, str]:
    """Return MongoDB connectivity status."""
    try:
        database = get_database()
        database.client.admin.command("ping")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB indisponible.",
        ) from exc

    return {"status": "ok", "database": settings.MONGO_DB_NAME}
