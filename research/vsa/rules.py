"""Causal VSA candidate and confirmation rules.

Patterns are treated as observations, not predictions.  A candidate is marked on the bar where it
is observed; only the following bar can move it to ``confirmed``, ``invalidated`` or ``expired``.
The executable signal is written on that following confirmation bar, so a strategy using
``NextOpen`` cannot accidentally trade on future information.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from .features import VSAConfig

VSA_RULE_VERSION = "vsa-rules-daily-1"

CANDIDATE_NONE = 0
CANDIDATE_NO_DEMAND = -1
CANDIDATE_UPTHRUST = -2
CANDIDATE_NO_SUPPLY = 1
CANDIDATE_STOPPING_VOLUME = 2
CANDIDATE_TEST = 3

CANDIDATE_LABELS: dict[int, str] = {
    CANDIDATE_NONE: "none",
    CANDIDATE_NO_DEMAND: "no_demand",
    CANDIDATE_UPTHRUST: "upthrust",
    CANDIDATE_NO_SUPPLY: "no_supply",
    CANDIDATE_STOPPING_VOLUME: "stopping_volume",
    CANDIDATE_TEST: "test",
}

BULLISH_CANDIDATES = frozenset({CANDIDATE_NO_SUPPLY, CANDIDATE_STOPPING_VOLUME, CANDIDATE_TEST})
BEARISH_CANDIDATES = frozenset({CANDIDATE_NO_DEMAND, CANDIDATE_UPTHRUST})


def candidate_label(code: float) -> str:
    """Return the stable text label for a candidate code."""

    try:
        normalized = int(code)
    except (TypeError, ValueError):
        normalized = CANDIDATE_NONE
    return CANDIDATE_LABELS.get(normalized, "unknown")


def _mask(frame: pd.DataFrame, expression: pd.Series) -> pd.Series:
    """Normalize nullable boolean expressions for predictable vectorized rules."""

    return expression.reindex(frame.index).fillna(False).astype(bool)


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _set_if_column(frame: pd.DataFrame, index: Any, column: str, value: Any) -> None:
    """Assign a value while keeping the rule loop independent of pandas dtypes."""

    frame.at[index, column] = value


def apply_vsa_rules(
    features: pd.DataFrame,
    config: VSAConfig | None = None,
) -> pd.DataFrame:
    """Add VSA candidates, one-bar confirmations, and executable signal metadata.

    The function expects the output of :func:`compute_vsa_features`.  It also accepts a compatible
    frame with the required feature columns, which is useful for focused unit tests.  Rows that
    are not valid or are not warmed up never become candidates or signals.
    """

    if not isinstance(features, pd.DataFrame):
        raise TypeError("features must be a pandas DataFrame")
    settings = config or VSAConfig()
    out = features.copy()
    if out.empty:
        out = _initialize_rule_columns(out)
        out["vsa_rule_version"] = VSA_RULE_VERSION
        return out

    required_features = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "vsa_data_valid",
        "vsa_history_ready",
        "vsa_volume_ratio",
        "vsa_spread_ratio",
        "vsa_close_location",
        "vsa_clv",
        "vsa_range_position",
        "vsa_trend_direction",
        "vsa_prior_low",
        "vsa_prior_high",
    }
    missing = sorted(required_features.difference(out.columns))
    if missing:
        raise ValueError("VSA feature frame is missing columns: " + ", ".join(missing))

    out = _initialize_rule_columns(out)
    valid = _mask(
        out,
        out["vsa_data_valid"].fillna(False) & out["vsa_history_ready"].fillna(False),
    )
    up_bar = _mask(out, out["close"] > out["open"])
    down_bar = _mask(out, out["close"] < out["open"])
    low_volume = _mask(out, out["vsa_volume_ratio"] <= settings.low_volume_ratio)
    high_volume = _mask(out, out["vsa_volume_ratio"] >= settings.high_volume_ratio)
    narrow_spread = _mask(out, out["vsa_spread_ratio"] <= settings.narrow_spread_ratio)
    wide_spread = _mask(out, out["vsa_spread_ratio"] >= settings.wide_spread_ratio)
    closes_high = _mask(out, out["vsa_close_location"] >= settings.close_high_threshold)
    closes_low = _mask(out, out["vsa_close_location"] <= settings.close_low_threshold)
    near_high = _mask(
        out,
        (out["vsa_range_position"] >= settings.near_high_threshold)
        | (out["high"] >= out["vsa_prior_high"]),
    )
    near_low = _mask(
        out,
        (out["vsa_range_position"] <= settings.near_low_threshold)
        | (out["low"] <= out["vsa_prior_low"]),
    )
    uptrend_or_neutral = _mask(out, out["vsa_trend_direction"] >= 0.0)
    downtrend_or_neutral = _mask(out, out["vsa_trend_direction"] <= 0.0)

    # Priority is explicit where two descriptions overlap.  A probe or climactic event carries
    # more information than a generic low-volume bar, so it wins over no-demand/no-supply.
    masks: tuple[tuple[int, pd.Series, str], ...] = (
        (
            CANDIDATE_TEST,
            valid & down_bar & narrow_spread & low_volume & closes_high & near_low,
            "down_probe+low_volume+narrow_spread+close_high+near_low",
        ),
        (
            CANDIDATE_STOPPING_VOLUME,
            valid & down_bar & wide_spread & high_volume & ~closes_low & near_low,
            "down_wide+high_volume+close_off_low+near_low",
        ),
        (
            CANDIDATE_UPTHRUST,
            valid & up_bar & wide_spread & high_volume & closes_low & near_high,
            "up_wide+high_volume+close_low+near_high",
        ),
        (
            CANDIDATE_NO_DEMAND,
            valid & up_bar & narrow_spread & low_volume & ~closes_low & uptrend_or_neutral,
            "up_narrow+low_volume+close_not_low+uptrend_context",
        ),
        (
            CANDIDATE_NO_SUPPLY,
            valid & down_bar & narrow_spread & low_volume & ~closes_low & downtrend_or_neutral,
            "down_narrow+low_volume+close_off_low+downtrend_context",
        ),
    )

    for code, candidate_mask, reason in masks:
        selected = candidate_mask & out["vsa_candidate_code"].eq(CANDIDATE_NONE)
        out.loc[selected, "vsa_candidate_code"] = code
        out.loc[selected, "vsa_candidate"] = CANDIDATE_LABELS[code]
        out.loc[selected, "vsa_candidate_reason"] = reason

    if not out.index.is_unique:
        raise ValueError("VSA feature frame index must be unique")

    symbols = _symbol_series(out)
    out["_vsa_symbol"] = symbols
    duplicate_keys = pd.DataFrame(
        {"_vsa_symbol": symbols, "timestamp": out["timestamp"]}, index=out.index
    ).duplicated(keep=False)
    if duplicate_keys.any():
        raise ValueError("VSA feature frame contains duplicate symbol/timestamp rows")
    for _, group in out.groupby("_vsa_symbol", sort=False, dropna=False):
        # Callers may pass a compatible feature frame in display order rather than chronological
        # order.  Confirmation is a temporal relation, so sort only the iteration view and keep
        # the caller's row order in the returned frame.
        indices = list(group.sort_values("timestamp", kind="mergesort").index)
        for position, index in enumerate(indices):
            if position > 0:
                previous_index = indices[position - 1]
                previous_code = int(out.at[previous_index, "vsa_candidate_code"] or 0)
                if previous_code != CANDIDATE_NONE:
                    _resolve_confirmation(out, previous_index, index, previous_code, settings)
            current_code = int(out.at[index, "vsa_candidate_code"] or 0)
            if current_code != CANDIDATE_NONE:
                _set_if_column(out, index, "vsa_confirmation_status", "pending")

    out = out.drop(columns=["_vsa_symbol"])
    out["vsa_rule_version"] = VSA_RULE_VERSION
    return out


def _initialize_rule_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Create stable rule columns without changing source feature columns."""

    out = frame.copy()
    out["vsa_candidate_code"] = np.zeros(len(out), dtype="int64")
    out["vsa_candidate"] = "none"
    out["vsa_candidate_reason"] = ""
    out["vsa_confirmation_status"] = "none"
    out["vsa_confirmed_signal"] = np.zeros(len(out), dtype="int64")
    out["vsa_confirmed_code"] = np.zeros(len(out), dtype="int64")
    out["vsa_signal_name"] = "none"
    out["vsa_reference_timestamp"] = pd.NaT
    out["vsa_stop_price"] = np.nan
    out["vsa_target_price"] = np.nan
    out["vsa_risk_per_share"] = np.nan
    out["vsa_signal"] = np.zeros(len(out), dtype="int64")
    return out


