from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.order_flow.aggregate import aggregate_transactions_to_bars
from research.order_flow.config import OrderFlowConfig
from research.order_flow.easy_tdx_factor import (
    OrderFlowParticipationScore,
    build_easy_tdx_participation_factor_frame,
    ensure_participation_factor_registered,
    participation_factor_definition,
    save_easy_tdx_factor_bundle,
)
from research.order_flow.normalize import normalize_bar_frame, normalize_transaction_frame
from research.order_flow.participation import (
    PARTICIPATION_DAILY_QUANTILE,
    PARTICIPATION_FACTOR_NAME,
    compute_participation_features,
    participation_score_from_components,
    summarize_participation_sessions,
)


def _participation_input(days: int = 4) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_index, day in enumerate(pd.bdate_range("2026-01-05", periods=days)):
        high_evidence = day_index >= 2
        for slot, minute in enumerate((35, 40)):
            positive = not (high_evidence and day_index == 3 and slot == 0)
            delta = 0.8 if positive else -0.8
            clv = 0.8 if positive else 0.7
            rows.append(
                {
                    "timestamp": day.replace(hour=9, minute=minute),
                    "symbol": "SH:688256",
                    "slot_key": 9 * 60 + minute,
                    "session_date_key": int(day.strftime("%Y%m%d")),
                    "total_transaction_volume": 1_000.0 if high_evidence else 100.0,
                    "transaction_amount": 1_000_000.0 if high_evidence else 10_000.0,
                    "trade_count": 20.0 if high_evidence else 10.0,
                    "large_trade_volume": 800.0 if high_evidence else 10.0,
                    "large_trade_share": 0.8 if high_evidence else 0.1,
                    "delta_ratio": delta if high_evidence else 0.1,
                    "large_delta_ratio": delta if high_evidence else 0.1,
                    "bar_return": 0.01 if positive else 0.0001,
                    "clv": clv,
                    "vwap_distance": 0.005 if positive else 0.001,
                    "transaction_coverage": 1.0,
                    "baseline_observations": day_index,
                    "of_data_valid": True,
                    "of_history_ready": high_evidence,
                    "bullish_absorption": bool(not positive),
                    "bearish_absorption": False,
                    "is_incomplete_session": False,
                }
            )
    return pd.DataFrame(rows)


def test_aggregation_keeps_directional_large_prints_and_average_trade_size() -> None:
    day = pd.Timestamp("2026-01-05")
    bars = normalize_bar_frame(
        pd.DataFrame(
            {
                "timestamp": [day.replace(hour=9, minute=30)],
                "open": [10.0],
                "high": [10.2],
                "low": [9.9],
                "close": [10.1],
                "volume": [1_000.0],
            }
        ),
        symbol="SH:688256",
        bar_minutes=5,
    )
    transactions = normalize_transaction_frame(
        pd.DataFrame(
            {
                "time": ["09:30:10", "09:30:20", "09:30:30"],
                "price": [10.0, 10.0, 10.0],
                "vol": [6, 3, 1],
                "trade_count": [2, 1, 1],
                "bs_flag": [0, 1, 2],
            }
        ),
        trade_date=day,
        symbol="SH:688256",
    )
    result = aggregate_transactions_to_bars(
        bars,
        transactions,
        symbol="SH:688256",
        large_trade_lots=3,
        transaction_alignment="floor",
    ).iloc[0]
    assert result["large_buy_volume"] == 600.0
    assert result["large_sell_volume"] == 300.0
    assert result["large_neutral_volume"] == 0.0
    assert result["large_delta_ratio"] == pytest.approx(1.0 / 3.0)
    assert result["average_trade_size"] == 250.0
    assert result["average_trade_amount"] == 2_500.0


def test_participation_score_is_causal_bounded_and_confirms_same_session() -> None:
    config = OrderFlowConfig(
        volume_baseline_sessions=2,
        min_history_sessions=2,
        participation_confirmation_bars=2,
    )
    source = _participation_input()
    result = compute_participation_features(source, config)
    available = result[PARTICIPATION_FACTOR_NAME].dropna()
    assert not available.empty
    assert available.between(0.0, 100.0).all()
    assert (
        result.loc[result["session_date_key"].le(20260106), PARTICIPATION_FACTOR_NAME].isna().all()
    )

    first_high_day = result.loc[result["session_date_key"].eq(20260107)]
    assert first_high_day["participation_state"].eq("active_buy").all()
    assert bool(first_high_day.iloc[0]["participation_confirmed"]) is False
    assert bool(first_high_day.iloc[1]["participation_confirmed"]) is True

    changed = source.copy()
    changed.loc[changed.index[-1], "total_transaction_volume"] = 100_000.0
    changed.loc[changed.index[-1], "transaction_amount"] = 100_000_000.0
    recalculated = compute_participation_features(changed, config)
    pd.testing.assert_series_equal(
        result.iloc[:-1][PARTICIPATION_FACTOR_NAME].reset_index(drop=True),
        recalculated.iloc[:-1][PARTICIPATION_FACTOR_NAME].reset_index(drop=True),
    )


