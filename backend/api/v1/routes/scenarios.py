from fastapi import APIRouter, Depends

from backend.api.v1.dependencies import require_user


router = APIRouter(
    prefix="/scenarios", tags=["scenarios"], dependencies=[Depends(require_user)]
)
