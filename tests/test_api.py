from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from forbiddenland.api.app import create_app
from forbiddenland.application.market_service import (
    MarketDataProviderError,
    MarketDataService,
)
from forbiddenland.domain.market import (
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
    assert payload["symbol"] == "688256"
    assert payload["bars"][0]["close"] == 10.2
    assert payload["summary"]["bar_count"] == 2
    assert payload["summary"]["period_change_percent"] == pytest.approx(3.9215686)
    assert payload["provenance"]["adjust"] == "qfq"
    assert payload["provenance"]["local_snapshot_review_required"] is False


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
