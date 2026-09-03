"""Advanced, transaction-only order-flow proxy features.

The V2 path is deliberately built from fields that ``easy-tdx`` can actually supply: aggregated
transaction direction, transaction size/counts, and OHLCV bars.  It implements useful
microstructure *proxies* (multi-scale pressure, volume-synchronized imbalance, impact efficiency,
absorption, and regime alignment), but never presents them as a complete order-book OFI or an
institution identifier.  Every rolling calculation is causal and resets at an intraday session
break or a timestamp gap.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import OrderFlowConfig

V2_FEATURE_VERSION = "order-flow-proxy-2-features"

# The composite is an equal-weight mean of the available components.  Large-print information is
# optional because older saved feature files predate the directional large-print aggregation.
V2_SCORE_COMPONENTS = (
    "v2_flow_pressure",
    "v2_execution_quality",
    "v2_absorption_score",
    "v2_regime_alignment",
    "v2_divergence_score",
    "v2_large_flow_score",
)
V2_STRENGTH_COMPONENTS = (
    "v2_activity_percentile",
    "v2_pressure_percentile",
    "v2_large_trade_percentile",
    "v2_size_concentration_percentile",
    "v2_toxicity_percentile",
)


def _numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.where(denominator > 0.0)
    result = numerator / denominator
    return result.replace([np.inf, -np.inf], np.nan)


def _clip(values: pd.Series, lower: float = -1.0, upper: float = 1.0) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").clip(lower, upper)


def _bool_series(values: pd.Series, *, name: str, default: bool = False) -> pd.Series:
    """Parse booleans from both in-memory frames and CSV round trips."""

    if pd.api.types.is_bool_dtype(values):
        return values.fillna(default).astype(bool)
    text = values.astype("string").str.strip().str.casefold()
    numeric = pd.to_numeric(values, errors="coerce")
    missing = values.isna() | text.eq("")
    truthy = text.isin({"true", "t", "yes", "y"}) | numeric.eq(1.0).fillna(False)
    falsy = text.isin({"false", "f", "no", "n"}) | numeric.eq(0.0).fillna(False)
    invalid = ~(missing | truthy | falsy)
    if invalid.any():
        examples = values.loc[invalid].astype("string").drop_duplicates().head(3).tolist()
        raise ValueError(f"{name} contains invalid boolean values: {examples}")
    result = pd.Series(default, index=values.index, dtype=bool)
    result.loc[truthy] = True
    result.loc[falsy] = False
    return result


def _session_part(timestamp: pd.Series, session_bar: pd.Series) -> pd.Series:
    minutes = timestamp.dt.hour * 60 + timestamp.dt.minute
    values = np.select(
        [
            session_bar & minutes.between(9 * 60 + 30, 11 * 60 + 30),
            session_bar & minutes.between(13 * 60, 15 * 60 - 1),
        ],
        ["am", "pm"],
        default="out",
    )
    return pd.Series(values, index=timestamp.index, dtype="string")


def _segment_ids(
    data: pd.DataFrame,
    bar_minutes: int,
    *,
    reset_each_session: bool,
) -> pd.Series:
    """Create causal segments, optionally carrying state across known session breaks.

    A configured session reset separates morning/afternoon and calendar sessions.  With the reset
    disabled, those known market breaks are allowed to carry EWM/rolling state; an unexpected gap
    inside a session and any out-of-session row still create a boundary so missing data cannot be
    interpreted as an observed bar.
    """

    previous_timestamp = data.groupby("symbol", sort=False)["timestamp"].shift(1)
    previous_part = data.groupby("symbol", sort=False)["v2_session_part"].shift(1)
    previous_date = data.groupby("symbol", sort=False)["session_date_key"].shift(1)
    gap_seconds = (data["timestamp"] - previous_timestamp).dt.total_seconds()
    session_change = data["v2_session_part"].ne(previous_part) | data["session_date_key"].ne(
        previous_date
    )
    out_of_session = data["v2_session_part"].eq("out") | previous_part.eq("out")
    gap = gap_seconds.gt(float(bar_minutes * 60))
    unexpected_gap = gap & ~session_change
    boundary = previous_timestamp.isna() | out_of_session | unexpected_gap
    if reset_each_session:
        boundary |= session_change
    return boundary.groupby(data["symbol"], sort=False, dropna=False).cumsum().astype("int64")


def _group_keys(data: pd.DataFrame, settings: OrderFlowConfig) -> list[str]:
    if settings.v2_reset_each_session:
        return ["symbol", "session_date_key", "v2_session_part", "v2_segment_id"]
    return ["symbol", "v2_segment_id"]


def _rolling(
    data: pd.DataFrame,
    values: pd.Series,
    keys: list[str],
    window: int,
    method: str,
    *,
    min_periods: int = 1,
) -> pd.Series:
    temp = data[keys].copy()
    temp["_value"] = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)

    def calculate(series: pd.Series) -> pd.Series:
        rolling = series.rolling(window=window, min_periods=min_periods)
        return getattr(rolling, method)()

    return temp.groupby(keys, sort=False, dropna=False)["_value"].transform(calculate)


def _ewm(data: pd.DataFrame, values: pd.Series, keys: list[str], span: int) -> pd.Series:
    temp = data[keys].copy()
    temp["_value"] = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return temp.groupby(keys, sort=False, dropna=False)["_value"].transform(
        lambda series: series.ewm(span=span, adjust=False, min_periods=1).mean()
    )


def _group_shift(
    data: pd.DataFrame, values: pd.Series, keys: list[str], periods: int = 1
) -> pd.Series:
    temp = data[keys].copy()
    temp["_value"] = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return temp.groupby(keys, sort=False, dropna=False)["_value"].shift(periods)


def _group_diff(data: pd.DataFrame, values: pd.Series, keys: list[str]) -> pd.Series:
    temp = data[keys].copy()
    temp["_value"] = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return temp.groupby(keys, sort=False, dropna=False)["_value"].diff()


def _rolling_percentile(
    data: pd.DataFrame,
    values: pd.Series,
    keys: list[str],
    window: int,
    min_periods: int,
) -> pd.Series:
    """Percentile of the current value within a trailing window, including the current bar."""

    def last_percentile(raw: np.ndarray) -> float:
        if len(raw) == 0 or not np.isfinite(raw[-1]):
            return np.nan
        valid = raw[np.isfinite(raw)]
        if len(valid) < min_periods:
            return np.nan
        current = raw[-1]
        less = float(np.count_nonzero(valid < current))
        equal = float(np.count_nonzero(valid == current))
        # Mid-rank keeps a constant series at 0.5 instead of artificially at 1.0.
        return (less + 0.5 * equal) / float(len(valid))

    temp = data[keys].copy()
    temp["_value"] = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return temp.groupby(keys, sort=False, dropna=False)["_value"].transform(
        lambda series: series.rolling(window=window, min_periods=1).apply(last_percentile, raw=True)
    )


def _prior_slot_median(
    data: pd.DataFrame, values: pd.Series, window: int, min_periods: int = 2
) -> pd.Series:
    temp = data[["symbol", "slot_key"]].copy()
    temp["_value"] = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return temp.groupby(["symbol", "slot_key"], sort=False, dropna=False)["_value"].transform(
        lambda series: series.shift(1).rolling(window=window, min_periods=min_periods).median()
    )


def _weighted_mean(frame: pd.DataFrame, columns: list[str], minimum: int = 1) -> pd.Series:
    values = frame[columns].apply(pd.to_numeric, errors="coerce")
    count = values.notna().sum(axis=1)
    result = values.mean(axis=1, skipna=True)
    return result.where(count.ge(minimum))


def _quote_proxies(data: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    bid_volume_columns = [f"bid_vol{i}" for i in range(1, 6) if f"bid_vol{i}" in data]
    ask_volume_columns = [f"ask_vol{i}" for i in range(1, 6) if f"ask_vol{i}" in data]
    if not bid_volume_columns or not ask_volume_columns:
        missing = pd.Series(np.nan, index=data.index, dtype="float64")
        return missing, missing.copy(), pd.Series(False, index=data.index, dtype=bool)
    bid_values = data[bid_volume_columns].apply(pd.to_numeric, errors="coerce")
    ask_values = data[ask_volume_columns].apply(pd.to_numeric, errors="coerce")
    bid_volume = bid_values.sum(axis=1, min_count=1)
    ask_volume = ask_values.sum(axis=1, min_count=1)
    imbalance = _safe_ratio(bid_volume - ask_volume, bid_volume + ask_volume)
    bid_price = _numeric(data, "bid1")
    ask_price = _numeric(data, "ask1")
    microprice = _safe_ratio(
        ask_price * bid_volume + bid_price * ask_volume, bid_volume + ask_volume
    )
    midpoint = (bid_price + ask_price) / 2.0
    edge = _safe_ratio(microprice - midpoint, midpoint)
    observed = (
        imbalance.notna()
        & edge.notna()
        & bid_price.gt(0.0)
        & ask_price.ge(bid_price)
        & bid_volume.ge(0.0)
        & ask_volume.ge(0.0)
    )
    return imbalance.where(observed), edge.where(observed), observed


def _persistent(data: pd.DataFrame, mask: pd.Series, keys: list[str], length: int) -> pd.Series:
    temp = data[keys].copy()
    temp["_mask"] = mask.fillna(False).astype(float).to_numpy()
    return (
        temp.groupby(keys, sort=False, dropna=False)["_mask"]
        .transform(lambda series: series.rolling(length, min_periods=length).sum().eq(length))
        .astype(bool)
    )


def compute_order_flow_v2_features(
    frame: pd.DataFrame,
    config: OrderFlowConfig | None = None,
) -> pd.DataFrame:
    """Add advanced V2 order-flow proxy features to an aggregated bar frame.

    The function accepts either the output of ``aggregate_transactions_to_bars`` or an existing V1
    feature frame.  It never changes V1 signal columns.  Feature values at bar ``t`` use only
    transaction/OHLCV observations through ``t``; the future-return prediction module remains
    responsible for constructing labels and purging them from training windows.
    """

    settings = config or OrderFlowConfig()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("order-flow V2 input must be a pandas DataFrame")
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
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("order-flow V2 frame is missing columns: " + ", ".join(missing))

    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("order-flow V2 frame contains invalid timestamps")
    if "symbol" not in data.columns:
        data["symbol"] = "__single__"
    data["symbol"] = data["symbol"].astype(str)
    data = data.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
    if data.duplicated(["symbol", "timestamp"]).any():
        raise ValueError("order-flow V2 frame contains duplicate symbol/timestamp rows")

    for column in (
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
        "transaction_amount",
        "transaction_rows",
        "trade_count",
        "trade_volume_squared",
        "trade_size_hhi",
        "max_trade_volume",
        "large_trade_volume",
        "large_delta",
        "large_delta_ratio",
        "large_trade_share",
        "clv",
        "bar_return",
        "relative_transaction_volume",
    ):
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")

    if "session_date_key" not in data.columns:
        data["session_date_key"] = data["timestamp"].dt.strftime("%Y%m%d").astype("int64")
    else:
        fallback_date = data["timestamp"].dt.strftime("%Y%m%d").astype("int64")
        data["session_date_key"] = (
            pd.to_numeric(data["session_date_key"], errors="coerce")
            .fillna(fallback_date)
            .astype("int64")
        )
    if "slot_key" not in data.columns:
        data["slot_key"] = data["timestamp"].dt.hour * 60 + data["timestamp"].dt.minute
    if "is_session_bar" in data.columns:
        session_bar = _bool_series(data["is_session_bar"], name="is_session_bar")
    else:
        session_bar = pd.Series(True, index=data.index, dtype=bool)
    data["v2_session_part"] = _session_part(data["timestamp"], session_bar)
    data["v2_segment_id"] = _segment_ids(
        data,
        settings.bar_minutes,
        reset_each_session=settings.v2_reset_each_session,
    )
    keys = _group_keys(data, settings)

    if "of_data_valid" in data.columns:
        base_quality = _bool_series(data["of_data_valid"], name="of_data_valid")
    else:
        base_quality = pd.Series(True, index=data.index, dtype=bool)
    if "transaction_observed" in data.columns:
        observed = _bool_series(data["transaction_observed"], name="transaction_observed")
    else:
        observed = data["total_transaction_volume"].notna()
    finite_ohlc = np.isfinite(
        data[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    ).all(axis=1)
    valid_ohlc = (
        data["high"].ge(data[["open", "close"]].max(axis=1))
        & data["low"].le(data[["open", "close"]].min(axis=1))
        & data["high"].ge(data["low"])
        & data["volume"].ge(0.0)
        & data[["open", "high", "low", "close"]].gt(0.0).all(axis=1)
    )
    total_volume = data["total_transaction_volume"]
    base_valid = (
        base_quality
        & observed
        & finite_ohlc
        & valid_ohlc
        & total_volume.gt(0.0)
        & data["v2_session_part"].ne("out")
    )
    data["of_v2_base_valid"] = base_valid.astype(bool)

    # An invalid/missing print bar is a state boundary.  Without this extra boundary, pandas EWM
    # would carry the last valid pressure through a data gap and make the first recovered bar look
    # as if the missing observation had actually occurred.
    previous_segment = data.groupby("symbol", sort=False)["v2_segment_id"].shift(1)
    previous_valid = data.groupby("symbol", sort=False)["of_v2_base_valid"].shift(1)
    segment_boundary = (
        data["v2_segment_id"].ne(previous_segment) | base_valid.ne(previous_valid) | ~base_valid
    )
    data["v2_segment_id"] = (
        segment_boundary.groupby(data["symbol"], sort=False, dropna=False).cumsum().astype("int64")
    )
    keys = _group_keys(data, settings)

    # V1 frames can already contain many columns.  Consolidate before adding the V2 diagnostics so
    # large live runs do not accumulate a highly fragmented pandas frame.
    data = data.copy()

    flow = _safe_ratio(data["delta"], total_volume).clip(-1.0, 1.0)
    flow = flow.where(base_valid)
    data["v2_flow_ratio"] = flow
    data["v2_signed_amount_ratio"] = _clip(
        _safe_ratio(
            _numeric(data, "buy_amount") - _numeric(data, "sell_amount"),
            _numeric(data, "transaction_amount"),
        )
    ).where(base_valid)

    for label, span in (
        ("fast", settings.v2_fast_span),
        ("medium", settings.v2_medium_span),
        ("slow", settings.v2_slow_span),
    ):
        data[f"v2_flow_ema_{label}"] = _clip(_ewm(data, flow, keys, span))
    ema_columns = ["v2_flow_ema_fast", "v2_flow_ema_medium", "v2_flow_ema_slow"]
    data["v2_flow_pressure"] = _clip(_weighted_mean(data, ema_columns, minimum=2))
    data["v2_flow_trend"] = _clip(data["v2_flow_ema_fast"] - data["v2_flow_ema_slow"])
    data["v2_flow_acceleration"] = _clip(_group_diff(data, data["v2_flow_ema_fast"], keys))
    data["v2_flow_persistence"] = _clip(
        _rolling(data, np.sign(flow), keys, settings.v2_flow_window, "mean", min_periods=2)
    )
    rolling_delta = _rolling(
        data, data["delta"].where(base_valid), keys, settings.v2_flow_window, "sum"
    )
    rolling_abs_delta = _rolling(
        data, data["delta"].abs().where(base_valid), keys, settings.v2_flow_window, "sum"
    )
    data["v2_flow_consistency"] = _safe_ratio(rolling_delta.abs(), rolling_abs_delta).clip(0.0, 1.0)
    buy_roll = _rolling(
        data, data["buy_volume"].where(base_valid), keys, settings.v2_flow_window, "sum"
    )
    sell_roll = _rolling(
        data, data["sell_volume"].where(base_valid), keys, settings.v2_flow_window, "sum"
    )
    neutral_roll = _rolling(
        data, data["neutral_volume"].where(base_valid), keys, settings.v2_flow_window, "sum"
    )
    total_roll = buy_roll + sell_roll + neutral_roll
    proportions = pd.concat(
        [
            _safe_ratio(buy_roll, total_roll),
            _safe_ratio(sell_roll, total_roll),
            _safe_ratio(neutral_roll, total_roll),
        ],
        axis=1,
    ).clip(1e-12, 1.0)
    entropy = -(proportions * np.log(proportions)).sum(axis=1) / np.log(3.0)
    data["v2_direction_entropy"] = entropy.where(total_roll.gt(0.0))
    data["v2_flow_concentration"] = (1.0 - data["v2_direction_entropy"]).clip(0.0, 1.0)
    data["v2_vpin_proxy"] = _safe_ratio(
        _rolling(data, data["delta"].abs().where(base_valid), keys, settings.v2_flow_window, "sum"),
        total_roll,
    ).clip(0.0, 1.0)

    activity = _numeric(data, "relative_transaction_volume")
    activity = activity.where(activity.gt(0.0))
    # A raw transaction-volume fallback keeps diagnostics usable for a newly collected symbol;
    # the percentile is still calculated only from past/current observations.
    activity = activity.fillna(total_volume.where(base_valid))
    data["v2_activity_ratio"] = activity
    data["v2_activity_percentile"] = _rolling_percentile(
        data,
        activity.where(base_valid),
        keys,
        settings.v2_percentile_window,
        settings.v2_min_observations,
    )
    data["v2_pressure_percentile"] = _rolling_percentile(
        data, flow.abs(), keys, settings.v2_percentile_window, settings.v2_min_observations
    )

    large_share = _numeric(data, "large_trade_share")
    if large_share.isna().all() and "large_trade_volume" in data:
        large_share = _safe_ratio(_numeric(data, "large_trade_volume"), total_volume)
    data["v2_large_trade_share"] = large_share.clip(0.0, 1.0).where(base_valid)
    data["v2_large_trade_percentile"] = _rolling_percentile(
        data,
        large_share.where(base_valid),
        keys,
        settings.v2_percentile_window,
        settings.v2_min_observations,
    )
    large_delta_ratio = _numeric(data, "large_delta_ratio")
    if large_delta_ratio.isna().all():
        large_delta_ratio = _safe_ratio(
            _numeric(data, "large_delta"), _numeric(data, "large_trade_volume")
        )
    data["v2_large_delta_ratio"] = large_delta_ratio.clip(-1.0, 1.0).where(base_valid)
    data["v2_large_flow_score"] = _clip(
        large_delta_ratio.clip(-1.0, 1.0) * large_share.clip(0.0, 1.0)
    ).where(base_valid)

    hhi = _numeric(data, "trade_size_hhi")
    if hhi.isna().all() and {"trade_volume_squared", "total_transaction_volume"}.issubset(data):
        hhi = _safe_ratio(_numeric(data, "trade_volume_squared"), total_volume.pow(2))
    data["v2_trade_size_hhi"] = hhi.clip(0.0, 1.0).where(base_valid)
    data["v2_size_concentration_percentile"] = _rolling_percentile(
        data,
        hhi.where(base_valid),
        keys,
        settings.v2_percentile_window,
        settings.v2_min_observations,
    )
    max_share = _safe_ratio(_numeric(data, "max_trade_volume"), total_volume)
    data["v2_max_trade_share"] = max_share.where(base_valid)
    trade_count = _numeric(data, "trade_count")
    data["v2_trade_count"] = trade_count.where(base_valid)
    data["v2_trade_count_baseline"] = _prior_slot_median(
        data, trade_count.where(base_valid), settings.volume_baseline_sessions
    )
    data["v2_trade_intensity_ratio"] = _safe_ratio(trade_count, data["v2_trade_count_baseline"])
    data["v2_trade_size_cv"] = (
        (data["v2_trade_size_hhi"] * trade_count - 1.0).clip(lower=0.0).pow(0.5)
    ).where(base_valid)

    data["v2_toxicity_percentile"] = _rolling_percentile(
        data,
        data["v2_vpin_proxy"],
        keys,
        settings.v2_percentile_window,
        settings.v2_min_observations,
    )
    strength_frame = data[list(V2_STRENGTH_COMPONENTS)]
    strength_count = strength_frame.notna().sum(axis=1)
    data["v2_strength_component_count"] = strength_count.astype("int64")
    strength_minimum = min(settings.v2_min_component_count, len(V2_STRENGTH_COMPONENTS))
    data["v2_strength_min_component_count"] = strength_minimum
    data["v2_strong_participation_score"] = (
        strength_frame.mean(axis=1, skipna=True).mul(100.0)
    ).where(strength_count.ge(strength_minimum))
    data["v2_participation_confidence"] = strength_count / float(len(V2_STRENGTH_COMPONENTS))
    data["v2_participation_direction"] = data["v2_flow_pressure"].mul(100.0).clip(-100.0, 100.0)
    if "strong_participation_score" not in data.columns:
        data["strong_participation_score"] = data["v2_strong_participation_score"]
    if "participation_direction" not in data.columns:
        data["participation_direction"] = data["v2_participation_direction"]

    close = data["close"]
    open_price = data["open"]
    high = data["high"]
    low = data["low"]
    bar_return = _numeric(data, "bar_return")
    if bar_return.isna().all():
        bar_return = _safe_ratio(close - open_price, open_price)
    clv = _numeric(data, "clv")
    if clv.isna().all():
        clv = _safe_ratio(2.0 * close - high - low, high - low)
    data["v2_bar_return"] = bar_return.where(base_valid)
    data["v2_clv"] = clv.where(base_valid)
    range_pct = _safe_ratio(high - low, close).where(base_valid)
    atr_pct = _rolling(data, range_pct, keys, settings.v2_regime_window, "median", min_periods=2)
    data["v2_range_pct"] = range_pct
    data["v2_atr_pct"] = atr_pct
    response_scale = atr_pct.where(atr_pct.gt(1e-8))
    normalized_return = _safe_ratio(bar_return, response_scale)
    data["v2_normalized_return"] = normalized_return.where(base_valid)
    response = pd.Series(np.tanh(normalized_return), index=data.index) * np.sign(
        data["v2_flow_pressure"]
    )
    data["v2_signed_price_response"] = response.where(base_valid)
    data["v2_execution_quality"] = (
        (response * (0.5 + 0.5 * data["v2_flow_consistency"])).clip(-1.0, 1.0).where(base_valid)
    )

    tx_median = _rolling(
        data, total_volume.where(base_valid), keys, settings.v2_flow_window, "median"
    )
    activity_scale = _safe_ratio(total_volume, tx_median)
    data["v2_kyle_lambda_proxy"] = _safe_ratio(bar_return.abs(), activity_scale.pow(0.5)).where(
        base_valid
    )
    impact_median = _rolling(
        data,
        data["v2_kyle_lambda_proxy"],
        keys,
        settings.v2_flow_window,
        "median",
        min_periods=min(3, settings.v2_flow_window),
    )
    impact_std = _rolling(
        data,
        data["v2_kyle_lambda_proxy"],
        keys,
        settings.v2_flow_window,
        "std",
        min_periods=min(3, settings.v2_flow_window),
    )
    data["v2_impact_zscore"] = _safe_ratio(data["v2_kyle_lambda_proxy"] - impact_median, impact_std)
    data["v2_impact_score"] = np.tanh(data["v2_impact_zscore"].clip(-4.0, 4.0) / 2.0) * np.sign(
        data["v2_flow_pressure"]
    )

    amount = _numeric(data, "transaction_amount")
    amount = amount.where(amount.gt(0.0), _numeric(data, "vwap") * total_volume)
    amount = amount.where(amount.gt(0.0), close * total_volume)
    rolling_amount = _rolling(data, amount.where(base_valid), keys, settings.v2_flow_window, "sum")
    rolling_volume = _rolling(
        data, total_volume.where(base_valid), keys, settings.v2_flow_window, "sum"
    )
    data["v2_rolling_vwap"] = _safe_ratio(rolling_amount, rolling_volume)
    data["v2_vwap_distance"] = _safe_ratio(close - data["v2_rolling_vwap"], data["v2_rolling_vwap"])

    activity_strength = _weighted_mean(
        data,
        ["v2_activity_percentile", "v2_large_trade_percentile", "v2_toxicity_percentile"],
        minimum=1,
    ).clip(0.0, 1.0)
    response_abs = data["v2_signed_price_response"].abs().clip(0.0, 1.0)
    close_location = ((clv + 1.0) / 2.0).clip(0.0, 1.0)
    buy_pressure = data["v2_flow_pressure"].clip(lower=0.0)
    sell_pressure = (-data["v2_flow_pressure"]).clip(lower=0.0)
    bullish_absorption = (
        sell_pressure * activity_strength * (1.0 - response_abs) * (0.5 + 0.5 * close_location)
    )
    bearish_absorption = (
        buy_pressure * activity_strength * (1.0 - response_abs) * (1.0 - 0.5 * close_location)
    )
    data["v2_bullish_absorption"] = bullish_absorption.clip(0.0, 1.0).where(base_valid)
    data["v2_bearish_absorption"] = bearish_absorption.clip(0.0, 1.0).where(base_valid)
    data["v2_absorption_score"] = (
        data["v2_bullish_absorption"] - data["v2_bearish_absorption"]
    ).clip(-1.0, 1.0)
    deceleration = (-data["v2_flow_acceleration"] * np.sign(data["v2_flow_pressure"])).clip(
        lower=0.0, upper=1.0
    )
    data["v2_exhaustion_score"] = (
        (
            -np.sign(data["v2_flow_pressure"])
            * data["v2_flow_pressure"].abs()
            * activity_strength
            * deceleration
        )
        .clip(-1.0, 1.0)
        .where(base_valid)
    )

    past_close = _group_shift(data, close, keys, settings.v2_flow_window)
    price_move = _safe_ratio(close - past_close, past_close)
    flow_move = _rolling(data, flow.where(base_valid), keys, settings.v2_flow_window, "sum")
    price_norm = np.tanh(_safe_ratio(price_move, atr_pct * np.sqrt(settings.v2_flow_window)))
    flow_norm = np.tanh(flow_move)
    divergence_magnitude = pd.concat([flow_norm.abs(), price_norm.abs()], axis=1).min(axis=1)
    disagreement = flow_norm.mul(price_norm).lt(0.0)
    # Positive values denote hidden demand (negative flow with a resilient/rising price); negative
    # values denote supply (positive flow failing to lift price).
    divergence_score = (-flow_norm * divergence_magnitude).where(disagreement, 0.0)
    data["v2_divergence_score"] = divergence_score.clip(-1.0, 1.0).where(base_valid)
    data["v2_price_flow_divergence"] = (-data["v2_divergence_score"]).clip(-1.0, 1.0)
    data["v2_cvd_slope"] = _safe_ratio(
        flow_move, _rolling(data, flow.abs(), keys, settings.v2_flow_window, "sum")
    )

    past_close_regime = _group_shift(data, close, keys, settings.v2_regime_window)
    price_change = close - past_close_regime
    close_diff = _group_diff(data, close, keys).abs()
    path = _rolling(data, close_diff, keys, settings.v2_regime_window, "sum")
    efficiency = _safe_ratio(price_change.abs(), path).clip(0.0, 1.0)
    data["v2_trend_efficiency"] = efficiency.where(base_valid)
    data["v2_regime_score"] = (np.sign(price_change) * efficiency).clip(-1.0, 1.0).where(base_valid)
    volatility = _rolling(
        data,
        bar_return.where(base_valid),
        keys,
        settings.v2_regime_window,
        "std",
        min_periods=min(3, settings.v2_regime_window),
    )
    data["v2_realized_volatility"] = volatility
    volatility_percentile = _rolling_percentile(
        data, volatility, keys, settings.v2_percentile_window, settings.v2_min_observations
    )
    data["v2_volatility_percentile"] = volatility_percentile
    data["v2_regime"] = pd.Series(
        np.select(
            [
                volatility_percentile.ge(0.80),
                data["v2_regime_score"].ge(0.35),
                data["v2_regime_score"].le(-0.35),
            ],
            ["high_volatility", "trend_up", "trend_down"],
            default="range",
        ),
        index=data.index,
        dtype="string",
    ).where(base_valid, "insufficient")
    data["v2_regime_alignment"] = _clip(data["v2_flow_pressure"] * data["v2_regime_score"]).where(
        base_valid
    )

    quote_imbalance, microprice_edge, quote_observed = _quote_proxies(data)
    data["v2_quote_imbalance"] = quote_imbalance
    data["v2_microprice_edge"] = microprice_edge
    data["v2_quote_observed"] = quote_observed

    score_frame = data[list(V2_SCORE_COMPONENTS)]
    score_count = score_frame.notna().sum(axis=1)
    data["v2_score_component_count"] = score_count.astype("int64")
    data["v2_score_min_component_count"] = settings.v2_min_component_count
    data["v2_score_confidence"] = score_count / float(len(V2_SCORE_COMPONENTS))
    data["order_flow_v2_score"] = (
        score_frame.mean(axis=1, skipna=True)
        .where(score_count.ge(settings.v2_min_component_count) & base_valid)
        .clip(-1.0, 1.0)
    )
    data["v2_score_strength"] = data["order_flow_v2_score"].abs()
    data["v2_history_ready"] = data["v2_strength_component_count"].ge(strength_minimum) & data[
        "v2_score_component_count"
    ].ge(settings.v2_min_component_count)
    data["order_flow_v2_score"] = data["order_flow_v2_score"].where(data["v2_history_ready"])
    data["v2_score_strength"] = data["order_flow_v2_score"].abs()
    data["of_v2_data_valid"] = (
        base_valid & data["v2_history_ready"] & data["order_flow_v2_score"].notna()
    ).astype(bool)
    data["of_v2_invalid_reason"] = np.select(
        [
            ~base_valid,
            data["v2_strength_component_count"].lt(strength_minimum),
            data["v2_score_component_count"].lt(settings.v2_min_component_count),
        ],
        [
            "invalid_or_missing_transaction",
            "strength_history_not_ready",
            "score_components_missing",
        ],
        default="",
    )

    direction = data["v2_participation_direction"]
    strong_score = data["v2_strong_participation_score"]
    strong_buy = (
        data["of_v2_data_valid"]
        & strong_score.ge(settings.participation_strong_threshold)
        & direction.ge(settings.participation_direction_threshold)
    )
    strong_sell = (
        data["of_v2_data_valid"]
        & strong_score.ge(settings.participation_strong_threshold)
        & direction.le(-settings.participation_direction_threshold)
    )
    participation_state = pd.Series(
        np.select(
            [
                strong_buy,
                strong_sell,
                direction.ge(settings.participation_direction_threshold),
                direction.le(-settings.participation_direction_threshold),
            ],
            ["strong_buy", "strong_sell", "buy_pressure", "sell_pressure"],
            default="balanced",
        ),
        index=data.index,
        dtype="string",
    ).where(data["of_v2_data_valid"], "insufficient")
    data["v2_participation_state"] = participation_state
    if "participation_state" not in data.columns:
        data["participation_state"] = participation_state
    data["v2_strong_buy"] = strong_buy
    data["v2_strong_sell"] = strong_sell
    confirmed_buy = (
        _persistent(data, strong_buy, keys, settings.participation_confirmation_bars)
        & data["of_v2_data_valid"]
    )
    confirmed_sell = (
        _persistent(data, strong_sell, keys, settings.participation_confirmation_bars)
        & data["of_v2_data_valid"]
    )
    data["v2_participation_confirmed"] = confirmed_buy | confirmed_sell
    if "participation_confirmed" not in data.columns:
        data["participation_confirmed"] = data["v2_participation_confirmed"]

    entry_score = data["order_flow_v2_score"].ge(settings.v2_score_entry_threshold)
    exit_score = data["order_flow_v2_score"].le(settings.v2_score_exit_threshold)
    # Absorption is contrarian evidence: sell pressure that fails to push price lower is a
    # potential long setup, while buy pressure that fails to lift price is a potential long exit.
    bullish_absorption_entry = data["v2_absorption_score"].ge(
        settings.v2_score_entry_threshold
    ) & direction.le(0.0)
    bearish_absorption_exit = data["v2_absorption_score"].le(
        settings.v2_score_exit_threshold
    ) & direction.ge(0.0)
    entry_raw = (
        data["of_v2_data_valid"]
        & data["v2_score_confidence"].ge(settings.v2_min_confidence)
        & (entry_score | bullish_absorption_entry)
        & (strong_buy | bullish_absorption_entry)
    )
    exit_raw = (
        data["of_v2_data_valid"]
        & data["v2_score_confidence"].ge(settings.v2_min_confidence)
        & (
            exit_score
            | strong_sell
            | bearish_absorption_exit
            | data["v2_exhaustion_score"].le(-settings.v2_exhaustion_threshold)
        )
    )
    if settings.v2_use_regime_filter:
        entry_raw &= data["v2_regime_score"].ge(0.0)
        exit_raw &= data["v2_regime_score"].le(0.0)
    data["of_v2_entry_candidate"] = entry_raw.astype(bool)
    data["of_v2_exit_candidate"] = exit_raw.astype(bool)
    if settings.v2_require_confirmation:
        data["of_v2_entry_signal"] = (
            _persistent(data, entry_raw, keys, settings.participation_confirmation_bars)
            & data["of_v2_data_valid"]
        )
        data["of_v2_exit_signal"] = (
            _persistent(data, exit_raw, keys, settings.participation_confirmation_bars)
            & data["of_v2_data_valid"]
        )
    else:
        data["of_v2_entry_signal"] = entry_raw.astype(bool)
        data["of_v2_exit_signal"] = exit_raw.astype(bool)
    data["of_v2_entry_signal"] = data["of_v2_entry_signal"].fillna(False).astype(bool)
    data["of_v2_exit_signal"] = data["of_v2_exit_signal"].fillna(False).astype(bool)
    data["of_v2_signal"] = np.select(
        [data["of_v2_entry_signal"], data["of_v2_exit_signal"]], [1, -1], default=0
    ).astype(int)
    data["of_v2_feature_version"] = V2_FEATURE_VERSION
    data["v2_strategy_version"] = "v2"
    if "strategy_version" not in data.columns:
        data["strategy_version"] = "v2"
    return data.copy()


def summarize_order_flow_v2(frame: pd.DataFrame) -> dict[str, Any]:
    """Return coverage, score, state, and signal counts for a V2 feature frame."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("order-flow V2 summary input must be a pandas DataFrame")
    valid = frame.get("of_v2_data_valid", pd.Series(False, index=frame.index))
    entry = frame.get("of_v2_entry_signal", pd.Series(False, index=frame.index))
    exit_ = frame.get("of_v2_exit_signal", pd.Series(False, index=frame.index))
    score = pd.to_numeric(
        frame.get("order_flow_v2_score", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    strong = pd.to_numeric(
        frame.get("v2_strong_participation_score", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    states = frame.get(
        "v2_participation_state",
        frame.get("participation_state", pd.Series("insufficient", index=frame.index)),
    )
    state_counts = {
        str(key): int(value) for key, value in states.value_counts(dropna=False).items()
    }
    median_strong = float(strong.median()) if strong.notna().any() else None
    return {
        "v2_valid_bars": int(_bool_series(valid, name="of_v2_data_valid").sum()),
        "v2_entry_signal_count": int(_bool_series(entry, name="of_v2_entry_signal").sum()),
        "v2_exit_signal_count": int(_bool_series(exit_, name="of_v2_exit_signal").sum()),
        "v2_score_mean": float(score.mean()) if score.notna().any() else None,
        "v2_score_median": float(score.median()) if score.notna().any() else None,
        "v2_strong_participation_median": median_strong,
        "strong_participation_median": median_strong,
        "v2_participation_states": state_counts,
        "participation_states": state_counts,
    }


# A descriptive alias makes the intended enrichment step explicit to callers that already have a
# V1 feature frame.
enrich_order_flow_v2_features = compute_order_flow_v2_features


__all__ = [
    "V2_FEATURE_VERSION",
    "V2_SCORE_COMPONENTS",
    "V2_STRENGTH_COMPONENTS",
    "compute_order_flow_v2_features",
    "enrich_order_flow_v2_features",
    "summarize_order_flow_v2",
]
