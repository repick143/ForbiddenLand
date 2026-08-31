from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from forbiddenland.config import CompatibilityConfig
from forbiddenland.infrastructure.market_data.akshare_provider import AkShareMarketProvider
from research.vsa.run import (
    DEMO_NAME,
    DEMO_SYMBOL,
    build_fixture,
    build_report,
    fetch_demo_data_with_metadata,
    generate_vsa_frame,
    normalize_demo_frame,
    recent_date_window,
    resolve_date_window,
    run_backtest,
    write_report,
)


def test_fixture_runs_akquant_with_recorded_indicators_and_real_stop_fill() -> None:
    features = generate_vsa_frame(build_fixture())
    result = run_backtest(features)

    metrics = result.metrics_df
    assert int(metrics.loc["total_bars", "value"]) == 100
    assert int(metrics.loc["closed_trade_count", "value"]) >= 1
    assert not result.indicator_df().empty
    assert {
        "vsa_volume_ratio",
        "vsa_spread_ratio",
        "vsa_clv",
        "vsa_candidate_code",
        "vsa_confirmed_signal",
    }.issubset(set(result.indicator_definitions["indicator_key"]))

    indicators = result.indicator_df()
    ratio_rows = indicators.loc[indicators["indicator_key"].eq("vsa_volume_ratio")]
    expected_ratio_rows = features.loc[
        features["vsa_data_valid"]
        & features["vsa_history_ready"]
        & features["vsa_volume_baseline"].gt(0.0)
    ]
    assert len(ratio_rows) == len(expected_ratio_rows)
    assert ratio_rows["value"].ne(0.0).all()
    stop_rows = indicators.loc[indicators["indicator_key"].eq("vsa_stop_price")]
    assert len(stop_rows) == int(features["vsa_stop_price"].gt(0.0).sum())

    orders = result.orders_df
    stop_orders = orders.loc[orders["tag"].eq("vsa-stop-loss")]
    assert not stop_orders.empty
    assert set(stop_orders["status"]).issubset({"filled", "cancelled"})
    assert (
        stop_orders["created_at"] > orders.loc[orders["side"].eq("buy"), "created_at"].min()
    ).all()


def test_report_and_indicator_payload_are_json_serializable(tmp_path: Path) -> None:
    features = generate_vsa_frame(build_fixture())
    result = run_backtest(features)
    report = build_report(
        result,
        features=features,
        source="deterministic synthetic fixture (offline test only)",
        storage="in-memory DataFrame",
        backend="fixture",
    )
    report_path = tmp_path / "vsa.json"
    indicators_path = tmp_path / "indicators.json"
    write_report(report, report_path)
    result.export_indicators(str(indicators_path), format="json")

    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    indicator_payload = json.loads(indicators_path.read_text(encoding="utf-8"))
    assert loaded["symbol"] == {"code": DEMO_SYMBOL, "name": DEMO_NAME}
    assert loaded["data"]["backend"] == "fixture"
    assert loaded["validation"]["out_of_sample"] is False
    assert len(indicator_payload["points"]) > 0


def test_normalize_demo_frame_preserves_string_codes_and_rejects_bad_ohlcv() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2024-01-02"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.2],
            "volume": [100.0],
            "symbol": ["000001"],
        }
    )
    normalized = normalize_demo_frame(frame)
    assert normalized.loc[0, "symbol"] == "000001"
    assert normalized.loc[0, "timestamp"] == pd.Timestamp("2024-01-02")

    bad = frame.copy()
    bad.loc[0, "high"] = 8.0
    try:
        normalize_demo_frame(bad)
    except ValueError as exc:
        assert "OHLC ordering" in str(exc)
    else:
        raise AssertionError("invalid OHLC ordering should be rejected")


def test_normalize_demo_frame_rejects_missing_symbol_values() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2024-01-02"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.2],
            "volume": [100.0],
            "symbol": [None],
        }
    )

    try:
        normalize_demo_frame(frame)
    except ValueError as exc:
        assert "empty symbol" in str(exc)
    else:
        raise AssertionError("missing symbol should be rejected")


def test_run_backtest_reports_missing_vsa_columns() -> None:
    with pytest.raises(ValueError, match="missing columns: symbol"):
        run_backtest(
            pd.DataFrame(
                {
                    "timestamp": [pd.Timestamp("2024-01-02")],
                    "open": [10.0],
                    "high": [10.5],
                    "low": [9.5],
                    "close": [10.2],
                    "volume": [100.0],
                }
            )
        )


def test_recent_date_window_uses_calendar_months_and_month_end_clamping() -> None:
    assert recent_date_window(date(2026, 8, 31)) == ("20260531", "20260831")
    assert recent_date_window(date(2024, 5, 31)) == ("20240229", "20240531")


def test_resolve_date_window_supports_partial_overrides() -> None:
    assert resolve_date_window(end_date="20260831") == ("20260531", "20260831")
    assert resolve_date_window("20260701", as_of=date(2026, 8, 31)) == (
        "20260701",
        "20260831",
    )
    with pytest.raises(ValueError, match="start_date must not be later"):
        resolve_date_window("20260901", "20260831")


def test_remote_demo_path_uses_provider_and_preserves_provenance() -> None:
    calls: list[dict[str, object]] = []
    source_frame = pd.DataFrame(
        {
            "日期": ["2024-01-02", "2024-01-03"],
            "股票代码": [DEMO_SYMBOL, DEMO_SYMBOL],
            "开盘": [10.0, 10.2],
            "收盘": [10.2, 10.4],
            "最高": [10.5, 10.6],
            "最低": [9.8, 10.0],
            "成交量": [1000.0, 1200.0],
        }
    )

    def fetch(**kwargs: object) -> pd.DataFrame:
        calls.append(kwargs)
        return source_frame

    retrieved_at = datetime(2024, 2, 1, tzinfo=UTC)
    provider = AkShareMarketProvider(
        CompatibilityConfig(
            backend="remote",
            remote_alternate_source=False,
            remote_cache_enabled=False,
        ),
        client=SimpleNamespace(stock_zh_a_hist=fetch),
        clock=lambda: retrieved_at,
    )

    batch = fetch_demo_data_with_metadata(
        "20240101",
        "20240131",
        "qfq",
        provider=provider,
    )

    assert batch.frame["symbol"].tolist() == [DEMO_SYMBOL, DEMO_SYMBOL]
    assert batch.source == "AkShare remote provider"
    assert batch.storage == "remote response"
    assert batch.backend == "remote"
    assert batch.retrieved_at_utc == retrieved_at
    assert batch.cache_hit is False
    assert calls[0]["symbol"] == DEMO_SYMBOL
    assert calls[0]["start_date"] == "20240101"
    assert calls[0]["end_date"] == "20240131"
    assert calls[0]["adjust"] == "qfq"
