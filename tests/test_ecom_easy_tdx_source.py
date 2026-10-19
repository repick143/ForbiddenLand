"""Offline tests for the easy-tdx source adapter (fake MacClient + temp SQLite)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import easy_tdx
import pandas as pd
import pytest
from easy_tdx import Adjust, Market, Period

from ecom_data.errors import DataSourceError
from ecom_data.model import BarRequest, QuoteRequest
from ecom_data.sources.easy_tdx import EasyTdxSource


def _kline_frame(days: int = 30) -> pd.DataFrame:
    base = pd.Timestamp("2026-09-01")
    return pd.DataFrame(
        [
            {
                "datetime": base + pd.Timedelta(days=i),
                "open": 10.0 + i,
                "high": 11.0 + i,
                "low": 9.0 + i,
                "close": 10.5 + i,
                "vol": 1000 + i,
                "amount": 100000.0 + i,
                "float_shares": 5000.0,
            }
            for i in range(days)
        ]
    )


class FakeMacClient:
    """Deterministic MacClient stand-in recording the last call."""

    def __init__(self, frame: pd.DataFrame, quotes: pd.DataFrame | None = None) -> None:
        self.frame = frame
        self.quotes = quotes
        self.calls = 0
        self.closed = False
        self.last: tuple[Any, ...] | None = None

    def get_stock_kline(
        self,
        market: Market,
        code: str,
        period: Period,
        *,
        start: int,
        count: int,
        adjust: Adjust,
        bar_time: str,
    ) -> pd.DataFrame:
        self.calls += 1
        self.last = (market, code, period, start, count, adjust, bar_time)
        return self.frame.tail(count).reset_index(drop=True)

    def get_stock_quotes(self, stocks: list[tuple[int, str]]) -> pd.DataFrame:
        self.calls += 1
        if self.quotes is None or self.quotes.empty:
            return pd.DataFrame()
        return self.quotes

    def close(self) -> None:
        self.closed = True


def _make_source(
    tmp_path: Path,
    frame: pd.DataFrame | None = None,
    quotes: pd.DataFrame | None = None,
) -> tuple[EasyTdxSource, FakeMacClient]:
    client = FakeMacClient(frame if frame is not None else _kline_frame(), quotes=quotes)
    source = EasyTdxSource(
        db_path=tmp_path / "cache.db",
        client_factory=lambda: client,
    )
    return source, client


def test_fetch_bars_fetches_then_serves_from_cache(tmp_path: Path) -> None:
    source, client = _make_source(tmp_path)
    request = BarRequest("SH", "600519", adjust="QFQ", count=10)

    first = source.fetch_bars(request)
    assert len(first) == 10
    assert client.calls == 1
    assert source.last_fetch["600519"]["storage"] == "ecom-data source"

    second = source.fetch_bars(request)
    assert second == first
    assert client.calls == 1
    assert source.last_fetch["600519"]["storage"] == "ecom-data sqlite cache"


def test_fetch_bars_larger_count_refetches(tmp_path: Path) -> None:
    source, client = _make_source(tmp_path, _kline_frame(days=60))
    assert len(source.fetch_bars(BarRequest("SH", "600519", count=5))) == 5
    assert client.calls == 1

    bigger = source.fetch_bars(BarRequest("SH", "600519", count=50))
    assert client.calls == 2
    assert len(bigger) == 50
    assert bigger[-1]["close"] == 10.5 + 59


def test_fetch_bars_maps_request_to_python_api(tmp_path: Path) -> None:
    source, client = _make_source(tmp_path)
    source.fetch_bars(
        BarRequest("SZ", "000001", period="DAILY", adjust="qfq", bar_time="start", count=8)
    )
    assert client.last == (
        Market.SZ,
        "000001",
        Period.DAILY,
        0,
        8,
        Adjust.QFQ,
        "start",
    )


def test_fetch_bars_rejects_unsupported_market(tmp_path: Path) -> None:
    source, _ = _make_source(tmp_path)
    with pytest.raises(DataSourceError, match="unsupported market"):
        source.fetch_bars(BarRequest("XX", "600519", count=10))


def test_empty_frame_is_not_cached(tmp_path: Path) -> None:
    source, client = _make_source(tmp_path, frame=pd.DataFrame())
    bars = source.fetch_bars(BarRequest("SH", "601059", count=2500))
    assert bars == []
    assert client.calls == 1
    stats = source._cache.stats()
    assert stats["bar_series"]["count"] == 0


def test_fetch_quote_caches(tmp_path: Path) -> None:
    quotes = pd.DataFrame([{"market": 1, "code": "600183", "name": "生益科技", "close": 12.34}])
    source, client = _make_source(tmp_path, quotes=quotes)

    first = source.fetch_quote(QuoteRequest("SH", "600183"))
    assert first is not None
    assert first["close"] == 12.34
    assert first["market"] == "SH"
    assert client.calls == 1

    cached = source.fetch_quote(QuoteRequest("SH", "600183"))
    assert cached == first
    assert client.calls == 1


def test_source_version_and_close(tmp_path: Path) -> None:
    source, client = _make_source(tmp_path)
    assert source.source_version == easy_tdx.__version__
    source.fetch_bars(BarRequest("SH", "600519", count=5))
    source.close()
    assert client.closed


def test_ping_reports_reachable(tmp_path: Path) -> None:
    source, _ = _make_source(tmp_path)
    result = source.ping()
    assert result["reachable"] is True
    assert result["servers"] == ["python-api"]


def test_transient_failure_reconnects_once(tmp_path: Path) -> None:
    good = FakeMacClient(_kline_frame())
    calls = {"n": 0}

    def factory() -> FakeMacClient:
        calls["n"] += 1
        if calls["n"] == 1:
            bad = FakeMacClient(_kline_frame())

            def raiser(*args: Any, **kwargs: Any) -> Any:
                raise ConnectionError("drop")

            bad.get_stock_kline = raiser
            return bad
        return good

    source = EasyTdxSource(db_path=tmp_path / "cache.db", client_factory=factory)
    bars = source.fetch_bars(BarRequest("SH", "600519", count=5))
    assert len(bars) == 5
    assert good.calls == 1
