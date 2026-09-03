from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.order_flow.aggregate import aggregate_transactions_to_bars
from research.order_flow.config import (
    ORDER_FLOW_V1_VERSION,
    ORDER_FLOW_V2_VERSION,
    OrderFlowConfig,
)
from research.order_flow.easy_tdx_factor import (
    ORDER_FLOW_V2_FACTOR_NAME,
    OrderFlowParticipationScore,
    OrderFlowV2Score,
    build_easy_tdx_v2_factor_frame,
    compute_order_flow_v2_factor,
    ensure_order_flow_v2_factor_registered,
    order_flow_v2_factor_definition,
)
from research.order_flow.features import compute_order_flow_features
from research.order_flow.features_v2 import (
    V2_SCORE_COMPONENTS,
    compute_order_flow_v2_features,
    summarize_order_flow_v2,
)
from research.order_flow.run import _fixture_data, _parser, build_feature_frame, run
from research.order_flow.strategy import make_order_flow_strategy


def _bars(rows: int = 16, *, second_session: bool = False) -> pd.DataFrame:
    values: list[dict[str, object]] = []
    timestamps: list[pd.Timestamp] = [
        pd.Timestamp("2026-01-05 09:30") + pd.Timedelta(minutes=5 * i) for i in range(rows)
    ]
    if second_session:
        timestamps = [
            pd.Timestamp("2026-01-05 09:30") + pd.Timedelta(minutes=5 * i) for i in range(4)
        ] + [pd.Timestamp("2026-01-05 13:00") + pd.Timedelta(minutes=5 * i) for i in range(4)]
    for index, timestamp in enumerate(timestamps):
        flow = 0.75 if index < len(timestamps) // 2 else -0.75
        values.append(
            {
                "timestamp": timestamp,
                "symbol": "SH:688183",
                "open": 10.0 + index * 0.01,
                "high": 10.2 + index * 0.01,
                "low": 9.8 + index * 0.01,
                "close": 10.1 + index * 0.01,
                "volume": 1_000.0,
                "buy_volume": 500.0 * (1.0 + flow),
                "sell_volume": 500.0 * (1.0 - flow),
                "neutral_volume": 0.0,
                "total_transaction_volume": 1_000.0,
                "delta": flow * 1_000.0,
                "delta_ratio": flow,
                "transaction_observed": True,
                "is_session_bar": True,
                "trade_count": 10.0,
                "trade_volume_squared": 100_000.0,
                "max_trade_volume": 400.0,
                "large_trade_volume": 400.0,
                "large_delta": flow * 400.0,
                "large_delta_ratio": flow,
                "large_trade_share": 0.4,
            }
        )
    return pd.DataFrame(values)


def _config(**overrides: object) -> OrderFlowConfig:
    values: dict[str, object] = {
        "v2_min_observations": 2,
        "v2_percentile_window": 6,
        "v2_flow_window": 4,
        "v2_regime_window": 4,
        "volume_baseline_sessions": 2,
        "min_history_sessions": 2,
        "participation_strong_threshold": 40.0,
        "participation_direction_threshold": 30.0,
    }
    values.update(overrides)
    return OrderFlowConfig(**values)


def test_v2_features_are_bounded_and_expose_causal_signals() -> None:
    source = _bars()
    result = compute_order_flow_v2_features(source, _config())

    assert set(V2_SCORE_COMPONENTS).issubset(result.columns)
    assert result["order_flow_v2_score"].dropna().between(-1.0, 1.0).all()
    assert result["v2_score_confidence"].between(0.0, 1.0).all()
    assert result["v2_direction_entropy"].dropna().between(0.0, 1.0).all()
    assert result["v2_vpin_proxy"].dropna().between(0.0, 1.0).all()
    assert result["v2_participation_state"].iloc[0] == "insufficient"
    summary = summarize_order_flow_v2(result)
    assert summary["v2_valid_bars"] == len(result) - 1
    assert summary["participation_states"]

    changed = source.copy()
    changed.loc[changed.index[-1], "delta"] = -999_000.0
    changed.loc[changed.index[-1], "delta_ratio"] = -0.999
    recalculated = compute_order_flow_v2_features(changed, _config())
    for column in ("v2_flow_pressure", "order_flow_v2_score", "of_v2_entry_signal"):
        pd.testing.assert_series_equal(
            result.loc[:-2, column].reset_index(drop=True),
            recalculated.loc[:-2, column].reset_index(drop=True),
        )


def test_v2_resets_rolling_state_at_lunch_and_data_gaps() -> None:
    source = _bars(second_session=True)
    result = compute_order_flow_v2_features(source, _config())
    afternoon = result.loc[result["timestamp"].eq(pd.Timestamp("2026-01-05 13:00"))].iloc[0]
    assert afternoon["v2_session_part"] == "pm"
    assert afternoon["v2_flow_ema_fast"] == pytest.approx(-0.75)

    source.loc[4, "transaction_observed"] = False
    source.loc[4, "total_transaction_volume"] = np.nan
    source.loc[5, "delta"] = 0.75 * 1_000.0
    result = compute_order_flow_v2_features(source, _config())
    assert bool(result.loc[4, "of_v2_base_valid"]) is False
    assert result.loc[5, "v2_flow_ema_fast"] == pytest.approx(0.75)


