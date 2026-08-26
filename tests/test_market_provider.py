from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from forbiddenland.application.market_service import MarketDataProviderError
from forbiddenland.config import CompatibilityConfig
from forbiddenland.domain.market import MarketQuery
from forbiddenland.infrastructure.market_data.akshare_provider import AkShareMarketProvider


def _query() -> MarketQuery:
    return MarketQuery(
        symbol="688256.SH",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        adjust="qfq",
    )


def _client(frame: object) -> SimpleNamespace:
    return SimpleNamespace(stock_zh_a_hist=lambda **_: frame)


def test_provider_maps_akshare_columns_and_records_provenance() -> None:
    frame = pd.DataFrame(
        {
            "日期": ["2024-01-02", "2024-01-03"],
            "股票代码": ["688256", "688256"],
            "开盘": [10.0, 10.2],
            "收盘": [10.2, 10.6],
            "最高": [10.5, 10.8],
            "最低": [9.8, 10.0],
            "成交量": [1000.0, 1200.0],
            "成交额": [10000.0, 12000.0],
            "涨跌额": [0.2, 0.4],
            "涨跌幅": [2.0, 3.9],
            "换手率": [1.2, 1.4],
        }
    )
    provider = AkShareMarketProvider(
        CompatibilityConfig(backend="remote"),
        client=_client(frame),
        clock=lambda: datetime(2024, 2, 1, tzinfo=UTC),
    )

    result = provider.fetch_history(_query())

    assert result.backend == "remote"
    assert result.storage == "remote response"
    assert result.local_snapshot_review_required is False
    assert result.retrieved_at_utc == datetime(2024, 2, 1, tzinfo=UTC)
    assert [bar.date for bar in result.bars] == [date(2024, 1, 2), date(2024, 1, 3)]
    assert result.bars[-1].close == 10.6
    assert result.summary.period_change_percent == pytest.approx(3.9215686)


def test_provider_marks_explicit_local_backend_for_review() -> None:
    frame = pd.DataFrame(
        {
            "日期": [date(2024, 1, 2)],
            "开盘": [10.0],
            "收盘": [10.2],
            "最高": [10.5],
            "最低": [9.8],
            "成交量": [1000.0],
        }
    )
    provider = AkShareMarketProvider(
        CompatibilityConfig(backend="local"),
        client=_client(frame),
    )

    result = provider.fetch_history(_query())

    assert result.backend == "local"
    assert result.storage == "DuckDB/Parquet"
    assert result.local_snapshot_review_required is True


def test_provider_rejects_malformed_required_values() -> None:
    frame = pd.DataFrame(
        {
            "日期": ["2024-01-02"],
            "开盘": ["not-a-number"],
            "收盘": [10.2],
            "最高": [10.5],
            "最低": [9.8],
            "成交量": [1000.0],
        }
    )
    provider = AkShareMarketProvider(
        CompatibilityConfig(backend="remote"),
        client=_client(frame),
    )

    with pytest.raises(MarketDataProviderError, match="non-numeric open"):
        provider.fetch_history(_query())


@pytest.mark.parametrize("missing", [None, "", "--", "NA", float("nan"), pd.NA])
def test_provider_preserves_optional_missing_values(missing: object) -> None:
    frame = pd.DataFrame(
        {
            "日期": ["2024-01-02"],
            "开盘": [10.0],
            "收盘": [10.2],
            "最高": [10.5],
            "最低": [9.8],
            "成交量": [1000.0],
            "成交额": [missing],
        }
    )
    provider = AkShareMarketProvider(
        CompatibilityConfig(backend="remote"),
        client=_client(frame),
    )

    result = provider.fetch_history(_query())

    assert result.bars[0].amount is None


def test_provider_rejects_malformed_optional_values() -> None:
    frame = pd.DataFrame(
        {
            "日期": ["2024-01-02"],
            "开盘": [10.0],
            "收盘": [10.2],
            "最高": [10.5],
            "最低": [9.8],
            "成交量": [1000.0],
            "成交额": ["not-a-number"],
        }
    )
    provider = AkShareMarketProvider(
        CompatibilityConfig(backend="remote"),
        client=_client(frame),
    )

    with pytest.raises(MarketDataProviderError, match="non-numeric amount"):
        provider.fetch_history(_query())
