"""Tests for the authentication layer: hashing, JWT, login route, guard."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.core import auth as auth_module
from backend.core import config as config_module
from backend.main import app


KNOWN_PASSWORD = "S3cret-PFE!"


def _refresh_admin_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire up known admin credentials for every test that needs to log in.

    Always disables OTP here — OTP-aware behaviour is exhaustively covered
    in ``test_otp.py`` and the auth tests would otherwise pick up whatever
    is configured in the developer's real ``.env`` file.
    """
    fresh_hash = auth_module.hash_password(KNOWN_PASSWORD)
    monkeypatch.setattr(
        config_module.settings, "AUTH_ADMIN_USERNAME", "admin", raising=False
    )
    monkeypatch.setattr(
        config_module.settings,
        "AUTH_ADMIN_PASSWORD_HASH",
        fresh_hash,
        raising=False,
    )
    monkeypatch.setattr(
        config_module.settings,
        "AUTH_JWT_SECRET",
        "test-secret-please-rotate-me-32chars-min",
        raising=False,
    )
    monkeypatch.setattr(
        config_module.settings, "AUTH_JWT_EXPIRY_MINUTES", 60, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "AUTH_OTP_ENABLED", False, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "AUTH_OTP_SECRET", "", raising=False
    )


# ---------- Crypto primitives ----------


def test_hash_password_round_trips() -> None:
    h = auth_module.hash_password(KNOWN_PASSWORD)
    assert h.startswith("pbkdf2_sha256$")
    assert auth_module.verify_password(KNOWN_PASSWORD, h) is True
    assert auth_module.verify_password("wrong", h) is False


def test_hash_password_uses_fresh_salt_each_call() -> None:
    a = auth_module.hash_password(KNOWN_PASSWORD)
    b = auth_module.hash_password(KNOWN_PASSWORD)
    assert a != b
    assert auth_module.verify_password(KNOWN_PASSWORD, a)
    assert auth_module.verify_password(KNOWN_PASSWORD, b)


def test_verify_password_rejects_malformed_hash() -> None:
    assert auth_module.verify_password(KNOWN_PASSWORD, "") is False
    assert auth_module.verify_password(KNOWN_PASSWORD, "garbage") is False
    assert auth_module.verify_password(KNOWN_PASSWORD, "plain$$$nope") is False


def test_jwt_round_trip() -> None:
    token = auth_module.issue_token(
        "admin", secret="abc", expires_in_minutes=5
    )
    payload = auth_module.decode_token(token, secret="abc")
    assert payload.sub == "admin"
    assert payload.exp > payload.iat
    assert payload.exp > int(time.time())


def test_jwt_rejects_tampered_signature() -> None:
    token = auth_module.issue_token("admin", secret="abc")
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(auth_module.TokenError):
        auth_module.decode_token(tampered, secret="abc")


def test_jwt_rejects_wrong_secret() -> None:
    token = auth_module.issue_token("admin", secret="abc")
    with pytest.raises(auth_module.TokenError):
        auth_module.decode_token(token, secret="xyz")


def test_jwt_rejects_expired_token() -> None:
    token = auth_module.issue_token(
        "admin", secret="abc", expires_in_minutes=1
    )
    # Force expiry by overriding time.time inside decode_token via monkeypatch
    import time as time_module

    real_time = time_module.time
    try:
        time_module.time = lambda: real_time() + 10 * 60
        with pytest.raises(auth_module.TokenError):
            auth_module.decode_token(token, secret="abc")
    finally:
        time_module.time = real_time


def test_jwt_rejects_alg_none_confusion() -> None:
    """Defence against the classic ``alg=none`` attack."""
    import base64
    import json

    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "admin", "iat": 0, "exp": 9999999999}).encode()
    ).rstrip(b"=")
    forged = f"{header.decode()}.{payload.decode()}."
    with pytest.raises(auth_module.TokenError):
        auth_module.decode_token(forged, secret="abc")


# ---------- AuthService ----------


def test_auth_service_login_returns_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _refresh_admin_creds(monkeypatch)
    from backend.services.auth_service import AuthService

    service = AuthService()
    token = service.login("admin", KNOWN_PASSWORD)
    assert isinstance(token, str) and token.count(".") == 2
    user = service.authenticate_token(token)
    assert user.username == "admin"


def test_auth_service_login_rejects_bad_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refresh_admin_creds(monkeypatch)
    from backend.services.auth_service import (
        AuthenticationError,
        AuthService,
    )

    service = AuthService()
    with pytest.raises(AuthenticationError):
        service.login("admin", "nope")


def test_auth_service_login_username_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refresh_admin_creds(monkeypatch)
    from backend.services.auth_service import AuthService

    service = AuthService()
    assert service.login("ADMIN", KNOWN_PASSWORD)


# ---------- /auth/login HTTP route ----------


def test_login_route_returns_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _refresh_admin_creds(monkeypatch)
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", True, raising=False
    )
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": KNOWN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["username"] == "admin"
    assert body["expires_in_minutes"] == 60
    assert body["access_token"].count(".") == 2


def test_login_route_returns_401_on_bad_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refresh_admin_creds(monkeypatch)
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", True, raising=False
    )
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert resp.status_code == 401
    # Response must never echo back the submitted password.
    assert "wrong" not in resp.text


# ---------- Protected routes guard ----------


def test_protected_route_blocks_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refresh_admin_creds(monkeypatch)
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", True, raising=False
    )
    client = TestClient(app)
    resp = client.get("/api/v1/analysis/history")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").lower().startswith("bearer")


def test_protected_route_blocks_with_bad_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refresh_admin_creds(monkeypatch)
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", True, raising=False
    )
    client = TestClient(app)
    resp = client.get(
        "/api/v1/analysis/history",
        headers={"Authorization": "Bearer not.a.real.token"},
    )
    assert resp.status_code == 401


def test_protected_route_allows_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refresh_admin_creds(monkeypatch)
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", True, raising=False
    )
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": KNOWN_PASSWORD},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    # The route may fail downstream (no MongoDB in tests), but it must not
    # return 401: the auth layer accepted the token.
    resp = client.get(
        "/api/v1/analysis/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code != 401


def test_health_route_remains_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", True, raising=False
    )
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    # /health should NOT require auth; it returns 200 (or 503 if downstream
    # services are down) but never 401.
    assert resp.status_code != 401


def test_auth_disabled_skips_token_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", False, raising=False
    )
    client = TestClient(app)
    resp = client.get("/api/v1/analysis/history")
    assert resp.status_code != 401


# ---------- /auth/me ----------


def test_me_returns_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    _refresh_admin_creds(monkeypatch)
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", True, raising=False
    )
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": KNOWN_PASSWORD},
    )
    token = login.json()["access_token"]
    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "admin"
    assert body["authenticated"] is True
    assert body["auth_enabled"] is True


def test_me_under_disabled_auth_returns_anonymous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", False, raising=False
    )
    client = TestClient(app)
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is False
    assert body["auth_enabled"] is False
