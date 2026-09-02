from __future__ import annotations

import json
from datetime import date, time

import pandas as pd
import pytest

from research.order_flow.aggregate import (
    aggregate_transactions_to_bars,
    resolve_transaction_alignment,
    session_bar_mask,
)
from research.order_flow.backtest import prepare_backtest_frame, run_order_flow_backtest
from research.order_flow.collector import EasyTdxCollector
from research.order_flow.config import OrderFlowConfig
from research.order_flow.features import compute_order_flow_features
from research.order_flow.normalize import (
    classify_session,
    normalize_bar_frame,
    normalize_transaction_frame,
    parse_symbol,
)
from research.order_flow.run import _config_from_args, _fixture_data, _parser, build_feature_frame


def _bars(days: int = 24) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_index, day in enumerate(pd.bdate_range("2026-01-05", periods=days)):
        for slot in range(2):
            timestamp = day + pd.Timedelta(hours=9, minutes=30 + slot * 5)
            close = 10.0 + day_index * 0.02 + slot * 0.05
            rows.append(
                {
                    "timestamp": timestamp,
                    "open": close - 0.02,
                    "high": close + 0.08,
                    "low": close - 0.08,
                    "close": close,
                    "volume": 1_000.0,
                    "amount": close * 1_000,
                    "symbol": "SH:688183",
                }
            )
    return normalize_bar_frame(pd.DataFrame(rows), symbol="SH:688183", bar_minutes=5)


def _transactions(bars: pd.DataFrame, *, missing_last: bool = False) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    # The helper uses each bar's date so it does not assume one request spans multiple days.
    for day in bars["timestamp"].dt.date.drop_duplicates():
        day_bars = bars.loc[bars["timestamp"].dt.date.eq(day)]
        day_rows = []
        for row in day_bars.itertuples():
            day_rows.extend(
                [
                    {
                        "time": pd.Timestamp(row.timestamp).time().replace(second=10),
                        "price": row.close,
                        "vol": 6,
                        "trade_count": 2,
                        "bs_flag": 0,
                    },
                    {
                        "time": pd.Timestamp(row.timestamp).time().replace(second=20),
                        "price": row.close,
                        "vol": 4,
                        "trade_count": 2,
                        "bs_flag": 1,
                    },
                ]
            )
        if missing_last and day == bars["timestamp"].dt.date.iloc[-1]:
            day_rows = day_rows[:-2]
        pieces.append(
            normalize_transaction_frame(pd.DataFrame(day_rows), trade_date=day, symbol="SH:688183")
        )
    return pd.concat(pieces, ignore_index=True)


def test_symbol_and_session_mapping_is_explicit() -> None:
    assert parse_symbol("688183") == ("SH", "688183", "SH:688183")
    assert parse_symbol("SZ 000001") == ("SZ", "000001", "SZ:000001")
    assert classify_session(time(9, 20)) == "auction"
    assert classify_session(time(10, 0)) == "continuous"
    assert classify_session(time(15, 5)) == "after_hours"


def test_transaction_normalization_preserves_raw_units_and_direction() -> None:
    raw = pd.DataFrame(
        {
            "time": ["09:30:01", "09:30:02", "09:30:03", "15:20:00"],
            "price": [10.0, 10.0, 10.0, 10.0],
            "vol": [3, 2, 1, 4],
            "trade_count": [1, 1, 1, 1],
            "bs_flag": [0, 1, 2, 5],
        }
    )
    normalized = normalize_transaction_frame(raw, trade_date=date(2026, 1, 5), symbol="SH:688183")
    assert normalized["raw_volume"].tolist() == [3, 2, 1, 4]
    assert normalized["volume_shares"].tolist() == [300.0, 200.0, 100.0, 400.0]
    assert normalized["direction"].tolist() == [1, -1, 0, 0]
    assert normalized["included"].tolist() == [True, True, True, False]
    assert normalized.loc[normalized["bs_flag"].eq(5), "direction_label"].iloc[0] == "after_hours"


