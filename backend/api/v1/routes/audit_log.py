"""Route admin pour consulter le journal d'audit."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.api.v1.dependencies import require_role
from backend.repositories.audit_log_repository import (
    AuditLogRepository,
)
from backend.repositories.users_repository import ROLE_ADMIN


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/audit-log",
    tags=["audit"],
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)


def get_audit_log_repository() -> AuditLogRepository:
    return AuditLogRepository()


@router.get("")
def list_events(
    limit: int = Query(default=100, ge=1, le=1000),
    user_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    since: str | None = Query(
        default=None,
        description="ISO 8601 timestamp. Filtre `>= since`.",
    ),
    audit: AuditLogRepository = Depends(get_audit_log_repository),
) -> list[dict[str, Any]]:
    return audit.list_events(
        limit=limit,
        user_id=user_id,
        event_type=event_type,
        since=since,
    )
