"""Causal order-flow proxy features and signal candidates."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import ORDER_FLOW_VERSION, OrderFlowConfig

FEATURE_VERSION = f"{ORDER_FLOW_VERSION}-features"


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.where(denominator > 0.0)
    result = numerator / denominator
    return result.replace([np.inf, -np.inf], np.nan)


def _group_key(frame: pd.DataFrame) -> list[str]:
    return ["symbol", "slot_key"] if "symbol" in frame.columns else ["slot_key"]


def _prior_stat(
    frame: pd.DataFrame,
    column: str,
    window: int,
    method: str,
) -> pd.Series:
    """Calculate a same-time-of-day statistic using prior observations only."""

    keys = _group_key(frame)
    grouped = frame.groupby(keys, sort=False, dropna=False)[column]

    def calculate(values: pd.Series) -> pd.Series:
        rolling = values.shift(1).rolling(window=window, min_periods=window)
        return getattr(rolling, method)()

    return grouped.transform(calculate)


def _prior_std(frame: pd.DataFrame, column: str, window: int) -> pd.Series:
    keys = _group_key(frame)
    grouped = frame.groupby(keys, sort=False, dropna=False)[column]

    def calculate(values: pd.Series) -> pd.Series:
        return values.shift(1).rolling(window=window, min_periods=window).std(ddof=0)

    return grouped.transform(calculate)


def _prior_count(frame: pd.DataFrame) -> pd.Series:
    keys = _group_key(frame)
    return frame.groupby(keys, sort=False, dropna=False).cumcount()


def _persistent(
    mask: pd.Series,
    frame: pd.DataFrame,
    length: int,
    *,
    same_session: bool = False,
    bar_minutes: int = 5,
) -> pd.Series:
    """Require ``length`` true observations ending at the current bar.

    When ``same_session`` is enabled, a lunch break, a missing bar, or a calendar-day boundary
    resets the run.  This prevents the last morning bar and the first afternoon bar from counting
    as adjacent confirmations.
    """

    keys = ["symbol"] if "symbol" in frame.columns else []
    groups = (
        frame.groupby(keys, sort=False, dropna=False).groups.values() if keys else [frame.index]
    )
    result = pd.Series(False, index=frame.index)
    for indices in groups:
        labels = list(indices)
        if not same_session or len(labels) < 2:
            values = mask.loc[labels].astype(float).rolling(length, min_periods=length).sum()
            result.loc[labels] = values.eq(length).to_numpy()
            continue

        timestamps = pd.to_datetime(frame.loc[labels, "timestamp"], errors="coerce")
        gaps = timestamps.diff().dt.total_seconds().fillna(0.0).gt(float(bar_minutes * 60))
        gaps |= timestamps.dt.date.ne(timestamps.dt.date.shift(1)).fillna(False)
        segment_ids = gaps.cumsum()
        for segment_labels in (
            timestamps.to_frame(name="timestamp")
            .assign(_segment=segment_ids)
            .groupby("_segment", sort=False)
            .groups.values()
        ):
            segment = list(segment_labels)
            values = mask.loc[segment].astype(float).rolling(length, min_periods=length).sum()
            result.loc[segment] = values.eq(length).to_numpy()
    return result


def _session_last_by_clock(frame: pd.DataFrame, bar_minutes: int) -> pd.Series:
    if "is_session_last" in frame.columns:
        return frame["is_session_last"].fillna(False).astype(bool)
    minutes = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    session_bar = (
        frame["is_session_bar"].fillna(False).astype(bool)
        if "is_session_bar" in frame.columns
        else pd.Series(True, index=frame.index)
    )
    return session_bar & (minutes + bar_minutes >= 900)


def compute_order_flow_features(
    frame: pd.DataFrame,
    config: OrderFlowConfig | None = None,
) -> pd.DataFrame:
    """Compute missing-aware, causal transaction-flow features.

    ``frame`` should be the output of :func:`aggregate_transactions_to_bars`.  All baseline
    calculations use a one-row shift before rolling, so changing a later bar cannot change an
    earlier feature.  No field is interpreted as an institutional identity or a true order-book
    imbalance.
    """

    settings = config or OrderFlowConfig()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    required = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "buy_volume",
        "sell_volume",
        "neutral_volume",
        "total_transaction_volume",
        "delta",
        "delta_ratio",
        "transaction_observed",
        "slot_key",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("order-flow frame is missing columns: " + ", ".join(missing))

    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("order-flow frame contains invalid timestamps")
    if "symbol" not in data.columns:
        data["symbol"] = "__single__"
    data["symbol"] = data["symbol"].astype(str)
    data = data.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
    if data.duplicated(["symbol", "timestamp"]).any():
        raise ValueError("order-flow frame contains duplicate symbol/timestamp rows")

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "buy_volume",
        "sell_volume",
        "neutral_volume",
        "total_transaction_volume",
        "delta",
        "delta_ratio",
    ]
    for column in numeric_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    finite_ohlcv = np.isfinite(data[["open", "high", "low", "close", "volume"]].to_numpy()).all(
        axis=1
    )
    valid_order = (
        data["high"].ge(data[["open", "close"]].max(axis=1))
        & data["low"].le(data[["open", "close"]].min(axis=1))
        & data["high"].ge(data["low"])
        & data["volume"].ge(0.0)
    )
    observed = data["transaction_observed"].fillna(False).astype(bool)
    data["of_data_valid"] = (
        finite_ohlcv
        & valid_order.to_numpy()
        & observed.to_numpy()
        & data["total_transaction_volume"].gt(0.0).to_numpy()
    )
    data["of_invalid_reason"] = np.select(
        [
            ~finite_ohlcv,
            ~valid_order.to_numpy(),
            ~observed.to_numpy(),
            ~data["total_transaction_volume"].gt(0.0).to_numpy(),
        ],
        [
            "invalid_ohlcv",
            "invalid_ohlc_order_or_volume",
            "transaction_missing",
            "transaction_zero",
        ],
        default="",
    )

    data["close_location"] = _safe_ratio(data["close"] - data["low"], data["high"] - data["low"])
    data["clv"] = _safe_ratio(
        2.0 * data["close"] - data["high"] - data["low"], data["high"] - data["low"]
    )
    data["bar_return"] = _safe_ratio(data["close"] - data["open"], data["open"])
    data["buy_share"] = _safe_ratio(data["buy_volume"], data["total_transaction_volume"])
    data["sell_share"] = _safe_ratio(data["sell_volume"], data["total_transaction_volume"])
    data["neutral_share"] = _safe_ratio(data["neutral_volume"], data["total_transaction_volume"])

    # Same clock-slot baselines avoid comparing the opening auction/first bar with the lunch
    # session.  The shift is intentional: the current bar never enters its own reference sample.
    data["volume_baseline"] = _prior_stat(
        data, "volume", settings.volume_baseline_sessions, "median"
    )
    data["transaction_volume_baseline"] = _prior_stat(
        data, "total_transaction_volume", settings.volume_baseline_sessions, "median"
    )
    data["delta_ratio_baseline"] = _prior_stat(
        data, "delta_ratio", settings.volume_baseline_sessions, "median"
    )
    data["delta_ratio_std"] = _prior_std(data, "delta_ratio", settings.volume_baseline_sessions)
    data["relative_volume"] = _safe_ratio(data["volume"], data["volume_baseline"])
    data["relative_transaction_volume"] = _safe_ratio(
        data["total_transaction_volume"], data["transaction_volume_baseline"]
    )
    data["delta_ratio_zscore"] = _safe_ratio(
        data["delta_ratio"] - data["delta_ratio_baseline"], data["delta_ratio_std"]
    )
    data["baseline_observations"] = _prior_count(data)
    data["of_history_ready"] = data["baseline_observations"].ge(
        settings.min_history_sessions
    ) & data["volume_baseline"].gt(0.0)

    data["vwap"] = data.get("vwap", np.nan)
    data["vwap_distance"] = _safe_ratio(data["close"] - data["vwap"], data["vwap"])
    volume_intensity = _safe_ratio(data["volume"], data["volume_baseline"])
    data["price_impact"] = _safe_ratio(data["bar_return"], volume_intensity)
    data["flow_efficiency"] = _safe_ratio(
        data["bar_return"].abs(), data["delta_ratio"].abs().clip(lower=1e-9)
    )
    data["transaction_share_of_bar"] = _safe_ratio(data["total_transaction_volume"], data["volume"])

    # Coverage and large-print gates are opt-in quality controls.  A zero/None threshold leaves
    # the gate open, while an enabled threshold makes incomplete or suspiciously concentrated
    # bars ineligible for signals without rewriting their raw aggregates.
    coverage = pd.to_numeric(
        data.get("transaction_coverage", data["transaction_share_of_bar"]), errors="coerce"
    )
    data["transaction_coverage"] = coverage
    coverage_valid = pd.Series(True, index=data.index)
    if settings.min_transaction_coverage > 0.0:
        coverage_valid &= coverage.ge(settings.min_transaction_coverage)
    if settings.max_transaction_coverage is not None:
        coverage_valid &= coverage.le(settings.max_transaction_coverage)
    large_share = pd.to_numeric(data.get("large_trade_share", np.nan), errors="coerce")
    data["large_trade_share"] = large_share
    large_share_valid = pd.Series(True, index=data.index)
    if settings.min_large_trade_share > 0.0:
        large_share_valid &= large_share.ge(settings.min_large_trade_share)
    if settings.max_large_trade_share is not None:
        large_share_valid &= large_share.le(settings.max_large_trade_share)
    data["of_filter_valid"] = (coverage_valid & large_share_valid).astype(bool)
    data["of_data_valid"] &= data["of_filter_valid"].to_numpy()

    # CVD is descriptive.  Missing transaction bars are carried as a visible gap marker rather
    # than silently declared zero flow.  The cumulative state resumes after the gap; callers can
    # request a per-session reset when overnight carry is not meaningful for their experiment.
    delta_observed = data["delta"].notna()
    delta_for_cumsum = data["delta"].fillna(0.0)
    if settings.cvd_reset_each_session and "session_date_key" in data.columns:
        cvd_keys = ["symbol", "session_date_key"]
    else:
        cvd_keys = ["symbol"]
    data["cvd"] = delta_for_cumsum.groupby(
        [data[key] for key in cvd_keys], sort=False, dropna=False
    ).cumsum()
    data["cvd"] = data["cvd"].where(delta_observed)
    if "session_date_key" in data.columns:
        data["session_cvd"] = delta_for_cumsum.groupby(
            [data["symbol"], data["session_date_key"]], sort=False, dropna=False
        ).cumsum()
        data["session_cvd"] = data["session_cvd"].where(delta_observed)
    else:
        data["session_cvd"] = data["cvd"]

    threshold = settings.divergence_price_threshold
    bearish_divergence = data["delta_ratio"].ge(settings.entry_delta_ratio) & data["bar_return"].le(
        -threshold
    )
    bullish_divergence = data["delta_ratio"].le(settings.exit_delta_ratio) & data["bar_return"].ge(
        threshold
    )
    data["flow_price_divergence"] = np.select(
        [bearish_divergence, bullish_divergence], [1, -1], default=0
    ).astype(int)

    high_effort = data["relative_volume"].ge(settings.absorption_rvol)
    small_result = data["bar_return"].abs().le(settings.absorption_max_abs_return)
    data["bullish_absorption"] = (
        data["of_data_valid"]
        & high_effort
        & small_result
        & data["delta_ratio"].le(settings.exit_delta_ratio)
        & data["close_location"].ge(settings.entry_close_location)
    )
    data["bearish_absorption"] = (
        data["of_data_valid"]
        & high_effort
        & small_result
        & data["delta_ratio"].ge(settings.entry_delta_ratio)
        & data["close_location"].le(settings.exit_close_location)
    )

    eligible = data["of_data_valid"] & data["of_history_ready"]
    entry_raw = (
        eligible
        & data["delta_ratio"].ge(settings.entry_delta_ratio)
        & data["relative_volume"].ge(settings.entry_rvol)
        & data["close_location"].ge(settings.entry_close_location)
        & data["bar_return"].ge(settings.entry_price_return)
    )
    if settings.entry_delta_zscore is not None:
        entry_raw &= data["delta_ratio_zscore"].ge(settings.entry_delta_zscore)
    if settings.use_vwap_filter:
        entry_raw &= data["vwap"].gt(0.0) & data["vwap_distance"].ge(settings.entry_vwap_distance)
    exit_raw = (
        eligible
        & data["delta_ratio"].le(settings.exit_delta_ratio)
        & data["relative_volume"].ge(settings.exit_rvol)
        & data["close_location"].le(settings.exit_close_location)
    )
    if settings.exit_delta_zscore is not None:
        exit_raw &= data["delta_ratio_zscore"].le(settings.exit_delta_zscore)
    if settings.exit_price_return is not None:
        exit_raw &= data["bar_return"].le(settings.exit_price_return)
    if settings.use_vwap_exit_filter:
        exit_raw &= data["vwap"].gt(0.0) & data["vwap_distance"].le(settings.exit_vwap_distance)
    if settings.use_absorption_exit:
        exit_raw |= eligible & data["bearish_absorption"]
    exit_raw |= eligible & data["flow_price_divergence"].eq(1)
    data["of_entry_candidate"] = entry_raw.astype(bool)
    data["of_exit_candidate"] = exit_raw.astype(bool)
    data["of_entry_signal"] = (
        _persistent(
            entry_raw,
            data,
            settings.entry_persistence,
            same_session=settings.persistence_same_session,
            bar_minutes=settings.bar_minutes,
        )
        & eligible
    )
    data["of_exit_signal"] = (
        _persistent(
            exit_raw,
            data,
            settings.exit_persistence,
            same_session=settings.persistence_same_session,
            bar_minutes=settings.bar_minutes,
        )
        & eligible
    )
    data["of_entry_signal"] = data["of_entry_signal"].fillna(False).astype(bool)
    data["of_exit_signal"] = data["of_exit_signal"].fillna(False).astype(bool)
    data["of_signal"] = np.select(
        [data["of_entry_signal"], data["of_exit_signal"]], [1, -1], default=0
    ).astype(int)
    data["is_session_last"] = _session_last_by_clock(data, settings.bar_minutes)
    data["of_feature_version"] = FEATURE_VERSION
    return data


def summarize_order_flow(frame: pd.DataFrame) -> dict[str, Any]:
    """Return compact counts and coverage statistics for a feature frame."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    observed = frame.get("transaction_observed", pd.Series(False, index=frame.index)).fillna(False)
    valid = frame.get("of_data_valid", pd.Series(False, index=frame.index)).fillna(False)
    entry = frame.get("of_entry_signal", pd.Series(False, index=frame.index)).fillna(False)
    exit_ = frame.get("of_exit_signal", pd.Series(False, index=frame.index)).fillna(False)
    coverage_source = frame.get("transaction_coverage", pd.Series(np.nan, index=frame.index))
    coverage = pd.to_numeric(coverage_source, errors="coerce")
    return {
        "bars": len(frame),
        "transaction_observed_bars": int(observed.astype(bool).sum()),
        "transaction_coverage_pct": float(observed.astype(bool).mean() * 100.0)
        if len(frame)
        else 0.0,
        "feature_valid_bars": int(valid.astype(bool).sum()),
        "history_ready_bars": int(
            frame.get("of_history_ready", pd.Series(False, index=frame.index))
            .fillna(False)
            .astype(bool)
            .sum()
        ),
        "entry_signal_count": int(entry.astype(bool).sum()),
        "exit_signal_count": int(exit_.astype(bool).sum()),
        "median_transaction_coverage": float(coverage.median()) if coverage.notna().any() else None,
    }


__all__ = ["FEATURE_VERSION", "compute_order_flow_features", "summarize_order_flow"]