def _symbol_series(frame: pd.DataFrame) -> pd.Series:
    aliases = {"symbol", "股票代码", "代码", "code", "ticker"}
    for column in frame.columns:
        if str(column).casefold() in aliases:
            return frame[column].astype("string").fillna("__single__").astype(str)
    return pd.Series("__single__", index=frame.index, dtype="string")


def _resolve_confirmation(
    frame: pd.DataFrame,
    candidate_index: Any,
    confirmation_index: Any,
    code: int,
    settings: VSAConfig,
) -> None:
    """Resolve exactly one bar after a candidate, using only values known then."""

    candidate_label_text = CANDIDATE_LABELS.get(code, "unknown")
    candidate_valid = bool(pd.notna(frame.at[candidate_index, "vsa_data_valid"])) and bool(
        frame.at[candidate_index, "vsa_data_valid"]
    )
    confirmation_valid = bool(pd.notna(frame.at[confirmation_index, "vsa_data_valid"])) and bool(
        frame.at[confirmation_index, "vsa_data_valid"]
    )
    if not candidate_valid or not confirmation_valid:
        _set_if_column(frame, candidate_index, "vsa_confirmation_status", "invalidated_data")
        return

    candidate_close = _finite(frame.at[candidate_index, "close"])
    confirmation_close = _finite(frame.at[confirmation_index, "close"])
    confirmation_open = _finite(frame.at[confirmation_index, "open"])
    candidate_low = _finite(frame.at[candidate_index, "low"])
    candidate_high = _finite(frame.at[candidate_index, "high"])
    confirmation_clv = _finite(frame.at[confirmation_index, "vsa_clv"])
    if None in {
        candidate_close,
        confirmation_close,
        confirmation_open,
        candidate_low,
        candidate_high,
        confirmation_clv,
    }:
        _set_if_column(frame, candidate_index, "vsa_confirmation_status", "invalidated_data")
        return

    assert candidate_close is not None
    assert confirmation_close is not None
    assert confirmation_open is not None
    assert candidate_low is not None
    assert candidate_high is not None
    assert confirmation_clv is not None

    bullish = code in BULLISH_CANDIDATES
    bearish = code in BEARISH_CANDIDATES
    if bullish:
        confirmed = (
            confirmation_close > candidate_close * (1.0 + settings.confirmation_move_pct)
            and confirmation_close > confirmation_open
            and confirmation_clv >= 0.0
        )
        invalidated = confirmation_close < candidate_low
    elif bearish:
        confirmed = (
            confirmation_close < candidate_close * (1.0 - settings.confirmation_move_pct)
            and confirmation_close < confirmation_open
            and confirmation_clv <= 0.0
        )
        invalidated = confirmation_close > candidate_high
    else:
        confirmed = False
        invalidated = True

    if confirmed:
        stop_price = candidate_low * (1.0 - settings.stop_buffer_pct) if bullish else np.nan
        risk = confirmation_close - stop_price if bullish else np.nan
        target = (
            confirmation_close + settings.risk_reward * risk
            if bullish and np.isfinite(risk) and risk > 0.0
            else np.nan
        )
        _set_if_column(frame, candidate_index, "vsa_confirmation_status", "confirmed")
        _set_if_column(frame, confirmation_index, "vsa_confirmed_signal", 1 if bullish else -1)
        _set_if_column(frame, confirmation_index, "vsa_confirmed_code", code)
        _set_if_column(
            frame,
            confirmation_index,
            "vsa_signal_name",
            f"{candidate_label_text}_confirmed",
        )
        _set_if_column(
            frame,
            confirmation_index,
            "vsa_reference_timestamp",
            frame.at[candidate_index, "timestamp"],
        )
        if bullish:
            _set_if_column(frame, confirmation_index, "vsa_stop_price", stop_price)
            _set_if_column(frame, confirmation_index, "vsa_target_price", target)
            _set_if_column(frame, confirmation_index, "vsa_risk_per_share", risk)
        _set_if_column(frame, confirmation_index, "vsa_signal", 1 if bullish else -1)
    elif invalidated:
        _set_if_column(frame, candidate_index, "vsa_confirmation_status", "invalidated")
    else:
        _set_if_column(frame, candidate_index, "vsa_confirmation_status", "expired")


