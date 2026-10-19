"""Offline tests for the baseline data adapter over the ecom-data source contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ecom_data.model import BarRequest, QuoteRequest
from ecom_data.sources import DataSource, register_source
from ecom_data.sources.akshare import market_for_code
from research.predict.baseline import data as data_mod


class FakeSource(DataSource):
    """Deterministic in-memory source registered under ``fake``."""

    name = "fake"

    def __init__(self, bars_by_code: dict[str, list[dict]] | None = None) -> None:
        self.bars_by_code = bars_by_code or {}
        self.last_fetch: dict[str, dict] = {}

    @property
    def source_version(self) -> str | None:
        return "fake-1.0"

    def fetch_bars(self, request: BarRequest) -> list[dict]:
        self.last_fetch[request.code] = {
            "source": "fake-remote",
            "storage": "in-memory",
            "retrieved_at_utc": datetime(2026, 1, 1, tzinfo=UTC),
        }
        return list(self.bars_by_code.get(request.code, []))

    def fetch_quote(self, request: QuoteRequest) -> dict | None:
        return None


class NoProvenanceSource(FakeSource):
    """Fake source that does not expose ``last_fetch`` (fallback provenance path)."""

    def fetch_bars(self, request: BarRequest) -> list[dict]:
        return list(self.bars_by_code.get(request.code, []))


def _fake_bars(symbol: str, days: int = 40) -> list[dict]:
    base = datetime(2023, 12, 1, tzinfo=UTC)
    return [
        {
            "datetime": (base + timedelta(days=index)).isoformat(timespec="seconds"),
            "open": 10.0 + index,
            "high": 11.0 + index,
            "low": 9.0 + index,
            "close": 10.5 + index,
            "vol": 1000.0 + index,
        }
        for index in range(days)
    ]


def test_market_for_code() -> None:
    assert market_for_code("600183") == "SH"
    assert market_for_code("688256") == "SH"
    assert market_for_code("000001") == "SZ"
    assert market_for_code("300750") == "SZ"
    assert market_for_code("830799") == "BJ"


def test_fetch_bars_windows_and_normalizes() -> None:
    source = FakeSource({"688256": _fake_bars("688256")})
    register_source(source)
    batch = data_mod.fetch_bars(("688256",), "20240101", "20240131", "qfq", source=source)
    assert list(batch.frame.columns) == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "symbol",
    ]
    assert set(batch.frame["symbol"]) == {"688256"}
    assert batch.frame["timestamp"].is_monotonic_increasing
    assert batch.frame["timestamp"].min() >= pd.Timestamp("2024-01-01")
    assert batch.frame["timestamp"].max() <= pd.Timestamp("2024-01-31")
    assert batch.source == "fake-remote"
    assert batch.storage == "in-memory"


def test_fetch_bars_provenance_fallback_without_last_fetch() -> None:
    source = NoProvenanceSource({"688256": _fake_bars("688256")})
    batch = data_mod.fetch_bars(("688256",), "20240101", "20240131", source=source)
    assert batch.source == "fake"
    assert batch.storage == "ecom-data source"


def test_fetch_bars_rejects_missing_keys() -> None:
    source = FakeSource({"688256": [{"datetime": "2024-01-02", "close": 10.0}]})
    with pytest.raises(RuntimeError, match="missing keys"):
        data_mod.fetch_bars(("688256",), "20240101", "20240131", source=source)


def test_fetch_bars_rejects_inverted_window() -> None:
    source = FakeSource()
    with pytest.raises(ValueError, match="start_date"):
        data_mod.fetch_bars(("688256",), "20240131", "20240101", source=source)
