from __future__ import annotations

from datetime import UTC, date, datetime
from http.client import RemoteDisconnected
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
        CompatibilityConfig(backend="remote", remote_cache_enabled=False),
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


def test_provider_labels_hybrid_storage_without_claiming_a_single_source() -> None:
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
        CompatibilityConfig(backend="hybrid", allow_remote_fallback=True),
        client=_client(frame),
    )

    result = provider.fetch_history(_query())

    assert result.storage == "DuckDB/Parquet or remote response (explicit fallback)"
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
        CompatibilityConfig(backend="remote", remote_cache_enabled=False),
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
        CompatibilityConfig(backend="remote", remote_cache_enabled=False),
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
        CompatibilityConfig(backend="remote", remote_cache_enabled=False),
        client=_client(frame),
    )

    with pytest.raises(MarketDataProviderError, match="non-numeric amount"):
        provider.fetch_history(_query())


def test_provider_lists_stock_concept_and_curated_index_assets() -> None:
    client = SimpleNamespace(
        stock_info_a_code_name=lambda: pd.DataFrame({"code": ["688256"], "name": ["寒武纪"]}),
        stock_board_concept_name_ths=lambda: pd.DataFrame(
            {"name": ["MLCC概念"], "code": ["886112.TI"]}
        ),
    )
    provider = AkShareMarketProvider(CompatibilityConfig(backend="remote"), client=client)

    assert [(item.code, item.name) for item in provider.list_assets("stock")] == [
        ("688256", "寒武纪")
    ]
    assert [(item.code, item.name) for item in provider.list_assets("concept")] == [
        ("886112.TI", "MLCC概念")
    ]
    assert ("sh000001", "上证指数") in [
        (item.code, item.name) for item in provider.list_assets("index")
    ]


def test_provider_maps_remote_index_history() -> None:
    calls: list[dict[str, object]] = []

    def stock_zh_index_daily_em(**kwargs: object) -> object:
        calls.append(kwargs)
        return pd.DataFrame(
            {
                "date": ["2024-01-02"],
                "open": [3000.0],
                "close": [3012.0],
                "high": [3020.0],
                "low": [2990.0],
                "volume": [100000.0],
                "amount": [200000.0],
            }
        )

    provider = AkShareMarketProvider(
        CompatibilityConfig(backend="remote", remote_cache_enabled=False),
        client=SimpleNamespace(stock_zh_index_daily_em=stock_zh_index_daily_em),
    )
    query = MarketQuery(
        symbol="sh000001",
        asset_type="index",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )

    result = provider.fetch_history(query)

    assert calls == [{"symbol": "sh000001", "start_date": "20240101", "end_date": "20240131"}]
    assert result.query.asset_type == "index"
    assert result.bars[0].close == 3012.0


def test_provider_resolves_concept_code_and_maps_local_shape() -> None:
    calls: list[dict[str, object]] = []

    def stock_board_concept_index_ths(**kwargs: object) -> object:
        calls.append(kwargs)
        return pd.DataFrame(
            {
                "日期": [date(2024, 1, 2)],
                "开盘价": [1000.0],
                "收盘价": [1010.0],
                "最高价": [1020.0],
                "最低价": [995.0],
                "成交量": [pd.NA],
                "成交额": [pd.NA],
            }
        )

    client = SimpleNamespace(
        stock_board_concept_name_ths=lambda: pd.DataFrame(
            {"name": ["MLCC概念"], "code": ["886112.TI"]}
        ),
        stock_board_concept_index_ths=stock_board_concept_index_ths,
    )
    provider = AkShareMarketProvider(CompatibilityConfig(backend="local"), client=client)
    query = MarketQuery(
        symbol="886112.TI",
        asset_type="concept",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )

    result = provider.fetch_history(query)

    assert calls == [{"symbol": "MLCC概念", "start_date": "20240101", "end_date": "20240131"}]
    assert result.bars[0].close == 1010.0
    assert result.bars[0].volume is None
    assert result.bars[0].amount is None


def test_non_stock_query_rejects_adjustment() -> None:
    with pytest.raises(ValueError, match="stock assets only"):
        MarketQuery(
            symbol="sh000001",
            asset_type="index",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            adjust="qfq",
        )


def _tx_frame() -> object:
    return pd.DataFrame(
        {
            "date": ["2024-01-03", "2024-01-02"],
            "open": [12.0, 10.0],
            "close": [12.0, 10.0],
            "high": [12.5, 10.5],
            "low": [11.5, 9.5],
            "volume": [1200.0, 1000.0],
            "amount": [12000.0, 10000.0],
            # AkShare's Tencent endpoint returns turnover as a fraction.
            "turnover": [0.02, 0.01],
        }
    )


