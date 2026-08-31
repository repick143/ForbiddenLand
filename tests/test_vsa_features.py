from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.vsa.features import VSAConfig, compute_vsa_features


def _frame(symbols: tuple[str, ...] = ("600183",), rows: int = 8) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for symbol_index, symbol in enumerate(symbols):
        for index in range(rows):
            close = 10.0 + symbol_index * 20.0 + index * 0.2
            records.append(
                {
                    "timestamp": pd.Timestamp("2024-01-02") + pd.offsets.BDay(index),
                    "open": close - 0.05,
                    "high": close + 0.25,
                    "low": close - 0.25,
                    "close": close,
                    "volume": 100.0 + index,
                    "symbol": symbol,
                }
            )
    return pd.DataFrame(records).sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def _config() -> VSAConfig:
    return VSAConfig(
        volume_window=3,
        spread_window=3,
        trend_window=3,
        context_window=3,
        min_periods=3,
    )


def test_clv_is_missing_for_zero_range_without_marking_valid_ohlc_invalid() -> None:
    frame = _frame(rows=6)
    frame.loc[4, ["open", "high", "low", "close"]] = 12.0

    features = compute_vsa_features(frame, _config())

    row = features.iloc[4]
    assert bool(row["vsa_data_valid"])
    assert float(row["vsa_spread"]) == 0.0
    assert pd.isna(row["vsa_clv"])
    assert pd.isna(row["vsa_close_location"])


def test_rolling_baselines_exclude_current_and_future_bars() -> None:
    frame = _frame(rows=8)
    config = _config()
    original = compute_vsa_features(frame, config)

    changed = frame.copy()
    changed.loc[7, "volume"] = 10_000.0
    changed.loc[7, "high"] = 100.0
    changed.loc[7, "low"] = 1.0
    changed_features = compute_vsa_features(changed, config)

    # A future observation may change its own ratio, but never the previous rows' baselines.
    comparable = original.index < 7
    for column in ("vsa_volume_baseline", "vsa_spread_baseline", "vsa_prior_high", "vsa_prior_low"):
        np.testing.assert_allclose(
            original.loc[comparable, column].to_numpy(dtype=float),
            changed_features.loc[comparable, column].to_numpy(dtype=float),
            equal_nan=True,
        )


def test_rolling_state_is_isolated_per_symbol() -> None:
    combined = compute_vsa_features(_frame(("600183", "688072"), rows=7), _config())
    one = compute_vsa_features(_frame(("600183",), rows=7), _config())

    combined_one = combined.loc[combined["symbol"].astype(str).eq("600183")].sort_values(
        "timestamp"
    )
    one = one.sort_values("timestamp")
    np.testing.assert_allclose(
        combined_one["vsa_volume_baseline"].to_numpy(dtype=float),
        one["vsa_volume_baseline"].to_numpy(dtype=float),
        equal_nan=True,
    )
    np.testing.assert_allclose(
        combined_one["vsa_prior_high"].to_numpy(dtype=float),
        one["vsa_prior_high"].to_numpy(dtype=float),
        equal_nan=True,
    )


def test_missing_source_values_are_retained_and_flagged() -> None:
    frame = _frame(rows=6)
    frame.loc[3, "volume"] = np.nan

    features = compute_vsa_features(frame, _config())

    row = features.iloc[3]
    assert not bool(row["vsa_data_valid"])
    assert row["vsa_invalid_reason"] == "missing_required"
    assert pd.isna(row["volume"])


def test_input_schema_and_parameter_validation() -> None:
    with pytest.raises(ValueError, match="required column"):
        compute_vsa_features(pd.DataFrame({"close": [1.0]}), _config())
    with pytest.raises(ValueError, match="low_volume_ratio"):
        VSAConfig(low_volume_ratio=1.5)
    with pytest.raises(ValueError, match="smallest rolling window"):
        VSAConfig(volume_window=3, spread_window=4, trend_window=5, context_window=6, min_periods=4)
