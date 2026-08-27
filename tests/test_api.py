from __future__ import annotations

import importlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("uvicorn")
from fastapi.testclient import TestClient

from forbiddenland.api.app import DEFAULT_API_PORT, DEFAULT_API_RELOAD, create_app
from forbiddenland.application.market_service import (
    MarketDataProviderError,
    MarketDataService,
)
from forbiddenland.domain.market import (
    AssetType,
    MarketAsset,
    MarketBar,
    MarketDataResult,
    MarketQuery,
    Security,
)


class FakeProvider:
    backend = "remote"
    source = "fake provider"

    def list_securities(self) -> list[Security]:
        return [Security(code="688256", name="寒武纪")]

    def list_assets(self, asset_type: AssetType) -> list[MarketAsset]:
        items = {
            "stock": [MarketAsset(asset_type="stock", code="688256", name="寒武纪")],
            "index": [MarketAsset(asset_type="index", code="sh000001", name="上证指数")],
            "concept": [MarketAsset(asset_type="concept", code="886112.TI", name="MLCC概念")],
        }
        return items[asset_type]

    def fetch_history(self, query: MarketQuery) -> MarketDataResult:
        bars = (
            MarketBar(
                symbol=query.symbol,
                date=date(2024, 1, 2),
                open=10.0,
                high=10.5,
                low=9.8,
                close=10.2,
                volume=1000.0,
                change_percent=2.0,
            ),
            MarketBar(
                symbol=query.symbol,
                date=date(2024, 1, 3),
                open=10.2,
                high=10.8,
                low=10.0,
                close=10.6,
                volume=1200.0,
                change_percent=3.9,
            ),
        )
        return MarketDataResult(
            query=query,
            bars=bars,
            source=self.source,
            backend=self.backend,
            storage="memory",
            retrieved_at_utc=datetime(2024, 1, 4, tzinfo=UTC),
            local_snapshot_review_required=False,
        )


class FailingProvider(FakeProvider):
    def fetch_history(self, query: MarketQuery) -> MarketDataResult:
        raise MarketDataProviderError("provider unavailable")


def test_backend_default_port_is_9092() -> None:
    assert DEFAULT_API_PORT == 9092


def test_backend_development_entrypoint_enables_auto_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = importlib.import_module("forbiddenland.api.app")
    captured: dict[str, object] = {}

    def fake_run(application: object, **kwargs: object) -> None:
        captured["application"] = application
        captured.update(kwargs)

    monkeypatch.delenv("FORBIDDENLAND_API_RELOAD", raising=False)
    monkeypatch.setattr("uvicorn.run", fake_run)

    app_module.main()

    assert DEFAULT_API_RELOAD is True
    assert captured["application"] == "forbiddenland.api.app:app"
    assert captured["reload"] is True
    assert captured["reload_dirs"] == [str(Path(app_module.__file__).resolve().parents[2])]


def test_backend_development_reload_can_be_explicitly_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = importlib.import_module("forbiddenland.api.app")
    captured: dict[str, object] = {}

    def fake_run(application: object, **kwargs: object) -> None:
        captured["application"] = application
        captured.update(kwargs)

    monkeypatch.setenv("FORBIDDENLAND_API_RELOAD", "0")
    monkeypatch.setattr("uvicorn.run", fake_run)

    app_module.main()

    assert captured["reload"] is False
    assert captured["reload_dirs"] is None


def test_health_and_security_routes_expose_service_boundary() -> None:
    app = create_app(market_service=MarketDataService(FakeProvider()))

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        securities = client.get("/api/v1/market/securities")

    assert health.status_code == 200
    assert health.json()["backend"] == "remote"
    assert "local snapshots require" in health.json()["data_policy"]
    assert securities.status_code == 200
    assert securities.json() == {"items": [{"code": "688256", "name": "寒武纪"}]}


def test_market_route_returns_normalized_bars_and_provenance() -> None:
    app = create_app(market_service=MarketDataService(FakeProvider()))

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/market/bars",
            params={
                "symbol": "688256.SH",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "adjust": "qfq",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_type"] == "stock"
    assert payload["symbol"] == "688256"
    assert payload["bars"][0]["close"] == 10.2
    assert payload["summary"]["bar_count"] == 2
    assert payload["summary"]["period_change_percent"] == pytest.approx(3.9215686)
    assert payload["provenance"]["adjust"] == "qfq"
    assert payload["provenance"]["local_snapshot_review_required"] is False


def test_asset_route_filters_each_supported_asset_type() -> None:
    app = create_app(market_service=MarketDataService(FakeProvider()))

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/market/assets",
            params={"asset_type": "concept", "query": "MLCC", "limit": 10},
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [{"asset_type": "concept", "code": "886112.TI", "name": "MLCC概念"}]
    }


def test_market_route_accepts_index_asset_queries() -> None:
    app = create_app(market_service=MarketDataService(FakeProvider()))

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/market/bars",
            params={
                "asset_type": "index",
                "symbol": "sh000001",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
            },
        )

    assert response.status_code == 200
    assert response.json()["asset_type"] == "index"
    assert response.json()["symbol"] == "sh000001"


def test_market_route_maps_provider_failure_to_bad_gateway() -> None:
    app = create_app(market_service=MarketDataService(FailingProvider()))

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/market/bars",
            params={
                "symbol": "688256",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "provider unavailable"


def test_market_route_rejects_reversed_date_range() -> None:
    app = create_app(market_service=MarketDataService(FakeProvider()))

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/market/bars",
            params={
                "symbol": "688256",
                "start_date": "2024-02-01",
                "end_date": "2024-01-01",
            },
        )

    assert response.status_code == 422
    assert "start_date" in response.json()["detail"]