def test_v2_session_reset_switch_controls_known_session_breaks() -> None:
    source = _bars(second_session=True)
    reset = compute_order_flow_v2_features(source, _config(v2_reset_each_session=True))
    carry = compute_order_flow_v2_features(source, _config(v2_reset_each_session=False))

    # The morning-to-afternoon break is a deliberate state boundary by default.  Disabling the
    # option carries the fast EMA through that known break while still preserving the timestamp gap
    # in the segment metadata.
    assert reset.loc[4, "v2_flow_ema_fast"] == pytest.approx(-0.75)
    assert carry.loc[4, "v2_flow_ema_fast"] == pytest.approx(0.0)
    assert reset.loc[3, "v2_segment_id"] != reset.loc[4, "v2_segment_id"]
    assert carry.loc[3, "v2_segment_id"] == carry.loc[4, "v2_segment_id"]


def test_v2_short_windows_and_six_component_minimum_are_supported() -> None:
    result = compute_order_flow_v2_features(
        _bars(rows=8),
        _config(
            v2_flow_window=2,
            v2_regime_window=2,
            v2_percentile_window=2,
            v2_min_observations=2,
            v2_min_component_count=6,
        ),
    )
    assert result["v2_strength_min_component_count"].eq(5).all()
    assert result["v2_score_min_component_count"].eq(6).all()


def test_v2_absorption_uses_contrarian_direction_for_entries_and_exits() -> None:
    config = _config(
        v2_min_confidence=0.0,
        v2_score_entry_threshold=0.2,
        v2_score_exit_threshold=-0.2,
        v2_exhaustion_threshold=0.1,
        participation_strong_threshold=100.0,
        participation_direction_threshold=30.0,
        v2_require_confirmation=False,
    )

    bullish = _bars(rows=16)
    bullish["buy_volume"] = 125.0
    bullish["sell_volume"] = 875.0
    bullish["delta"] = -750.0
    bullish["delta_ratio"] = -0.75
    bullish["open"] = 10.0
    bullish["close"] = 10.001
    bullish["high"] = 10.2
    bullish["low"] = 9.8
    bullish_result = compute_order_flow_v2_features(bullish, config)
    assert bullish_result["v2_absorption_score"].iloc[-1] > 0.1
    assert bool(bullish_result["of_v2_entry_candidate"].iloc[-1]) is True

    bearish = bullish.copy()
    bearish["buy_volume"] = 875.0
    bearish["sell_volume"] = 125.0
    bearish["delta"] = 750.0
    bearish["delta_ratio"] = 0.75
    bearish_result = compute_order_flow_v2_features(bearish, config)
    assert bearish_result["v2_absorption_score"].iloc[-1] < -0.1
    assert bool(bearish_result["of_v2_exit_candidate"].iloc[-1]) is True


def test_v2_strategy_class_selects_versioned_signal_columns() -> None:
    v1 = make_order_flow_strategy(OrderFlowConfig())
    v2 = make_order_flow_strategy(_config(strategy_version="v2"))
    assert v1()._signal_columns() == ("of_data_valid", "of_entry_signal", "of_exit_signal")
    assert v2()._signal_columns() == (
        "of_v2_data_valid",
        "of_v2_entry_signal",
        "of_v2_exit_signal",
    )


def test_v2_runner_persists_versioned_factor_and_report(tmp_path: Path) -> None:
    args = _parser().parse_args(
        [
            "--source",
            "fixture",
            "--strategy-version",
            "v2",
            "--v2-min-observations",
            "2",
            "--v2-percentile-window",
            "6",
            "--v2-flow-window",
            "4",
            "--v2-regime-window",
            "4",
            "--v2-min-confidence",
            "0",
            "--v2-score-entry-threshold",
            "0",
            "--v2-score-exit-threshold",
            "0",
            "--no-v2-require-confirmation",
            "--participation-strong-threshold",
            "0",
            "--participation-direction-threshold",
            "0",
            "--report",
            str(tmp_path / "report.json"),
            "--features",
            str(tmp_path / "features.csv"),
            "--transactions",
            str(tmp_path / "transactions.csv"),
            "--factor-output",
            str(tmp_path / "v2-factor.parquet"),
            "--factor-manifest",
            str(tmp_path / "v2-factor.manifest.json"),
            "--participation-factor-output",
            str(tmp_path / "participation.parquet"),
            "--participation-factor-manifest",
            str(tmp_path / "participation.manifest.json"),
        ]
    )
    report = run(args)
    assert report["strategy_version"] == "v2"
    assert report["order_flow_version"] == ORDER_FLOW_V2_VERSION
    assert report["strategy"].endswith("_v2_long_only")
    assert report["factor"]["name"] == ORDER_FLOW_V2_FACTOR_NAME
    assert report["factor"]["version"] == "order-flow-v2-score-1"
    assert report["signal_summary_v2"]["v2_valid_bars"] > 0
    assert Path(report["factor"]["output"]).exists()
    assert Path(report["factor"]["manifest"]).exists()
    assert report["events"]