def test_provider_retries_transient_remote_disconnect_with_exponential_backoff() -> None:
    calls: list[dict[str, object]] = []
    delays: list[float] = []
    frame = pd.DataFrame(
        {
            "日期": ["2024-01-02"],
            "开盘": [10.0],
            "收盘": [10.2],
            "最高": [10.5],
            "最低": [9.8],
            "成交量": [1000.0],
        }
    )

    def fetch(**kwargs: object) -> object:
        calls.append(kwargs)
        if len(calls) < 3:
            raise RemoteDisconnected("remote closed connection")
        return frame

    config = CompatibilityConfig(
        backend="remote",
        remote_retry_attempts=3,
        remote_retry_backoff_seconds=0.25,
        remote_alternate_source=False,
        remote_cache_enabled=False,
    )
    provider = AkShareMarketProvider(
        config,
        client=SimpleNamespace(stock_zh_a_hist=fetch),
        sleeper=delays.append,
    )

    result = provider.fetch_history(_query())

    assert len(calls) == 3
    assert delays == [0.25, 0.5]
    assert calls[0]["timeout"] == 15.0
    assert result.source == "AkShare remote provider"


def test_provider_does_not_retry_non_transient_provider_errors() -> None:
    calls: list[dict[str, object]] = []
    delays: list[float] = []

    def fetch(**kwargs: object) -> object:
        calls.append(kwargs)
        raise ValueError("invalid request")

    provider = AkShareMarketProvider(
        CompatibilityConfig(
            backend="remote", remote_alternate_source=True, remote_cache_enabled=False
        ),
        client=SimpleNamespace(stock_zh_a_hist=fetch, stock_zh_a_hist_tx=fetch),
        sleeper=delays.append,
    )

    with pytest.raises(MarketDataProviderError, match="invalid request"):
        provider.fetch_history(_query())

    assert len(calls) == 1
    assert delays == []


def test_provider_uses_tencent_endpoint_after_primary_network_failure() -> None:
    primary_calls: list[dict[str, object]] = []
    alternate_calls: list[dict[str, object]] = []

    def primary(**kwargs: object) -> object:
        primary_calls.append(kwargs)
        raise RemoteDisconnected("remote closed connection")

    def alternate(**kwargs: object) -> object:
        alternate_calls.append(kwargs)
        return _tx_frame()

    provider = AkShareMarketProvider(
        CompatibilityConfig(
            backend="remote",
            remote_retry_attempts=2,
            remote_retry_backoff_seconds=0,
            remote_alternate_source=True,
            remote_cache_enabled=False,
        ),
        client=SimpleNamespace(stock_zh_a_hist=primary, stock_zh_a_hist_tx=alternate),
    )

    result = provider.fetch_history(_query())

    assert len(primary_calls) == 2
    assert len(alternate_calls) == 1
    assert alternate_calls[0] == {
        "symbol": "688256",
        "start_date": "20240101",
        "end_date": "20240131",
        "adjust": "qfq",
        "timeout": 15.0,
    }
    assert result.source == "AkShare remote provider (Tencent historical fallback)"
    assert result.storage == "remote response (Tencent historical fallback)"
    assert [bar.date for bar in result.bars] == [date(2024, 1, 2), date(2024, 1, 3)]
    assert result.bars[0].turnover_rate == pytest.approx(1.0)
    assert result.bars[1].turnover_rate == pytest.approx(2.0)
    assert result.bars[0].change is None
    assert result.bars[1].change == pytest.approx(2.0)
    assert result.bars[1].change_percent == pytest.approx(20.0)


def test_provider_can_disable_alternate_remote_endpoint() -> None:
    calls: list[str] = []

    def primary(**_: object) -> object:
        calls.append("primary")
        raise RemoteDisconnected("remote closed connection")

    def alternate(**_: object) -> object:
        calls.append("alternate")
        return _tx_frame()

    provider = AkShareMarketProvider(
        CompatibilityConfig(
            backend="remote",
            remote_retry_attempts=1,
            remote_alternate_source=False,
            remote_cache_enabled=False,
        ),
        client=SimpleNamespace(stock_zh_a_hist=primary, stock_zh_a_hist_tx=alternate),
    )

    with pytest.raises(MarketDataProviderError, match="remote closed connection"):
        provider.fetch_history(_query())

    assert calls == ["primary"]


def test_provider_reports_primary_and_alternate_failures() -> None:
    def fail(**_: object) -> object:
        raise RemoteDisconnected("remote closed connection")

    provider = AkShareMarketProvider(
        CompatibilityConfig(
            backend="remote",
            remote_retry_attempts=1,
            remote_alternate_source=True,
            remote_cache_enabled=False,
        ),
        client=SimpleNamespace(stock_zh_a_hist=fail, stock_zh_a_hist_tx=fail),
    )

    with pytest.raises(
        MarketDataProviderError,
        match="Primary AkShare stock_zh_a_hist.*Tencent stock_zh_a_hist_tx fallback",
    ):
        provider.fetch_history(_query())


def _cache_frame(close: float = 10.2) -> object:
    return pd.DataFrame(
        {
            "日期": ["2024-01-02"],
            "股票代码": ["688256"],
            "开盘": [10.0],
            "收盘": [close],
            "最高": [10.5],
            "最低": [9.8],
            "成交量": [1000.0],
        }
    )


