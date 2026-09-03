"""Causal evidence factor for unusually strong order-flow participation.

The factor estimates observable participation strength. It does not identify accounts or prove
that an institution traded. Every historical percentile uses prior same-clock observations only.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import OrderFlowConfig
from .normalize import parse_symbol

PARTICIPATION_FACTOR_NAME = "order_flow_participation_score"
PARTICIPATION_FACTOR_VERSION = "order-flow-participation-score-1"
PARTICIPATION_DAILY_QUANTILE = 0.90
PARTICIPATION_COMPONENT_COLUMNS = (
    "participation_activity",
    "participation_size",
    "participation_imbalance",
    "participation_control",
)


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    result = numerator / denominator.where(denominator > 0.0)
    return result.replace([np.inf, -np.inf], np.nan)


def _boolean_series(values: pd.Series, name: str) -> pd.Series:
    """Parse booleans without treating the string ``"False"`` as true."""

    if pd.api.types.is_bool_dtype(values.dtype):
        return values.fillna(False).astype(bool)
    normalized = values.astype("string").str.strip().str.casefold()
    mapped = normalized.map(
        {
            "true": True,
            "1": True,
            "yes": True,
            "false": False,
            "0": False,
            "no": False,
        }
    )
    missing = values.isna()
    invalid = mapped.isna() & ~missing
    if invalid.any():
        examples = sorted(normalized.loc[invalid].dropna().unique().tolist())[:3]
        raise ValueError(f"{name} contains invalid boolean value(s): {examples}")
    return mapped.fillna(False).astype(bool)


def _prior_percentile(
    frame: pd.DataFrame,
    values: pd.Series,
    *,
    window: int,
    min_history: int,
) -> pd.Series:
    """Return a causal same-clock midrank percentile for non-negative evidence values."""

    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    output = np.full(len(frame), np.nan, dtype=float)
    keys = ["symbol", "slot_key"]
    for positions in frame.groupby(keys, sort=False, dropna=False).indices.values():
        ordered = np.asarray(positions, dtype=int)
        for offset, position in enumerate(ordered):
            current = numeric[position]
            if not np.isfinite(current):
                continue
            history_positions = ordered[max(0, offset - window) : offset]
            history = numeric[history_positions]
            history = history[np.isfinite(history)]
            if len(history) < min_history:
                continue
            if current == 0.0 and np.all(history >= 0.0):
                output[position] = 0.0
                continue
            less = np.count_nonzero(history < current)
            equal = np.count_nonzero(history == current)
            output[position] = (less + 0.5 * equal) / len(history)
    return pd.Series(output, index=frame.index, dtype="float64")


def _sequence_groups(frame: pd.DataFrame, bar_minutes: int) -> list[np.ndarray]:
    """Return continuous morning/afternoon position groups without crossing timestamp gaps."""

    minutes = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    session_part = pd.Series(np.where(minutes <= 11 * 60 + 30, "am", "pm"), index=frame.index)
    dates = frame.get("session_date_key", frame["timestamp"].dt.strftime("%Y%m%d").astype("int64"))
    working = pd.DataFrame(
        {
            "symbol": frame["symbol"],
            "date": dates,
            "part": session_part,
            "timestamp": frame["timestamp"],
        },
        index=frame.index,
    )
    expected = pd.to_timedelta(bar_minutes, unit="min")
    sequences: list[np.ndarray] = []
    for positions in working.groupby(["symbol", "date", "part"], sort=False).indices.values():
        ordered = np.asarray(positions, dtype=int)
        timestamps = working.iloc[ordered]["timestamp"]
        breaks = timestamps.diff().ne(expected).to_numpy()
        breaks[0] = False
        start = 0
        for boundary in np.flatnonzero(breaks):
            sequences.append(ordered[start:boundary])
            start = int(boundary)
        sequences.append(ordered[start:])
    return sequences


def _persistent_delta_ratio(
    frame: pd.DataFrame,
    window: int,
    bar_minutes: int,
    valid: pd.Series,
) -> pd.Series:
    delta = pd.to_numeric(frame["delta_ratio"], errors="coerce") * pd.to_numeric(
        frame["total_transaction_volume"], errors="coerce"
    )
    delta = delta.where(valid)
    volume = pd.to_numeric(frame["total_transaction_volume"], errors="coerce").where(
        valid & delta.notna()
    )
    result = pd.Series(np.nan, index=frame.index, dtype="float64")
    for positions in _sequence_groups(frame, bar_minutes):
        labels = frame.index[positions]
        numerator = delta.iloc[positions].rolling(window, min_periods=1).sum()
        denominator = volume.iloc[positions].rolling(window, min_periods=1).sum()
        result.loc[labels] = _safe_ratio(numerator, denominator).to_numpy()
    return result


def _confirmed_side(
    frame: pd.DataFrame,
    strong_directional: pd.Series,
    side: pd.Series,
    *,
    length: int,
    bar_minutes: int,
) -> pd.Series:
    result = pd.Series(False, index=frame.index)
    for positions in _sequence_groups(frame, bar_minutes):
        labels = frame.index[positions]
        for direction in (-1, 1):
            matches = strong_directional.iloc[positions] & side.iloc[positions].eq(direction)
            confirmed = matches.astype(int).rolling(length, min_periods=length).sum().eq(length)
            result.loc[labels] |= confirmed.to_numpy()
    return result.astype(bool)


def _score_stability(frame: pd.DataFrame, score: pd.Series, bar_minutes: int) -> pd.Series:
    stability = pd.Series(0.5, index=frame.index, dtype="float64")
    for positions in _sequence_groups(frame, bar_minutes):
        labels = frame.index[positions]
        current = score.iloc[positions]
        previous = current.shift(1)
        values = (1.0 - (current - previous).abs() / 100.0).clip(0.0, 1.0).fillna(0.5)
        stability.loc[labels] = values.to_numpy()
    return stability


def participation_score_from_components(frame: pd.DataFrame) -> pd.Series:
    """Combine four bounded evidence components with explicit equal weights."""

    missing = sorted(set(PARTICIPATION_COMPONENT_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError("participation factor input is missing columns: " + ", ".join(missing))
    components = frame.loc[:, PARTICIPATION_COMPONENT_COLUMNS].apply(pd.to_numeric, errors="coerce")
    finite = np.isfinite(components.to_numpy())
    out_of_range = finite & ((components.to_numpy() < 0.0) | (components.to_numpy() > 1.0))
    if out_of_range.any():
        raise ValueError("participation components must be between 0 and 1")
    result = components.sum(axis=1, min_count=len(PARTICIPATION_COMPONENT_COLUMNS)) * 25.0
    result = result.clip(0.0, 100.0).astype("float64")
    result.name = PARTICIPATION_FACTOR_NAME
    return result


def compute_participation_features(
    frame: pd.DataFrame,
    config: OrderFlowConfig | None = None,
) -> pd.DataFrame:
    """Compute a causal participation score, direction, state, confirmation, and confidence."""

    settings = config or OrderFlowConfig()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("participation input must be a pandas DataFrame")
    required = {
        "timestamp",
        "slot_key",
        "total_transaction_volume",
        "transaction_amount",
        "trade_count",
        "large_trade_volume",
        "large_trade_share",
        "delta_ratio",
        "large_delta_ratio",
        "bar_return",
        "clv",
        "vwap_distance",
        "transaction_coverage",
        "baseline_observations",
        "of_data_valid",
        "of_history_ready",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("participation input is missing columns: " + ", ".join(missing))

    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("participation input contains invalid timestamps")
    if "symbol" not in data.columns:
        data["symbol"] = "__single__"
    data["symbol"] = data["symbol"].astype("string").str.strip()
    if data["symbol"].isna().any() or data["symbol"].eq("").any():
        raise ValueError("participation input contains an empty symbol")
    data = data.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
    if data.duplicated(["symbol", "timestamp"]).any():
        raise ValueError("participation input contains duplicate symbol/timestamp rows")

    numeric_columns = required.difference({"timestamp", "of_data_valid", "of_history_ready"})
    for column in numeric_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["average_trade_amount"] = _safe_ratio(data["transaction_amount"], data["trade_count"])
    valid = _boolean_series(data["of_data_valid"], "of_data_valid")

    large_delta = data["large_delta_ratio"].copy()
    no_large_print = data["large_trade_volume"].eq(0.0) & data["total_transaction_volume"].gt(0.0)
    large_delta = large_delta.where(~no_large_print, 0.0)
    data["participation_persistent_delta_ratio"] = _persistent_delta_ratio(
        data,
        window=3,
        bar_minutes=settings.bar_minutes,
        valid=valid,
    )

    window = max(settings.volume_baseline_sessions, settings.min_history_sessions)
    percentile_inputs = {
        "participation_transaction_volume_percentile": data["total_transaction_volume"].where(
            valid
        ),
        "participation_trade_count_percentile": data["trade_count"].where(valid),
        "participation_average_trade_amount_percentile": data["average_trade_amount"].where(valid),
        "participation_large_trade_share_percentile": data["large_trade_share"].where(valid),
        "participation_delta_magnitude_percentile": data["delta_ratio"].abs().where(valid),
        "participation_large_delta_magnitude_percentile": large_delta.abs().where(valid),
        "participation_return_magnitude_percentile": data["bar_return"].abs().where(valid),
        "participation_vwap_distance_percentile": data["vwap_distance"].abs().where(valid),
    }
    for name, values in percentile_inputs.items():
        data[name] = _prior_percentile(
            data,
            values,
            window=window,
            min_history=settings.min_history_sessions,
        )

    data["participation_activity"] = data[
        [
            "participation_transaction_volume_percentile",
            "participation_trade_count_percentile",
        ]
    ].mean(axis=1, skipna=False)
    data["participation_size"] = data[
        [
            "participation_average_trade_amount_percentile",
            "participation_large_trade_share_percentile",
        ]
    ].mean(axis=1, skipna=False)
    data["participation_imbalance"] = data[
        [
            "participation_delta_magnitude_percentile",
            "participation_large_delta_magnitude_percentile",
        ]
    ].mean(axis=1, skipna=False)

    flow_inputs = pd.concat(
        [data["delta_ratio"], large_delta, data["participation_persistent_delta_ratio"]], axis=1
    )
    flow_direction = flow_inputs.mean(axis=1, skipna=True).clip(-1.0, 1.0)
    signed_return = np.sign(data["bar_return"]) * data["participation_return_magnitude_percentile"]
    signed_vwap = np.sign(data["vwap_distance"]) * data["participation_vwap_distance_percentile"]
    price_inputs = pd.concat([signed_return, signed_vwap, data["clv"].clip(-1.0, 1.0)], axis=1)
    price_direction = price_inputs.mean(axis=1, skipna=True).clip(-1.0, 1.0)
    data["participation_aggressor_direction_score"] = flow_direction * 100.0
    data["participation_price_direction_score"] = price_direction * 100.0
    data["participation_flow_price_alignment"] = flow_direction * price_direction

    raw_price_direction = pd.concat(
        [np.sign(data["bar_return"]), np.sign(data["vwap_distance"]), data["clv"].clip(-1.0, 1.0)],
        axis=1,
    ).mean(axis=1, skipna=True)
    control_raw = (flow_direction * raw_price_direction).abs()
    bullish_absorption = _boolean_series(
        data.get("bullish_absorption", pd.Series(False, index=data.index)),
        "bullish_absorption",
    )
    bearish_absorption = _boolean_series(
        data.get("bearish_absorption", pd.Series(False, index=data.index)),
        "bearish_absorption",
    )
    absorption = bullish_absorption | bearish_absorption
    control_raw = pd.concat([control_raw, flow_direction.abs().where(absorption, 0.0)], axis=1).max(
        axis=1, skipna=True
    )
    data["participation_control_raw"] = control_raw
    data["participation_control"] = _prior_percentile(
        data,
        control_raw.where(valid),
        window=window,
        min_history=settings.min_history_sessions,
    )

    history_ready = _boolean_series(data["of_history_ready"], "of_history_ready")
    components_complete = data.loc[:, PARTICIPATION_COMPONENT_COLUMNS].notna().all(axis=1)
    data["participation_eligible"] = valid & history_ready & components_complete
    score = participation_score_from_components(data).where(data["participation_eligible"])
    data[PARTICIPATION_FACTOR_NAME] = score
    data["participation_strong_evidence"] = data["participation_eligible"] & score.ge(
        settings.participation_strong_threshold
    )

    inferred_direction = data["participation_aggressor_direction_score"].copy()
    inferred_direction = inferred_direction.where(
        ~bullish_absorption, inferred_direction.abs()
    ).where(~bearish_absorption, -inferred_direction.abs())
    inferred_direction = inferred_direction.where(data["participation_eligible"])
    data["participation_direction_score"] = inferred_direction

    threshold = settings.participation_direction_threshold
    strong = data["participation_strong_evidence"]
    active_buy = (
        strong
        & data["participation_aggressor_direction_score"].ge(threshold)
        & data["participation_price_direction_score"].ge(0.0)
    )
    active_sell = (
        strong
        & data["participation_aggressor_direction_score"].le(-threshold)
        & data["participation_price_direction_score"].le(0.0)
    )
    data["participation_state"] = np.select(
        [
            ~data["participation_eligible"],
            ~strong,
            strong & bullish_absorption,
            strong & bearish_absorption,
            active_buy,
            active_sell,
        ],
        [
            "unavailable",
            "no_clear_evidence",
            "passive_buy_absorption",
            "passive_sell_distribution",
            "active_buy",
            "active_sell",
        ],
        default="conflicting_evidence",
    )
    directional_states = data["participation_state"].isin(
        {"active_buy", "active_sell", "passive_buy_absorption", "passive_sell_distribution"}
    )
    side = pd.Series(
        np.select(
            [inferred_direction.ge(threshold), inferred_direction.le(-threshold)],
            [1, -1],
            default=0,
        ),
        index=data.index,
        dtype="int8",
    )
    data["participation_side"] = side
    data["participation_confirmed"] = _confirmed_side(
        data,
        strong & directional_states,
        side,
        length=settings.participation_confirmation_bars,
        bar_minutes=settings.bar_minutes,
    )

    coverage = data["transaction_coverage"]
    coverage_quality = (1.0 - (coverage - 1.0).abs()).clip(0.0, 1.0)
    history_count = pd.to_numeric(data.get("baseline_observations"), errors="coerce")
    history_quality = (history_count / float(window)).clip(0.0, 1.0)
    component_quality = data.loc[:, PARTICIPATION_COMPONENT_COLUMNS].notna().mean(axis=1)
    stability = _score_stability(data, score, settings.bar_minutes)
    confidence = pd.concat(
        [coverage_quality, history_quality, component_quality, stability], axis=1
    ).mean(axis=1, skipna=False)
    data["participation_confidence"] = (confidence * 100.0).clip(0.0, 100.0).where(valid)
    data["participation_provisional"] = _boolean_series(
        data.get("is_incomplete_session", pd.Series(False, index=data.index)),
        "is_incomplete_session",
    )
    data["participation_factor_version"] = PARTICIPATION_FACTOR_VERSION
    data.attrs["participation_factor_name"] = PARTICIPATION_FACTOR_NAME
    data.attrs["participation_factor_version"] = PARTICIPATION_FACTOR_VERSION
    data.attrs["participation_daily_quantile"] = PARTICIPATION_DAILY_QUANTILE
    return data


def summarize_participation_sessions(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate bar evidence into a daily P90 factor and auditable state diagnostics."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("participation summary input must be a pandas DataFrame")
    required = {"timestamp", "symbol", PARTICIPATION_FACTOR_NAME}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("participation summary input is missing columns: " + ", ".join(missing))
    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("participation summary input contains invalid timestamps")
    data[PARTICIPATION_FACTOR_NAME] = pd.to_numeric(
        data[PARTICIPATION_FACTOR_NAME], errors="coerce"
    )
    data["date"] = data["timestamp"].dt.strftime("%Y%m%d").astype("int64")

    rows: list[dict[str, Any]] = []
    for (date_value, symbol), group in data.groupby(["date", "symbol"], sort=True, dropna=False):
        group = group.sort_values("timestamp", kind="mergesort")
        valid = group.loc[group[PARTICIPATION_FACTOR_NAME].notna()].copy()
        try:
            code, qualified = parse_symbol(str(symbol))[1:]
        except (TypeError, ValueError):
            code, qualified = str(symbol), str(symbol)
        row: dict[str, Any] = {
            "date": int(date_value),
            "code": code,
            "symbol": qualified,
            "datetime": group["timestamp"].max(),
            "valid_bars": len(valid),
            "total_bars": len(group),
            "participation_provisional": bool(
                _boolean_series(
                    group.get("participation_provisional", pd.Series(False, index=group.index)),
                    "participation_provisional",
                ).any()
            ),
        }
        if valid.empty:
            row.update(
                {
                    PARTICIPATION_FACTOR_NAME: np.nan,
                    "participation_latest_score": np.nan,
                    "participation_latest_direction_score": np.nan,
                    "participation_latest_state": "unavailable",
                    "participation_latest_confirmed": False,
                    "participation_latest_confidence": np.nan,
                    "participation_mean_score": np.nan,
                    "participation_peak_score": np.nan,
                    "participation_direction_score": np.nan,
                    "participation_dominant_state": "unavailable",
                    "participation_strong_bar_share": np.nan,
                    "participation_confirmed_bar_share": np.nan,
                    "participation_confirmed_buy_bar_share": np.nan,
                    "participation_confirmed_sell_bar_share": np.nan,
                    "participation_confirmed_direction": "none",
                    "participation_confidence": np.nan,
                }
            )
            rows.append(row)
            continue

        scores = valid[PARTICIPATION_FACTOR_NAME]
        direction = pd.to_numeric(
            valid.get("participation_direction_score", pd.Series(np.nan, index=valid.index)),
            errors="coerce",
        )
        directional = direction.notna()
        if directional.any() and scores.loc[directional].sum() > 0.0:
            daily_direction = float(
                np.average(direction.loc[directional], weights=scores.loc[directional])
            )
        else:
            daily_direction = float(direction.mean()) if directional.any() else np.nan
        states = valid.get(
            "participation_state", pd.Series("no_clear_evidence", index=valid.index)
        ).astype("string")
        strong = _boolean_series(
            valid.get("participation_strong_evidence", pd.Series(False, index=valid.index)),
            "participation_strong_evidence",
        )
        state_weights = scores.loc[strong].groupby(states.loc[strong], sort=False).sum()
        dominant_state = (
            str(state_weights.idxmax()) if not state_weights.empty else "no_clear_evidence"
        )
        confirmed = _boolean_series(
            valid.get("participation_confirmed", pd.Series(False, index=valid.index)),
            "participation_confirmed",
        )
        side = pd.to_numeric(
            valid.get("participation_side", pd.Series(0, index=valid.index)), errors="coerce"
        ).fillna(0.0)
        confirmed_buy = confirmed & side.eq(1.0)
        confirmed_sell = confirmed & side.eq(-1.0)
        if confirmed_buy.any() and confirmed_sell.any():
            confirmed_direction = "mixed"
        elif confirmed_buy.any():
            confirmed_direction = "buy"
        elif confirmed_sell.any():
            confirmed_direction = "sell"
        else:
            confirmed_direction = "none"
        confidence = pd.to_numeric(
            valid.get("participation_confidence", pd.Series(np.nan, index=valid.index)),
            errors="coerce",
        )
        latest_state = states.iloc[-1]
        latest_direction = direction.iloc[-1]
        latest_confidence = confidence.iloc[-1]
        row.update(
            {
                PARTICIPATION_FACTOR_NAME: float(scores.quantile(PARTICIPATION_DAILY_QUANTILE)),
                "participation_latest_score": float(scores.iloc[-1]),
                "participation_latest_direction_score": (
                    float(latest_direction) if pd.notna(latest_direction) else np.nan
                ),
                "participation_latest_state": (
                    str(latest_state) if pd.notna(latest_state) else "unavailable"
                ),
                "participation_latest_confirmed": bool(confirmed.iloc[-1]),
                "participation_latest_confidence": (
                    float(latest_confidence) if pd.notna(latest_confidence) else np.nan
                ),
                "participation_mean_score": float(scores.mean()),
                "participation_peak_score": float(scores.max()),
                "participation_direction_score": daily_direction,
                "participation_dominant_state": dominant_state,
                "participation_strong_bar_share": float(strong.mean()),
                "participation_confirmed_bar_share": float(confirmed.mean()),
                "participation_confirmed_buy_bar_share": float(confirmed_buy.mean()),
                "participation_confirmed_sell_bar_share": float(confirmed_sell.mean()),
                "participation_confirmed_direction": confirmed_direction,
                "participation_confidence": float(confidence.median())
                if confidence.notna().any()
                else np.nan,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


__all__ = [
    "PARTICIPATION_COMPONENT_COLUMNS",
    "PARTICIPATION_DAILY_QUANTILE",
    "PARTICIPATION_FACTOR_NAME",
    "PARTICIPATION_FACTOR_VERSION",
    "compute_participation_features",
    "participation_score_from_components",
    "summarize_participation_sessions",
]
