"""Tests for the TOTP layer (RFC 6238) and OTP-aware login flow."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.core import auth as auth_module
from backend.core import config as config_module
from backend.core import totp as totp_module
from backend.main import app


KNOWN_PASSWORD = "S3cret-PFE!"
KNOWN_SECRET = "JBSWY3DPEHPK3PXP"  # well-known RFC-test secret (= "Hello!\xde\xad\xbe\xef")


# ---------- TOTP primitives ----------


def test_generate_secret_returns_base32_string() -> None:
    s = totp_module.generate_secret()
    assert len(s) >= 32
    # Base32 alphabet only.
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in s)


def test_current_code_matches_self() -> None:
    code = totp_module.current_code(KNOWN_SECRET)
    assert totp_module.verify_code(KNOWN_SECRET, code) is True


def test_verify_code_rejects_wrong_code() -> None:
    assert totp_module.verify_code(KNOWN_SECRET, "000000") in {False, True}
    # Make sure a code that's definitely wrong is rejected: take the current
    # one and shift its last digit.
    real = totp_module.current_code(KNOWN_SECRET)
    wrong = real[:-1] + ("1" if real[-1] != "1" else "2")
    assert totp_module.verify_code(KNOWN_SECRET, wrong) is False


def test_verify_code_handles_clock_drift() -> None:
    """A code from 25 s ago must still be accepted with window=1 (±30s)."""
    now = int(time.time())
    past_code = totp_module.current_code(KNOWN_SECRET, when=now - 25)
    assert totp_module.verify_code(KNOWN_SECRET, past_code, when=now) is True


def test_verify_code_rejects_codes_outside_window() -> None:
    now = int(time.time())
    very_old = totp_module.current_code(KNOWN_SECRET, when=now - 180)
    assert totp_module.verify_code(KNOWN_SECRET, very_old, when=now) is False


def test_verify_code_rejects_malformed_input() -> None:
    assert totp_module.verify_code(KNOWN_SECRET, "abcdef") is False
    assert totp_module.verify_code(KNOWN_SECRET, "12345") is False  # 5 digits
    assert totp_module.verify_code(KNOWN_SECRET, "") is False
    assert totp_module.verify_code(KNOWN_SECRET, None) is False  # type: ignore[arg-type]


def test_verify_code_tolerates_whitespace_and_dashes() -> None:
    code = totp_module.current_code(KNOWN_SECRET)
    assert totp_module.verify_code(KNOWN_SECRET, f"{code[:3]} {code[3:]}") is True
    assert totp_module.verify_code(KNOWN_SECRET, f"{code[:3]}-{code[3:]}") is True


def test_provisioning_uri_format() -> None:
    uri = totp_module.provisioning_uri(
        KNOWN_SECRET, account_name="admin", issuer="SIA-CCM"
    )
    assert uri.startswith("otpauth://totp/SIA-CCM%3Aadmin?")
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "issuer=SIA-CCM" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri


# ---------- AuthService with OTP ----------


def _configure_auth(monkeypatch: pytest.MonkeyPatch, *, otp_enabled: bool) -> None:
    fresh_hash = auth_module.hash_password(KNOWN_PASSWORD)
    monkeypatch.setattr(
        config_module.settings, "AUTH_ADMIN_USERNAME", "admin", raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "AUTH_ADMIN_PASSWORD_HASH", fresh_hash, raising=False
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
        config_module.settings, "AUTH_OTP_ENABLED", otp_enabled, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "AUTH_OTP_SECRET", KNOWN_SECRET, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "AUTH_OTP_ISSUER", "SIA-CCM", raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", True, raising=False
    )


def test_login_without_otp_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, otp_enabled=False)
    from backend.services.auth_service import AuthService

    service = AuthService()
    token = service.login("admin", KNOWN_PASSWORD)
    assert isinstance(token, str)


def test_login_raises_otp_required_when_enabled_and_no_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, otp_enabled=True)
    from backend.services.auth_service import AuthService, OTPRequiredError

    service = AuthService()
    with pytest.raises(OTPRequiredError):
        service.login("admin", KNOWN_PASSWORD)


def test_login_rejects_wrong_otp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, otp_enabled=True)
    from backend.services.auth_service import AuthenticationError, AuthService

    service = AuthService()
    with pytest.raises(AuthenticationError):
        service.login("admin", KNOWN_PASSWORD, otp_code="000000")


def test_login_accepts_valid_otp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, otp_enabled=True)
    from backend.services.auth_service import AuthService

    service = AuthService()
    code = totp_module.current_code(KNOWN_SECRET)
    token = service.login("admin", KNOWN_PASSWORD, otp_code=code)
    assert isinstance(token, str)


# ---------- HTTP route flow ----------


def test_login_route_returns_requires_otp_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, otp_enabled=True)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": KNOWN_PASSWORD},
    )
    assert resp.status_code == 401
    body = resp.json()
    detail = body.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("requires_otp") is True


def test_login_route_returns_enrollment_info_on_otp_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When OTP is required, the 401 must carry the provisioning URI so the
    frontend can offer a QR code for first-time enrolment."""
    _configure_auth(monkeypatch, otp_enabled=True)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": KNOWN_PASSWORD},
    )
    detail = resp.json()["detail"]
    assert detail["requires_otp"] is True
    assert detail["account"] == "admin"
    assert detail["issuer"] == "SIA-CCM"
    assert detail["secret"] == KNOWN_SECRET
    assert detail["provisioning_uri"].startswith("otpauth://totp/")
    assert "secret=" in detail["provisioning_uri"]


def test_login_route_does_not_leak_enrollment_on_bad_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong password must NOT surface the OTP secret — only valid creds do."""
    _configure_auth(monkeypatch, otp_enabled=True)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert resp.status_code == 401
    text = resp.text
    assert KNOWN_SECRET not in text
    assert "provisioning_uri" not in text


def test_login_route_succeeds_with_otp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, otp_enabled=True)
    client = TestClient(app)
    code = totp_module.current_code(KNOWN_SECRET)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": KNOWN_PASSWORD, "otp_code": code},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["otp_used"] is True
    assert body["access_token"]


def test_otp_setup_route_returns_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, otp_enabled=True)
    client = TestClient(app)
    code = totp_module.current_code(KNOWN_SECRET)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": KNOWN_PASSWORD, "otp_code": code},
    )
    token = login.json()["access_token"]
    resp = client.get(
        "/api/v1/auth/otp/setup",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["account"] == "admin"
    assert body["secret"] == KNOWN_SECRET
    assert body["provisioning_uri"].startswith("otpauth://totp/")


def test_otp_setup_route_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, otp_enabled=True)
    client = TestClient(app)
    resp = client.get("/api/v1/auth/otp/setup")
    assert resp.status_code == 401


def test_otp_setup_route_409_when_no_secret_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, otp_enabled=False)
    monkeypatch.setattr(
        config_module.settings, "AUTH_OTP_SECRET", "", raising=False
    )
    client = TestClient(app)
    # We need a valid token first.
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": KNOWN_PASSWORD},
    )
    token = login.json()["access_token"]
    resp = client.get(
        "/api/v1/auth/otp/setup",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