def test_unknown_direction_policy_can_fail_or_drop() -> None:
    raw = pd.DataFrame(
        {"time": ["09:30:00"], "price": [10], "vol": [1], "trade_count": [1], "bs_flag": [9]}
    )
    with pytest.raises(ValueError, match="unknown bs_flag"):
        normalize_transaction_frame(
            raw, trade_date=20260105, symbol="SH:688183", unknown_direction_policy="error"
        )
    dropped = normalize_transaction_frame(
        raw, trade_date=20260105, symbol="SH:688183", unknown_direction_policy="drop"
    )
    assert dropped.empty


def test_transaction_normalization_rejects_non_integer_direction() -> None:
    raw = pd.DataFrame(
        {"time": ["09:30:00"], "price": [10], "vol": [1], "trade_count": [1], "bs_flag": [0.5]}
    )
    with pytest.raises(ValueError, match="non-integer bs_flag"):
        normalize_transaction_frame(raw, trade_date=20260105, symbol="SH:688183")


def test_pagination_audit_detects_repeated_page() -> None:
    page = pd.DataFrame(
        {
            "time": ["09:30:01", "09:30:02"],
            "price": [10.0, 10.0],
            "vol": [1, 1],
            "trade_count": [1, 1],
            "bs_flag": [0, 1],
        }
    )

    class FakeClient:
        def get_transactions(self, market, code, *, count, start, date):
            del market, code, count, date
            return page.copy() if start in {0, 2} else pd.DataFrame()

    fetched = EasyTdxCollector(client=FakeClient()).fetch_transactions_for_date(
        FakeClient(),
        1,
        "688183",
        20260105,
        config=OrderFlowConfig(volume_baseline_sessions=2, min_history_sessions=2),
        symbol="SH:688183",
        max_rows=10,
        page_size=2,
    )
    assert fetched.truncated is True
    assert fetched.pages[-1].repeated_page is True
    assert len(fetched.frame) == 2


def test_aggregation_keeps_missing_transaction_as_missing() -> None:
    bars = _bars(days=2)
    tx = _transactions(bars, missing_last=True)
    aggregated = aggregate_transactions_to_bars(bars, tx, symbol="SH:688183", bar_minutes=5)
    assert aggregated.iloc[0]["buy_volume"] == 600.0
    assert bool(aggregated.iloc[-1]["transaction_observed"]) is False
    assert pd.isna(aggregated.iloc[-1]["delta"])


def test_empty_session_aggregation_keeps_feature_schema() -> None:
    bars = normalize_bar_frame(
        pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]),
        symbol="SH:688183",
        bar_minutes=5,
    )
    aggregated = aggregate_transactions_to_bars(bars, pd.DataFrame(), symbol="SH:688183")
    assert aggregated.empty
    assert {"delta", "delta_ratio", "slot_key"}.issubset(aggregated.columns)


def test_feature_baseline_is_causal_and_signal_columns_are_configurable() -> None:
    bars = _bars(days=24)
    tx = _transactions(bars)
    config = OrderFlowConfig(
        volume_baseline_sessions=2,
        min_history_sessions=2,
        entry_delta_ratio=0.1,
        entry_rvol=0.5,
        entry_close_location=0.5,
        use_vwap_filter=False,
    )
    base = compute_order_flow_features(
        aggregate_transactions_to_bars(bars, tx, symbol="SH:688183"), config
    )
    changed = bars.copy()
    changed.loc[changed.index[-1], "volume"] = 99_999
    changed_features = compute_order_flow_features(
        aggregate_transactions_to_bars(changed, tx, symbol="SH:688183"), config
    )
    for column in ("volume_baseline", "relative_volume", "delta_ratio", "of_entry_signal"):
        pd.testing.assert_series_equal(
            base.loc[:-2, column].reset_index(drop=True),
            changed_features.loc[:-2, column].reset_index(drop=True),
            check_names=False,
        )
    assert base["of_history_ready"].sum() > 0


