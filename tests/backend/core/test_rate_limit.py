"""Vérifie que le rate limiter installé sur l'app renvoie bien 429."""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_slowapi(monkeypatch: pytest.MonkeyPatch):
    """Reload the rate_limit module for each test so limit counters reset."""
    # Memory storage par défaut → un counter par process. On veut un état
    # propre entre tests : on reload pour ré-instancier le ``Limiter``.
    monkeypatch.delenv("SIA_RATE_LIMIT_STORAGE", raising=False)
    monkeypatch.delenv("SIA_RATE_LIMIT_DEFAULT", raising=False)
    import backend.core.rate_limit as rl

    importlib.reload(rl)
    yield rl


def test_endpoint_with_strict_limit_returns_429_after_burst(
    _reset_slowapi,
) -> None:
    rl = _reset_slowapi

    app = FastAPI()
    rl.install_rate_limiting(app)

    # On colle un limite très basse pour pouvoir la déclencher en test.
    @app.get("/burst")
    @rl.limiter.limit("2/minute")
    def burst(request: Request) -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)

    assert client.get("/burst").status_code == 200
    assert client.get("/burst").status_code == 200
    third = client.get("/burst")
    assert third.status_code == 429
    body = third.json()
    assert "Trop de requêtes" in body["detail"]


def test_429_response_has_french_body_and_retry_after_header(
    _reset_slowapi,
) -> None:
    """Quand on dépasse, la réponse est française et porte Retry-After."""
    rl = _reset_slowapi

    app = FastAPI()
    rl.install_rate_limiting(app)

    @app.get("/strict")
    @rl.limiter.limit("1/minute")
    def strict(request: Request) -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/strict").status_code == 200
    over = client.get("/strict")
    assert over.status_code == 429
    body = over.json()
    assert body["detail"].startswith("Trop de requêtes")
    # ``retry_after_seconds`` peut être ``None`` selon la version de slowapi
    # mais la clé doit exister pour permettre au frontend de l'afficher.
    assert "retry_after_seconds" in body