def test_provider_caches_remote_daily_response_and_preserves_provenance(tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def fetch(**kwargs: object) -> object:
        calls.append(kwargs)
        return _cache_frame()

    retrieved_at = datetime(2024, 2, 1, tzinfo=UTC)
    provider = AkShareMarketProvider(
        CompatibilityConfig(
            backend="remote",
            remote_cache_dir=tmp_path,
            remote_retry_attempts=1,
            remote_alternate_source=False,
        ),
        client=SimpleNamespace(stock_zh_a_hist=fetch),
        clock=lambda: retrieved_at,
    )

    first = provider.fetch_history(_query())
    second = provider.fetch_history(_query())

    assert len(calls) == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.source == "AkShare remote provider"
    assert second.storage == "remote response (cache hit)"
    assert second.retrieved_at_utc == retrieved_at
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_provider_refetches_expired_cache_and_never_uses_it_after_failure(tmp_path) -> None:
    calls: list[int] = []
    current_time = [datetime(2024, 2, 1, tzinfo=UTC)]

    def fetch(**_: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            return _cache_frame()
        raise RemoteDisconnected("remote closed connection")

    provider = AkShareMarketProvider(
        CompatibilityConfig(
            backend="remote",
            remote_cache_dir=tmp_path,
            remote_cache_ttl_seconds=60,
            remote_retry_attempts=1,
            remote_alternate_source=False,
        ),
        client=SimpleNamespace(stock_zh_a_hist=fetch),
        clock=lambda: current_time[0],
    )

    provider.fetch_history(_query())
    current_time[0] = datetime(2024, 2, 1, 0, 2, tzinfo=UTC)

    with pytest.raises(MarketDataProviderError, match="remote closed connection"):
        provider.fetch_history(_query())

    assert len(calls) == 2


def test_provider_ignores_corrupt_cache_and_fetches_remote_again(tmp_path) -> None:
    calls: list[int] = []

    def fetch(**_: object) -> object:
        calls.append(1)
        return _cache_frame(close=10.0 + len(calls))

    config = CompatibilityConfig(
        backend="remote",
        remote_cache_dir=tmp_path,
        remote_retry_attempts=1,
        remote_alternate_source=False,
    )
    provider = AkShareMarketProvider(
        config,
        client=SimpleNamespace(stock_zh_a_hist=fetch),
        clock=lambda: datetime(2024, 2, 1, tzinfo=UTC),
    )
    provider.fetch_history(_query())
    cache_path = next(tmp_path.glob("*.json"))
    cache_path.write_text("{not-json", encoding="utf-8")

    result = provider.fetch_history(_query())

    assert len(calls) == 2
    assert result.cache_hit is False
    assert result.bars[0].close == pytest.approx(12.0)


def test_provider_can_disable_remote_cache(tmp_path) -> None:
    calls: list[int] = []

    def fetch(**_: object) -> object:
        calls.append(1)
        return _cache_frame()

    provider = AkShareMarketProvider(
        CompatibilityConfig(
            backend="remote",
            remote_cache_dir=tmp_path,
            remote_cache_enabled=False,
            remote_retry_attempts=1,
            remote_alternate_source=False,
        ),
        client=SimpleNamespace(stock_zh_a_hist=fetch),
    )

    provider.fetch_history(_query())
    provider.fetch_history(_query())

    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == []


def test_provider_keeps_tencent_fallback_cache_separate_from_primary(tmp_path) -> None:
    def primary(**_: object) -> object:
        raise RemoteDisconnected("remote closed connection")

    def alternate(**_: object) -> object:
        return _tx_frame()

    config = CompatibilityConfig(
        backend="remote",
        remote_cache_dir=tmp_path,
        remote_retry_attempts=1,
        remote_alternate_source=True,
    )
    first_provider = AkShareMarketProvider(
        config,
        client=SimpleNamespace(stock_zh_a_hist=primary, stock_zh_a_hist_tx=alternate),
        clock=lambda: datetime(2024, 2, 1, tzinfo=UTC),
    )
    first = first_provider.fetch_history(_query())

    second_calls: list[str] = []

    def unexpected_primary(**_: object) -> object:
        second_calls.append("primary")
        raise AssertionError("a valid fallback cache should avoid the network")

    def unexpected_alternate(**_: object) -> object:
        second_calls.append("alternate")
        raise AssertionError("a valid fallback cache should avoid the network")

    second_provider = AkShareMarketProvider(
        config,
        client=SimpleNamespace(
            stock_zh_a_hist=unexpected_primary,
            stock_zh_a_hist_tx=unexpected_alternate,
        ),
        clock=lambda: datetime(2024, 2, 1, tzinfo=UTC),
    )
    second = second_provider.fetch_history(_query())

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert "Tencent historical fallback" in second.source
    assert second_calls == []
    assert len(list(tmp_path.glob("*.json"))) == 1