def test_intraday_backtest_uses_unique_execution_keys_and_daily_metrics() -> None:
    bars, tx, _ = _fixture_data("SH:688183", days=45)
    features = build_feature_frame(
        bars,
        tx,
        symbol="SH:688183",
        config=OrderFlowConfig(
            volume_baseline_sessions=2,
            min_history_sessions=2,
            entry_rvol=1.1,
            use_vwap_filter=False,
        ),
    )
    execution = prepare_backtest_frame(features)
    assert execution["datetime"].is_unique
    result = run_order_flow_backtest(
        features,
        config=OrderFlowConfig(
            volume_baseline_sessions=2,
            min_history_sessions=2,
            entry_rvol=1.1,
            use_vwap_filter=False,
        ),
    )
    assert result.corrected_performance["sessions"] == 45
    assert (
        result.raw_engine_performance["annual_return"]
        != result.corrected_performance["annual_return"]
    )
    if not result.result.trades.empty:
        assert (
            result.result.trades["datetime"]
            .astype(int)
            .between(execution["datetime"].min(), execution["datetime"].max())
            .all()
        )


def test_corrected_metrics_include_initial_cash_in_first_day_drawdown() -> None:
    rows = []
    for day, close in (("2026-01-05", 9.0), ("2026-01-06", 9.0)):
        timestamp = pd.Timestamp(f"{day} 09:30")
        rows.append(
            {
                "timestamp": timestamp,
                "symbol": "SH:688183",
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000.0,
                "amount": close * 1_000,
                "of_data_valid": 1.0,
                "of_entry_signal": day == "2026-01-05",
                "of_exit_signal": day == "2026-01-06",
                "session_date_key": int(day.replace("-", "")),
                "next_session_date_key": int(day.replace("-", "")),
                "is_session_last": False,
            }
        )
    result = run_order_flow_backtest(
        pd.DataFrame(rows),
        config=OrderFlowConfig(
            t_plus_one=False,
            min_hold_bars=1,
            max_hold_bars=None,
            use_vwap_filter=False,
        ),
    )
    assert result.corrected_performance["sessions"] == 2
    assert result.corrected_performance["max_drawdown"] > 0.0


def test_config_rejects_engine_unsupported_lot_size() -> None:
    with pytest.raises(ValueError, match="lot_size=100"):
        OrderFlowConfig(lot_size=200)


def test_config_rejects_non_integer_bar_interval() -> None:
    with pytest.raises(ValueError, match="bar_minutes"):
        OrderFlowConfig(bar_minutes=5.0)


def test_auto_alignment_detects_fixture_and_mac_endpoint_labels() -> None:
    start_bars = pd.DataFrame(
        {"timestamp": pd.to_datetime(["2026-01-05 09:30", "2026-01-05 13:00"])}
    )
    assert resolve_transaction_alignment(start_bars, bar_minutes=5, alignment="auto") == "floor"
    endpoint_rows = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-01-05 09:35",
                    "2026-01-05 09:40",
                    "2026-01-05 11:30",
                    "2026-01-05 13:05",
                    "2026-01-05 13:10",
                    "2026-01-05 14:55",
                    "2026-01-05 15:00",
                ]
            )
        }
    )
    # These labels have no 09:30/13:00 left-boundary row and mirror the observed MAC response.
    assert resolve_transaction_alignment(endpoint_rows, bar_minutes=5, alignment="auto") == "ceil"
    endpoint_mask = session_bar_mask(endpoint_rows, alignment="ceil")
    assert endpoint_mask.tolist() == [True, True, True, True, True, True, False]


