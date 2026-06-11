from fastapi import APIRouter, Depends

from backend.api.v1.dependencies import require_user


router = APIRouter(
    prefix="/moderation", tags=["moderation"], dependencies=[Depends(require_user)]
)
