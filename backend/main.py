import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter

from backend.core.rate_limit import install_rate_limiting
from backend.db.mongodb import close_mongodb_client


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

ROUTE_MODULES = [
    "backend.api.v1.routes.auth",
    "backend.api.v1.routes.uploads",
    "backend.api.v1.routes.health",
    "backend.api.v1.routes.analysis",
    "backend.api.v1.routes.plagiarism",
    "backend.api.v1.routes.moderation",
    "backend.api.v1.routes.scenarios",
    "backend.api.v1.routes.users",
    "backend.api.v1.routes.audit_log",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Log application startup and shutdown events."""
    logger.info("Starting SPM FastAPI application.")
    yield
    close_mongodb_client()
    logger.info("Stopping SPM FastAPI application.")


def create_app() -> FastAPI:
    """Create and configure the main FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="SPM API",
        description=(
            "API backend pour l'analyse de scenarios PDF, la detection de "
            "plagiat et la moderation de contenu."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_rate_limiting(app)
    include_api_routers(app)

    @app.get("/", tags=["root"])
    def root() -> dict[str, str]:
        """Return a simple API welcome message."""
        return {"message": "SPM API is running"}

    return app


def include_api_routers(app: FastAPI) -> None:
    """Include available API routers under the global API prefix.

    Args:
        app: FastAPI application where routers should be registered.
    """
    for module_path in ROUTE_MODULES:
        module = import_module(module_path)
        router: Any = getattr(module, "router", None)

        if not isinstance(router, APIRouter):
            logger.warning("No APIRouter named 'router' found in %s.", module_path)
            continue

        app.include_router(router, prefix=API_PREFIX)
        logger.info("Included router from %s with prefix %s.", module_path, API_PREFIX)


app = create_app()