def test_endpoint_alignment_maps_session_boundaries_to_right_endpoint() -> None:
    day = pd.Timestamp("2026-01-05")
    bars = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": day + pd.Timedelta(hours=9, minutes=35),
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10.1,
                    "volume": 300.0,
                },
                {
                    "timestamp": day + pd.Timedelta(hours=13, minutes=5),
                    "open": 10.1,
                    "high": 10.3,
                    "low": 9.9,
                    "close": 10.2,
                    "volume": 500.0,
                },
            ]
        ),
        symbol="SH:688183",
        bar_minutes=5,
    )
    transactions = normalize_transaction_frame(
        pd.DataFrame(
            {
                "time": ["09:30:00", "09:34:59", "13:00:00"],
                "price": [10.0, 10.1, 10.2],
                "vol": [1, 2, 5],
                "trade_count": [1, 1, 1],
                "bs_flag": [0, 1, 0],
            }
        ),
        trade_date=day,
        symbol="SH:688183",
    )
    aggregated = aggregate_transactions_to_bars(
        bars,
        transactions,
        symbol="SH:688183",
        bar_minutes=5,
        transaction_alignment="auto",
    )
    assert aggregated["transaction_alignment"].tolist() == ["ceil", "ceil"]
    assert (
        aggregated.set_index("timestamp").loc[
            day + pd.Timedelta(hours=9, minutes=35), "total_transaction_volume"
        ]
        == 300.0
    )
    assert (
        aggregated.set_index("timestamp").loc[
            day + pd.Timedelta(hours=13, minutes=5), "total_transaction_volume"
        ]
        == 500.0
    )


def test_json_config_is_loaded_and_explicit_cli_values_win(tmp_path) -> None:
    path = tmp_path / "order-flow.json"
    path.write_text(
        json.dumps(
            {
                "entry_rvol": 2.0,
                "entry_delta_ratio": 0.3,
                "transaction_alignment": "ceil",
                "max_hold_bars": None,
                "position_mode": "percent",
                "position_fraction": 0.4,
            }
        ),
        encoding="utf-8",
    )
    args = _parser().parse_args(["--config", str(path), "--entry-rvol", "1.5"])
    config = _config_from_args(args)
    assert config.entry_rvol == 1.5
    assert config.entry_delta_ratio == 0.3
    assert config.transaction_alignment == "ceil"
    assert config.max_hold_bars is None
    assert config.position_mode == "percent"
    assert config.position_fraction == 0.4


def test_optional_quality_filters_can_disable_all_ineligible_bars() -> None:
    bars = _bars(days=24)
    transactions = _transactions(bars)
    aggregate = aggregate_transactions_to_bars(bars, transactions, symbol="SH:688183")
    coverage_filtered = compute_order_flow_features(
        aggregate,
        OrderFlowConfig(
            volume_baseline_sessions=2,
            min_history_sessions=2,
            min_transaction_coverage=1.01,
        ),
    )
    large_filtered = compute_order_flow_features(
        aggregate,
        OrderFlowConfig(
            volume_baseline_sessions=2,
            min_history_sessions=2,
            min_large_trade_share=0.01,
        ),
    )
    assert coverage_filtered["of_data_valid"].sum() == 0
    assert large_filtered["of_data_valid"].sum() == 0
    assert coverage_filtered["transaction_coverage"].notna().all()


def test_missing_transaction_bar_leaves_a_cvd_gap_marker() -> None:
    bars = _bars(days=2)
    transactions = _transactions(bars, missing_last=True)
    features = compute_order_flow_features(
        aggregate_transactions_to_bars(bars, transactions, symbol="SH:688183"),
        OrderFlowConfig(volume_baseline_sessions=2, min_history_sessions=2),
    )
    assert pd.isna(features.iloc[-1]["cvd"])
    assert pd.isna(features.iloc[-1]["session_cvd"])
    assert pd.notna(features.iloc[-2]["cvd"])
    assert pd.notna(features.iloc[-2]["session_cvd"])


def test_fixture_supports_configured_bar_interval() -> None:
    bars, transactions, provenance = _fixture_data("SH:688183", days=2, bar_minutes=15)
    assert provenance["period"] == "15MIN"
    assert bars["timestamp"].dt.minute.isin({0, 15, 30, 45}).all()
    assert not transactions.empty