def test_invalid_historical_bar_does_not_enter_participation_baseline() -> None:
    config = OrderFlowConfig(volume_baseline_sessions=2, min_history_sessions=2)
    ordinary = _participation_input(days=5)
    ordinary.loc[ordinary.index[4], "of_data_valid"] = False
    outlier = ordinary.copy()
    outlier.loc[outlier.index[4], "total_transaction_volume"] = 100_000_000.0
    outlier.loc[outlier.index[4], "transaction_amount"] = 10_000_000_000.0

    expected = compute_participation_features(ordinary, config)
    actual = compute_participation_features(outlier, config)
    assert np.isnan(actual.loc[4, PARTICIPATION_FACTOR_NAME])
    pd.testing.assert_series_equal(
        expected.loc[
            expected["session_date_key"].eq(20260109), PARTICIPATION_FACTOR_NAME
        ].reset_index(drop=True),
        actual.loc[actual["session_date_key"].eq(20260109), PARTICIPATION_FACTOR_NAME].reset_index(
            drop=True
        ),
    )


def test_passive_absorption_flips_inferred_participant_direction() -> None:
    result = compute_participation_features(
        _participation_input(),
        OrderFlowConfig(volume_baseline_sessions=2, min_history_sessions=2),
    )
    absorbed = result.loc[
        result["session_date_key"].eq(20260108) & result["slot_key"].eq(9 * 60 + 35)
    ].iloc[0]
    assert absorbed["participation_state"] == "passive_buy_absorption"
    assert absorbed["participation_aggressor_direction_score"] < 0.0
    assert absorbed["participation_direction_score"] > 0.0


def test_participation_factor_registry_and_daily_p90_export(tmp_path: Path) -> None:
    from easy_tdx.factor import FactorEngine

    features = compute_participation_features(
        _participation_input(),
        OrderFlowConfig(volume_baseline_sessions=2, min_history_sessions=2),
    )
    assert ensure_participation_factor_registered() is OrderFlowParticipationScore
    computed = FactorEngine().compute_single(features, [PARTICIPATION_FACTOR_NAME])
    pd.testing.assert_series_equal(
        computed[PARTICIPATION_FACTOR_NAME],
        features[PARTICIPATION_FACTOR_NAME],
        check_names=True,
    )

    daily = build_easy_tdx_participation_factor_frame(features, frequency="daily")
    target_date = 20260107
    source_values = features.loc[
        features["session_date_key"].eq(target_date), PARTICIPATION_FACTOR_NAME
    ].dropna()
    actual = daily.loc[daily["date"].eq(target_date), PARTICIPATION_FACTOR_NAME].iloc[0]
    assert actual == pytest.approx(source_values.quantile(PARTICIPATION_DAILY_QUANTILE))

    bar = build_easy_tdx_participation_factor_frame(features, frequency="bar")
    assert len(bar) == len(features)
    assert bar[PARTICIPATION_FACTOR_NAME].equals(features[PARTICIPATION_FACTOR_NAME])

    bundle = save_easy_tdx_factor_bundle(
        daily,
        tmp_path / "participation.parquet",
        provenance={"source": "fixture", "period": "5MIN", "adjustment": "NONE"},
        factor_name=PARTICIPATION_FACTOR_NAME,
        factor_metadata=participation_factor_definition(),
    )
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert manifest["factor"]["name"] == PARTICIPATION_FACTOR_NAME
    assert manifest["factor"]["daily_aggregation"] == "P90 of valid bar scores"


def test_participation_definition_file_matches_runtime_contract() -> None:
    path = Path(__file__).parents[1] / "research/order_flow/order_flow_participation_factor.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    runtime = participation_factor_definition()
    for key in ("name", "version", "inputs", "formula", "daily_aggregation"):
        assert payload["factor"][key] == runtime[key]


def test_daily_summary_preserves_state_quality_and_provisional_marker() -> None:
    features = compute_participation_features(
        _participation_input(),
        OrderFlowConfig(volume_baseline_sessions=2, min_history_sessions=2),
    )
    features.loc[features.index[-1], "participation_provisional"] = True
    result = summarize_participation_sessions(features)
    latest = result.iloc[-1]
    assert latest["code"] == "688256"
    assert bool(latest["participation_provisional"]) is True
    assert latest["valid_bars"] == 2
    assert latest["participation_latest_state"] == features.iloc[-1]["participation_state"]
    assert latest["participation_latest_direction_score"] == pytest.approx(
        features.iloc[-1]["participation_direction_score"]
    )
    assert bool(latest["participation_latest_confirmed"]) is bool(
        features.iloc[-1]["participation_confirmed"]
    )
    assert latest["participation_latest_confidence"] == pytest.approx(
        features.iloc[-1]["participation_confidence"]
    )
    assert 0.0 <= latest["participation_strong_bar_share"] <= 1.0
    assert np.isfinite(latest["participation_confidence"])

    confirmed_buy_day = result.loc[result["date"].eq(20260107)].iloc[0]
    assert confirmed_buy_day["participation_confirmed_direction"] == "buy"
    assert confirmed_buy_day["participation_confirmed_buy_bar_share"] == pytest.approx(0.5)
    assert confirmed_buy_day["participation_confirmed_sell_bar_share"] == 0.0


def test_participation_components_reject_out_of_range_values() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        participation_score_from_components(
            pd.DataFrame(
                {
                    "participation_activity": [1.1],
                    "participation_size": [0.5],
                    "participation_imbalance": [0.5],
                    "participation_control": [0.5],
                }
            )
        )
