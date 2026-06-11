"""Tests pour /api/v1/users (admin) et /api/v1/audit-log (admin)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.v1.dependencies.auth import CurrentUser, require_user
from backend.api.v1.routes.audit_log import (
    get_audit_log_repository as get_audit_log_repo_for_audit_route,
)
from backend.api.v1.routes.audit_log import router as audit_router
from backend.api.v1.routes.users import (
    get_audit_log_repository as get_audit_log_repo_for_users_route,
)
from backend.api.v1.routes.users import (
    get_users_repository,
    router as users_router,
)
from backend.repositories.users_repository import (
    ROLE_ADMIN,
    ROLE_REVIEWER,
    ROLE_VIEWER,
    UserAlreadyExistsError,
)


# ---- Fakes ----


class FakeUsersRepo:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def list_users(self, limit: int = 200) -> list[dict[str, Any]]:
        # Renvoie sans password_hash.
        return [
            {k: v for k, v in d.items() if k != "password_hash"}
            for d in self.docs
        ][:limit]

    def create_user(
        self, *, username: str, password_hash: str, role: str = ROLE_VIEWER
    ) -> dict[str, Any]:
        if any(d["username"].lower() == username.lower() for d in self.docs):
            raise UserAlreadyExistsError(f"user exists: {username}")
        doc = {
            "user_id": str(uuid4()),
            "username": username,
            "role": role,
            "password_hash": password_hash,
            "disabled": False,
        }
        self.docs.append(doc)
        return {k: v for k, v in doc.items() if k != "password_hash"}

    def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        for d in self.docs:
            if d["user_id"] == user_id:
                return {k: v for k, v in d.items() if k != "password_hash"}
        return None

    def delete(self, user_id: str) -> bool:
        for i, d in enumerate(self.docs):
            if d["user_id"] == user_id:
                del self.docs[i]
                return True
        return False

    def set_role(self, user_id: str, role: str) -> None:
        for d in self.docs:
            if d["user_id"] == user_id:
                d["role"] = role
                return

    def set_password(self, user_id: str, password_hash: str) -> None:
        for d in self.docs:
            if d["user_id"] == user_id:
                d["password_hash"] = password_hash
                return


class FakeAuditRepo:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, **fields: Any) -> dict[str, Any]:
        event = {**fields, "event_id": str(uuid4())}
        self.events.append(event)
        return event

    def list_events(
        self,
        *,
        limit: int = 100,
        user_id: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        out = self.events
        if user_id:
            out = [e for e in out if e.get("user_id") == user_id]
        if event_type:
            out = [e for e in out if e.get("event_type") == event_type]
        return out[:limit]


def build_client(
    actor_role: str = ROLE_ADMIN,
    users_repo: FakeUsersRepo | None = None,
    audit_repo: FakeAuditRepo | None = None,
) -> tuple[TestClient, FakeUsersRepo, FakeAuditRepo]:
    app = FastAPI()
    app.include_router(users_router)
    app.include_router(audit_router)

    users_repo = users_repo or FakeUsersRepo()
    audit_repo = audit_repo or FakeAuditRepo()

    def _actor() -> CurrentUser:
        return CurrentUser(
            username="bob",
            authenticated=True,
            issued_at=0,
            expires_at=9999999999,
            user_id="actor-id",
            role=actor_role,
        )

    app.dependency_overrides[require_user] = _actor
    app.dependency_overrides[get_users_repository] = lambda: users_repo
    app.dependency_overrides[get_audit_log_repo_for_users_route] = lambda: audit_repo
    app.dependency_overrides[get_audit_log_repo_for_audit_route] = lambda: audit_repo

    return TestClient(app), users_repo, audit_repo


# ---- /users ----


def test_admin_can_list_users() -> None:
    client, users_repo, _ = build_client(actor_role=ROLE_ADMIN)
    users_repo.create_user(
        username="zoe", password_hash="hashed", role=ROLE_REVIEWER
    )
    resp = client.get("/users")
    assert resp.status_code == 200
    body = resp.json()
    assert any(u["username"] == "zoe" for u in body)
    # Aucun password_hash leaké.
    assert all("password_hash" not in u for u in body)


def test_non_admin_cannot_list_users(monkeypatch) -> None:
    # Le conftest global désactive AUTH_ENABLED — on le rétablit pour
    # vérifier explicitement le garde-fou de rôle.
    from backend.core import config as cfg
    monkeypatch.setattr(cfg.settings, "AUTH_ENABLED", True, raising=False)

    client, _, _ = build_client(actor_role=ROLE_REVIEWER)
    resp = client.get("/users")
    assert resp.status_code == 403


def test_admin_can_create_user_and_audit_logs_it() -> None:
    client, users_repo, audit_repo = build_client(actor_role=ROLE_ADMIN)
    resp = client.post(
        "/users",
        json={
            "username": "newbie",
            "password": "Sup3rSecret!",
            "role": ROLE_VIEWER,
        },
    )
    assert resp.status_code == 201
    assert any(d["username"] == "newbie" for d in users_repo.docs)
    assert any(e["event_type"] == "user_created" for e in audit_repo.events)


def test_create_user_rejects_invalid_role() -> None:
    client, _, _ = build_client(actor_role=ROLE_ADMIN)
    resp = client.post(
        "/users",
        json={"username": "x", "password": "Sup3rSecret!", "role": "superadmin"},
    )
    assert resp.status_code == 400


def test_create_user_returns_409_on_duplicate_username() -> None:
    client, users_repo, _ = build_client(actor_role=ROLE_ADMIN)
    users_repo.create_user(
        username="dup", password_hash="hashed", role=ROLE_VIEWER
    )
    resp = client.post(
        "/users",
        json={"username": "dup", "password": "Sup3rSecret!"},
    )
    assert resp.status_code == 409


def test_admin_cannot_delete_self() -> None:
    client, users_repo, _ = build_client(actor_role=ROLE_ADMIN)
    # Fabrique manuellement un utilisateur partageant l'user_id de
    # l'acteur fictif pour simuler "je supprime mon propre compte".
    users_repo.docs.append(
        {
            "user_id": "actor-id",
            "username": "bob",
            "role": ROLE_ADMIN,
            "password_hash": "hashed",
            "disabled": False,
        }
    )
    resp = client.delete("/users/actor-id")
    assert resp.status_code == 400


def test_admin_can_change_role_and_audit_logs_it() -> None:
    client, users_repo, audit_repo = build_client(actor_role=ROLE_ADMIN)
    user = users_repo.create_user(
        username="x", password_hash="hashed", role=ROLE_VIEWER
    )
    resp = client.patch(
        f"/users/{user['user_id']}/role", json={"role": ROLE_REVIEWER}
    )
    assert resp.status_code == 200
    assert any(
        e["event_type"] == "user_role_changed" for e in audit_repo.events
    )


# ---- /audit-log ----


def test_admin_can_list_audit_log() -> None:
    client, _, audit_repo = build_client(actor_role=ROLE_ADMIN)
    audit_repo.append(
        event_type="login_success",
        user_id="actor-id",
        username="bob",
    )
    resp = client.get("/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert any(e["event_type"] == "login_success" for e in body)


def test_non_admin_cannot_read_audit_log(monkeypatch) -> None:
    from backend.core import config as cfg
    monkeypatch.setattr(cfg.settings, "AUTH_ENABLED", True, raising=False)

    client, _, _ = build_client(actor_role=ROLE_REVIEWER)
    resp = client.get("/audit-log")
    assert resp.status_code == 403


def test_audit_log_can_filter_by_event_type() -> None:
    client, _, audit_repo = build_client(actor_role=ROLE_ADMIN)
    audit_repo.append(event_type="login_success", user_id="u1", username="a")
    audit_repo.append(event_type="scenario_upload", user_id="u1", username="a")
    resp = client.get("/audit-log", params={"event_type": "login_success"})
    assert resp.status_code == 200
    body = resp.json()
    assert all(e["event_type"] == "login_success" for e in body)
