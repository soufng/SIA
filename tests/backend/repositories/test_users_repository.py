"""Tests pour UsersRepository (CRUD + unicité + sécurité des sorties)."""

from __future__ import annotations

import pytest

from backend.core.auth import hash_password
from backend.repositories.users_repository import (
    ROLE_ADMIN,
    ROLE_REVIEWER,
    ROLE_VIEWER,
    UserAlreadyExistsError,
    UsersRepository,
)


class _Cursor(list):
    """Petit objet façon pymongo qui supporte .sort/.limit en chaîne."""

    def sort(self, _spec) -> "_Cursor":
        return self

    def limit(self, n: int) -> "_Cursor":
        return _Cursor(self[:n])


# ---- Fakes minimaux pour éviter de toucher Mongo dans les tests ----


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def find_one(self, query: dict):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    def find(self, query: dict):
        if not query:
            results = [dict(d) for d in self.docs]
        else:
            results = [
                dict(d)
                for d in self.docs
                if all(d.get(k) == v for k, v in query.items())
            ]
        return _Cursor(results)

    def insert_one(self, document: dict) -> None:
        # Index unique sur username_lower : on simule la contrainte.
        if any(d["username_lower"] == document["username_lower"] for d in self.docs):
            raise RuntimeError("E11000 duplicate key")
        self.docs.append(dict(document))

    def update_one(self, query: dict, patch: dict) -> None:
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(patch.get("$set", {}))
                return

    def delete_one(self, query: dict):
        for i, doc in enumerate(self.docs):
            if all(doc.get(k) == v for k, v in query.items()):
                del self.docs[i]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def count_documents(self, query: dict) -> int:
        return len(list(self.find(query)))

    def create_index(self, *args, **kwargs) -> None:
        # Index already simulated above.
        pass


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]


@pytest.fixture
def repo() -> UsersRepository:
    db = FakeDatabase()
    return UsersRepository(database=db)


# ---- Tests ----


def test_create_user_assigns_uuid_and_strips_hash_in_response(
    repo: UsersRepository,
) -> None:
    out = repo.create_user(
        username="alice",
        password_hash=hash_password("Sup3rSecret!"),
        role=ROLE_REVIEWER,
    )
    assert out["username"] == "alice"
    assert out["role"] == ROLE_REVIEWER
    # Les sorties publiques NE doivent JAMAIS exposer le hash.
    assert "password_hash" not in out
    # On a bien un user_id généré.
    assert out["user_id"]


def test_create_user_rejects_duplicate_username_case_insensitive(
    repo: UsersRepository,
) -> None:
    repo.create_user(
        username="Alice", password_hash=hash_password("xxxxxxxx"),
    )
    with pytest.raises(UserAlreadyExistsError):
        repo.create_user(
            username="alice",
            password_hash=hash_password("yyyyyyyy"),
        )


def test_create_user_rejects_unknown_role(repo: UsersRepository) -> None:
    with pytest.raises(ValueError):
        repo.create_user(
            username="bob",
            password_hash=hash_password("xxxxxxxx"),
            role="superadmin",
        )


def test_get_for_auth_keeps_password_hash(repo: UsersRepository) -> None:
    hsh = hash_password("Sup3rSecret!")
    repo.create_user(username="charlie", password_hash=hsh)
    auth_doc = repo.get_for_auth("charlie")
    assert auth_doc is not None
    assert auth_doc["password_hash"] == hsh


def test_get_by_username_drops_password_hash(repo: UsersRepository) -> None:
    repo.create_user(
        username="dave", password_hash=hash_password("xxxxxxxx")
    )
    user = repo.get_by_username("DAVE")
    assert user is not None
    assert "password_hash" not in user


def test_list_users_returns_all_visible_fields(
    repo: UsersRepository,
) -> None:
    repo.create_user(
        username="eve", password_hash=hash_password("xxxxxxxx"),
        role=ROLE_ADMIN,
    )
    repo.create_user(
        username="frank", password_hash=hash_password("xxxxxxxx"),
        role=ROLE_VIEWER,
    )
    users = repo.list_users()
    assert {u["username"] for u in users} == {"eve", "frank"}
    assert all("password_hash" not in u for u in users)


def test_delete_user_returns_true_when_deleted(
    repo: UsersRepository,
) -> None:
    created = repo.create_user(
        username="grace", password_hash=hash_password("xxxxxxxx"),
    )
    assert repo.delete(created["user_id"]) is True
    assert repo.get_by_id(created["user_id"]) is None


def test_set_role_validates_role(repo: UsersRepository) -> None:
    created = repo.create_user(
        username="henry", password_hash=hash_password("xxxxxxxx"),
    )
    with pytest.raises(ValueError):
        repo.set_role(created["user_id"], "godmode")


def test_count_reflects_collection_state(repo: UsersRepository) -> None:
    assert repo.count() == 0
    repo.create_user(
        username="ivy", password_hash=hash_password("xxxxxxxx"),
    )
    assert repo.count() == 1