def summarize_vsa_events(frame: pd.DataFrame) -> dict[str, Any]:
    """Return compact counts for reports without converting labels into trade advice."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    candidate_counts = {
        label: int(frame.get("vsa_candidate", pd.Series(dtype=str)).eq(label).sum())
        for label in CANDIDATE_LABELS.values()
    }
    status_counts = {
        status: int(frame.get("vsa_confirmation_status", pd.Series(dtype=str)).eq(status).sum())
        for status in ("none", "pending", "confirmed", "invalidated", "invalidated_data", "expired")
    }
    signals = frame.get("vsa_confirmed_signal", pd.Series(dtype=float))
    return {
        "candidate_counts": candidate_counts,
        "confirmation_status_counts": status_counts,
        "confirmed_long_count": int(signals.eq(1).sum()),
        "confirmed_exit_count": int(signals.eq(-1).sum()),
        "observations": len(frame),
    }


def iter_confirmed_rows(frame: pd.DataFrame) -> Iterable[pd.Series]:
    """Yield confirmed rows in frame order for report/test consumers."""

    if "vsa_confirmed_signal" not in frame.columns:
        return
    mask = pd.to_numeric(frame["vsa_confirmed_signal"], errors="coerce").fillna(0).ne(0)
    for _, row in frame.loc[mask].iterrows():
        yield row


__all__ = [
    "BEARISH_CANDIDATES",
    "BULLISH_CANDIDATES",
    "CANDIDATE_LABELS",
    "CANDIDATE_NONE",
    "CANDIDATE_NO_DEMAND",
    "CANDIDATE_NO_SUPPLY",
    "CANDIDATE_STOPPING_VOLUME",
    "CANDIDATE_TEST",
    "CANDIDATE_UPTHRUST",
    "VSA_RULE_VERSION",
    "apply_vsa_rules",
    "candidate_label",
    "iter_confirmed_rows",
    "summarize_vsa_events",
]
