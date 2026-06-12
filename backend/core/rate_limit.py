"""Rate limiting partagé (slowapi).

On expose un ``limiter`` unique instancié une fois et réutilisé par toutes
les routes qui veulent appliquer une limite. Backend de stockage :
``memory://`` par défaut (suffisant pour une seule instance). Pour du
multi-worker, basculer sur ``redis://...`` via ``SIA_RATE_LIMIT_STORAGE``.

Les limites par route sont définies au plus près de leur usage avec le
décorateur ``@limiter.limit("…/h")`` dans le module de la route.
"""

from __future__ import annotations

import logging
import os

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from fastapi import FastAPI, status


logger = logging.getLogger(__name__)


def _storage_uri() -> str:
    return os.getenv("SIA_RATE_LIMIT_STORAGE", "memory://")


# Limites globales par défaut. Override via env SIA_RATE_LIMIT_DEFAULT.
# Une liste de limites séparées par des virgules (ex. "60/minute,1000/hour").
def _default_limits() -> list[str]:
    raw = os.getenv("SIA_RATE_LIMIT_DEFAULT", "120/minute")
    return [chunk.strip() for chunk in raw.split(",") if chunk.strip()]


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_storage_uri(),
    default_limits=_default_limits(),
)


def _french_rate_limit_response(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Réponse 429 en français, sans fuiter de détails internes."""
    logger.warning(
        "Rate limit hit on %s from %s: %s",
        request.url.path,
        get_remote_address(request),
        exc.detail,
    )
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": (
                "Trop de requêtes — vous êtes temporairement bloqué. "
                "Réessayez dans quelques instants."
            ),
            "retry_after_seconds": getattr(exc, "retry_after", None),
        },
        headers=(
            {"Retry-After": str(int(exc.retry_after))}
            if getattr(exc, "retry_after", None)
            else None
        ),
    )


def install_rate_limiting(app: FastAPI) -> None:
    """Attach the shared limiter and the French 429 handler to the app."""
    app.state.limiter = limiter
    # ``_rate_limit_exceeded_handler`` is the official slowapi handler ; we
    # wrap it so the response body matches the rest of the API (French).
    app.add_exception_handler(
        RateLimitExceeded, _french_rate_limit_response  # type: ignore[arg-type]
    )
    # Conserver aussi l'handler par défaut pour les middlewares custom qui
    # auraient besoin du comportement officiel.
    _ = _rate_limit_exceeded_handler  # silence "unused" linter
    logger.info(
        "Rate limiting enabled. storage=%s default=%s",
        _storage_uri(),
        _default_limits(),
    )
