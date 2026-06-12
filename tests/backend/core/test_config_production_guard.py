"""Vérifie que SIA_ENV=production refuse de démarrer avec un secret par défaut.

Le but du garde-fou : impossible de faire tourner une instance prod qui
porterait encore le JWT secret ou le hash admin du repo public.
"""

from __future__ import annotations

import pytest


from backend.core.config import _DEFAULT_JWT_SECRET, Settings


def test_dev_mode_allows_default_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIA_ENV", "development")
    monkeypatch.delenv("SIA_JWT_SECRET", raising=False)
    monkeypatch.delenv("SIA_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("SIA_OTP_ENABLED", "false")
    monkeypatch.delenv("SIA_OTP_SECRET", raising=False)

    # On force pydantic-settings à ignorer le .env du repo qui pourrait
    # surcharger nos variables d'environnement de test.
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.ENV == "development"
    assert s.AUTH_JWT_SECRET == _DEFAULT_JWT_SECRET


def test_production_refuses_default_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIA_ENV", "production")
    monkeypatch.setenv(
        "SIA_ADMIN_PASSWORD_HASH",
        "pbkdf2_sha256$200000$AAAA$BBBB",
    )
    monkeypatch.delenv("SIA_JWT_SECRET", raising=False)
    monkeypatch.setenv("SIA_OTP_ENABLED", "false")

    with pytest.raises(Exception) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "SIA_JWT_SECRET" in str(exc_info.value)


def test_production_refuses_default_admin_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIA_ENV", "production")
    monkeypatch.setenv("SIA_JWT_SECRET", "x" * 64)
    monkeypatch.delenv("SIA_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("SIA_OTP_ENABLED", "false")

    with pytest.raises(Exception) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "SIA_ADMIN_PASSWORD_HASH" in str(exc_info.value)


def test_production_refuses_otp_enabled_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIA_ENV", "production")
    monkeypatch.setenv("SIA_JWT_SECRET", "x" * 64)
    monkeypatch.setenv("SIA_ADMIN_PASSWORD_HASH", "pbkdf2_sha256$200000$AA$BB")
    monkeypatch.setenv("SIA_OTP_ENABLED", "true")
    monkeypatch.setenv("SIA_OTP_SECRET", "")

    with pytest.raises(Exception) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "SIA_OTP_SECRET" in str(exc_info.value)


def test_production_accepts_all_secrets_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIA_ENV", "production")
    monkeypatch.setenv("SIA_JWT_SECRET", "x" * 64)
    monkeypatch.setenv(
        "SIA_ADMIN_PASSWORD_HASH",
        "pbkdf2_sha256$200000$AA$BB",
    )
    monkeypatch.setenv("SIA_OTP_ENABLED", "true")
    monkeypatch.setenv("SIA_OTP_SECRET", "JBSWY3DPEHPK3PXP")

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.ENV == "production"
    assert s.AUTH_JWT_SECRET == "x" * 64