def test_v2_participation_factor_parses_csv_boolean_values() -> None:
    frame = pd.DataFrame(
        {
            "participation_activity": [0.5, 0.5],
            "participation_size": [0.5, 0.5],
            "participation_imbalance": [0.5, 0.5],
            "participation_control": [0.5, 0.5],
            "participation_eligible": ["False", "true"],
        }
    )
    values = OrderFlowParticipationScore().compute(frame)
    assert pd.isna(values.iloc[0])
    assert values.iloc[1] == pytest.approx(50.0)


def test_v2_factor_registry_definition_and_daily_export(tmp_path: Path) -> None:
    source = compute_order_flow_v2_features(_bars(), _config())
    assert ensure_order_flow_v2_factor_registered() is OrderFlowV2Score
    computed = compute_order_flow_v2_factor(source)
    pd.testing.assert_series_equal(
        computed,
        source["order_flow_v2_score"],
        check_names=False,
    )

    daily = build_easy_tdx_v2_factor_frame(source, frequency="daily")
    assert list(daily.columns) == ["date", "code", "symbol", "datetime", ORDER_FLOW_V2_FACTOR_NAME]
    assert daily["code"].tolist() == ["688183"]
    definition = order_flow_v2_factor_definition()
    assert definition["feature_version"] == "order-flow-proxy-2-features"
    assert definition["strategy_version"] == "v2"
    assert definition["minimum_component_parameter"] == "v2_min_component_count (2-6)"
    payload = json.loads(
        (Path(__file__).parents[1] / "research/order_flow/order_flow_factor_v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["factor"]["name"] == definition["name"]
    assert payload["factor"]["inputs"] == definition["inputs"]
    assert payload["factor"]["strategy_version"] == definition["strategy_version"]
    assert (
        payload["factor"]["minimum_component_parameter"]
        == definition["minimum_component_parameter"]
    )


def test_build_feature_frame_selects_v1_or_v2_without_dropping_columns() -> None:
    bars, transactions, _ = _fixture_data("SH:688183", days=3)
    v1 = build_feature_frame(
        bars,
        transactions,
        symbol="SH:688183",
        config=OrderFlowConfig(volume_baseline_sessions=2, min_history_sessions=2),
    )
    v2 = build_feature_frame(
        bars,
        transactions,
        symbol="SH:688183",
        config=_config(strategy_version="v2"),
    )
    assert v1["strategy_version"].eq("v1").all()
    assert v2["strategy_version"].eq("v2").all()
    assert v1["order_flow_version"].eq(ORDER_FLOW_V1_VERSION).all()
    assert v2["order_flow_version"].eq(ORDER_FLOW_V2_VERSION).all()
    assert v1["of_entry_signal"].equals(v2["of_entry_signal"])
    assert "of_v2_entry_signal" in v2


def test_v2_factor_rejects_missing_components() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        OrderFlowV2Score().compute(pd.DataFrame({"v2_flow_pressure": [0.1]}))


def test_v2_accepts_legacy_v1_frame_without_new_aggregate_columns() -> None:
    bars, transactions, _ = _fixture_data("SH:688183", days=3)
    aggregate = aggregate_transactions_to_bars(
        bars,
        transactions,
        symbol="SH:688183",
        bar_minutes=5,
        large_trade_lots=100,
        transaction_alignment="floor",
    )
    legacy = compute_order_flow_features(
        aggregate,
        OrderFlowConfig(volume_baseline_sessions=2, min_history_sessions=2),
    )
    optional = {
        "buy_trade_count",
        "sell_trade_count",
        "neutral_trade_count",
        "trade_volume_squared",
        "max_trade_volume",
        "large_buy_volume",
        "large_sell_volume",
        "large_neutral_volume",
        "large_delta",
        "large_delta_ratio",
        "average_trade_size",
        "average_trade_amount",
        "trade_size_hhi",
        "max_trade_share",
        "buy_trade_share",
        "sell_trade_share",
    }
    legacy = legacy.drop(columns=optional.intersection(legacy.columns))
    result = compute_order_flow_v2_features(
        legacy,
        _config(
            v2_min_observations=2, v2_percentile_window=6, v2_flow_window=4, v2_regime_window=4
        ),
    )
    assert len(result) == len(legacy)
    assert result["order_flow_v2_score"].notna().any()
