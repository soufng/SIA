"""Routes admin pour la gestion des utilisateurs.

Toutes les routes exigent le rôle ``admin``. Les actions sont tracées
dans le journal d'audit.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.api.v1.dependencies import CurrentUser, require_role, require_user
from backend.core.auth import hash_password
from backend.repositories.audit_log_repository import (
    AUDIT_LOG_COLLECTION_NAME,  # re-imported for clarity in IDE jump
    AuditLogRepository,
    EVENT_USER_CREATED,
    EVENT_USER_DELETED,
    EVENT_USER_PASSWORD_CHANGED,
    EVENT_USER_ROLE_CHANGED,
)
from backend.repositories.users_repository import (
    ALL_ROLES,
    ROLE_ADMIN,
    UserAlreadyExistsError,
    UsersRepository,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


# --- DI ---------------------------------------------------------------


def get_users_repository() -> UsersRepository:
    return UsersRepository()


def get_audit_log_repository() -> AuditLogRepository:
    return AuditLogRepository()


# --- Schemas ----------------------------------------------------------


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=8, max_length=512)
    role: str = Field(default="viewer")


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., min_length=1, max_length=32)


class ChangePasswordRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=512)


# --- Helpers ----------------------------------------------------------


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


# --- Routes -----------------------------------------------------------


@router.get(
    "",
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def list_users(
    users: UsersRepository = Depends(get_users_repository),
) -> list[dict[str, Any]]:
    return users.list_users(limit=500)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def create_user(
    request: Request,
    body: CreateUserRequest,
    actor: CurrentUser = Depends(require_user),
    users: UsersRepository = Depends(get_users_repository),
    audit: AuditLogRepository = Depends(get_audit_log_repository),
) -> dict[str, Any]:
    if body.role not in ALL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Rôle invalide. Valeurs autorisées : {', '.join(ALL_ROLES)}.",
        )
    try:
        user = users.create_user(
            username=body.username,
            password_hash=hash_password(body.password),
            role=body.role,
        )
    except UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    audit.append(
        event_type=EVENT_USER_CREATED,
        user_id=actor.user_id,
        username=actor.username,
        target_id=user["user_id"],
        ip=_client_ip(request),
        details={"username": user["username"], "role": user["role"]},
    )
    return user


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def delete_user(
    user_id: str,
    request: Request,
    actor: CurrentUser = Depends(require_user),
    users: UsersRepository = Depends(get_users_repository),
    audit: AuditLogRepository = Depends(get_audit_log_repository),
) -> None:
    target = users.get_by_id(user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable.",
        )
    if target["user_id"] == actor.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas supprimer votre propre compte.",
        )
    users.delete(user_id)
    audit.append(
        event_type=EVENT_USER_DELETED,
        user_id=actor.user_id,
        username=actor.username,
        target_id=user_id,
        ip=_client_ip(request),
        details={"username": target.get("username")},
    )


@router.patch(
    "/{user_id}/role",
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def update_role(
    user_id: str,
    request: Request,
    body: UpdateRoleRequest,
    actor: CurrentUser = Depends(require_user),
    users: UsersRepository = Depends(get_users_repository),
    audit: AuditLogRepository = Depends(get_audit_log_repository),
) -> dict[str, Any]:
    if body.role not in ALL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Rôle invalide. Valeurs autorisées : {', '.join(ALL_ROLES)}.",
        )
    target = users.get_by_id(user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable.",
        )
    users.set_role(user_id, body.role)
    audit.append(
        event_type=EVENT_USER_ROLE_CHANGED,
        user_id=actor.user_id,
        username=actor.username,
        target_id=user_id,
        ip=_client_ip(request),
        details={"from": target.get("role"), "to": body.role},
    )
    updated = users.get_by_id(user_id) or target
    return updated


@router.post(
    "/{user_id}/password",
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def reset_password(
    user_id: str,
    request: Request,
    body: ChangePasswordRequest,
    actor: CurrentUser = Depends(require_user),
    users: UsersRepository = Depends(get_users_repository),
    audit: AuditLogRepository = Depends(get_audit_log_repository),
) -> dict[str, bool]:
    target = users.get_by_id(user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable.",
        )
    users.set_password(user_id, hash_password(body.password))
    audit.append(
        event_type=EVENT_USER_PASSWORD_CHANGED,
        user_id=actor.user_id,
        username=actor.username,
        target_id=user_id,
        ip=_client_ip(request),
        details={"username": target.get("username")},
    )
    return {"success": True}


# Tiny re-export to silence "unused import" — we intentionally re-export
# the collection name in case a downstream module reads it from this
# router for documentation.
_ = AUDIT_LOG_COLLECTION_NAME
