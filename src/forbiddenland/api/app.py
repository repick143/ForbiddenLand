"""FastAPI application entry point for the independent backend service."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..application.market_service import MarketDataService
from ..config import CompatibilityConfig
from ..infrastructure.market_data.akshare_provider import AkShareMarketProvider
from .routes import router

DEFAULT_API_PORT = 9092
DEFAULT_API_RELOAD = True


def _reload_enabled() -> bool:
    """Return whether the development API process should watch source files."""

    value = os.environ.get("FORBIDDENLAND_API_RELOAD")
    if value is None:
        return DEFAULT_API_RELOAD
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(
        "Invalid FORBIDDENLAND_API_RELOAD value; use true/false, 1/0, yes/no, or on/off."
    )


def _cors_origins() -> list[str]:
    configured = os.environ.get(
        "FORBIDDENLAND_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def create_app(*, market_service: MarketDataService | None = None) -> FastAPI:
    """Create the HTTP app with injectable services for contract and unit tests."""

    service = market_service
    if service is None:
        service = MarketDataService(AkShareMarketProvider(CompatibilityConfig.from_env()))
    app = FastAPI(
        title="ForbiddenLand API",
        version=__version__,
        description="Backend service for A-share research data and analysis views.",
    )
    app.state.market_data_service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("FORBIDDENLAND_API_HOST", "127.0.0.1")
    port = int(os.environ.get("FORBIDDENLAND_API_PORT", str(DEFAULT_API_PORT)))
    reload_enabled = _reload_enabled()
    # Keep the reloader focused on Python sources; scanning local market snapshots and frontend
    # dependencies would add needless filesystem work to every edit.
    source_root = Path(__file__).resolve().parents[2]
    uvicorn.run(
        "forbiddenland.api.app:app",
        host=host,
        port=port,
        reload=reload_enabled,
        reload_dirs=[str(source_root)] if reload_enabled else None,
    )


if __name__ == "__main__":
    main()
