"""Causal daily volume-spread analysis (VSA) features.

The feature layer is deliberately independent of AKQuant.  It accepts normalized OHLCV data,
keeps missing source values visible, and computes rolling reference values from *previous* bars
only.  This makes the resulting frame suitable for both offline inspection and pre-computation
before an AKQuant run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

VSA_FEATURE_VERSION = "vsa-daily-1"

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "datetime", "date", "time", "日期", "时间"),
    "open": ("open", "开盘", "开盘价"),
    "high": ("high", "最高", "最高价"),
    "low": ("low", "最低", "最低价"),
    "close": ("close", "收盘", "收盘价"),
    "volume": ("volume", "成交量", "vol"),
    "symbol": ("symbol", "股票代码", "代码", "ticker"),
}


@dataclass(frozen=True, slots=True)
class VSAConfig:
    """Versioned, intentionally small set of VSA parameters.

    The defaults are conventional starting values, not fitted values.  A report should persist
    :meth:`as_dict` so a later run can be reproduced.  ``min_periods`` may be lowered explicitly
    for a small synthetic fixture; production research should normally leave it at ``None``.
    """

    volume_window: int = 20
    spread_window: int = 20
    trend_window: int = 20
    context_window: int = 20
    min_periods: int | None = None
    low_volume_ratio: float = 0.80
    high_volume_ratio: float = 1.50
    narrow_spread_ratio: float = 0.80
    wide_spread_ratio: float = 1.50
    close_high_threshold: float = 0.70
    close_low_threshold: float = 0.30
    near_high_threshold: float = 0.75
    near_low_threshold: float = 0.25
    confirmation_move_pct: float = 0.0
    stop_buffer_pct: float = 0.003
    risk_reward: float = 2.0

    def __post_init__(self) -> None:
        for name in (
            "volume_window",
            "spread_window",
            "trend_window",
            "context_window",
        ):
            value = int(getattr(self, name))
            if value < 2:
                raise ValueError(f"{name} must be at least 2")
            object.__setattr__(self, name, value)

        if self.min_periods is not None:
            min_periods = int(self.min_periods)
            if min_periods < 2:
                raise ValueError("min_periods must be at least 2 when provided")
            smallest_window = min(
                self.volume_window,
                self.spread_window,
                self.trend_window,
                self.context_window,
            )
            if min_periods > smallest_window:
                raise ValueError(
                    f"min_periods must not exceed the smallest rolling window ({smallest_window})"
                )
            object.__setattr__(self, "min_periods", min_periods)

        bounded = {
            "close_high_threshold": self.close_high_threshold,
            "close_low_threshold": self.close_low_threshold,
            "near_high_threshold": self.near_high_threshold,
            "near_low_threshold": self.near_low_threshold,
        }
        for name, raw_value in bounded.items():
            value = float(raw_value)
            if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value)

        positive = {
            "low_volume_ratio": self.low_volume_ratio,
            "high_volume_ratio": self.high_volume_ratio,
            "narrow_spread_ratio": self.narrow_spread_ratio,
            "wide_spread_ratio": self.wide_spread_ratio,
            "risk_reward": self.risk_reward,
        }
        for name, raw_value in positive.items():
            value = float(raw_value)
            if not np.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
            object.__setattr__(self, name, value)

        for name in ("confirmation_move_pct", "stop_buffer_pct"):
            value = float(getattr(self, name))
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)

        if self.low_volume_ratio >= self.high_volume_ratio:
            raise ValueError("low_volume_ratio must be below high_volume_ratio")
        if self.narrow_spread_ratio >= self.wide_spread_ratio:
            raise ValueError("narrow_spread_ratio must be below wide_spread_ratio")
        if self.close_low_threshold >= self.close_high_threshold:
            raise ValueError("close_low_threshold must be below close_high_threshold")
        if self.near_low_threshold >= self.near_high_threshold:
            raise ValueError("near_low_threshold must be below near_high_threshold")

    @property
    def effective_min_periods(self) -> int:
        """Return the minimum observations used by each rolling baseline."""

        if self.min_periods is not None:
            return self.min_periods
        return max(
            self.volume_window,
            self.spread_window,
            self.trend_window,
            self.context_window,
        )

    def min_periods_for(self, window: int) -> int:
        """Return the explicit minimum or the natural full length for one window."""

        return self.min_periods if self.min_periods is not None else int(window)

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-friendly parameters, including the feature-version marker."""

        values = asdict(self)
        values["feature_version"] = VSA_FEATURE_VERSION
        values["effective_min_periods"] = self.effective_min_periods
        return values


# A descriptive alias keeps imports readable for callers that distinguish feature configuration.
VSAFeatureConfig = VSAConfig


def _resolve_column(frame: pd.DataFrame, field: str) -> str | None:
    """Resolve one canonical field without guessing between similarly named columns."""

    columns = list(frame.columns)
    lower_to_column = {str(column).casefold(): column for column in columns}
    for candidate in _COLUMN_ALIASES[field]:
        if candidate in frame.columns:
            return str(candidate)
        resolved = lower_to_column.get(candidate.casefold())
        if resolved is not None:
            return str(resolved)
    return None


def _rolling_prior(
    frame: pd.DataFrame,
    column: str,
    window: int,
    min_periods: int,
    method: Literal["median", "mean", "max", "min"],
) -> pd.Series:
    """Calculate a per-symbol rolling statistic with the current bar excluded."""

    grouped = frame.groupby("_vsa_symbol", sort=False, dropna=False)[column]

    def calculate(values: pd.Series) -> pd.Series:
        rolling = values.shift(1).rolling(window=window, min_periods=min_periods)
        return getattr(rolling, method)()

    return grouped.transform(calculate)


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Divide while retaining undefined/zero-denominator observations as missing."""

    denominator = denominator.where(denominator > 0.0)
    result = numerator / denominator
    return result.replace([np.inf, -np.inf], np.nan)


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")

    data = frame.copy()
    resolved: dict[str, str] = {}
    for field in ("timestamp", "open", "high", "low", "close", "volume"):
        column = _resolve_column(data, field)
        if column is None:
            raise ValueError(f"VSA input is missing required column: {field}")
        resolved[field] = column

    if resolved["timestamp"] != "timestamp":
        data["timestamp"] = data[resolved["timestamp"]]
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("VSA input contains invalid timestamps")

    for field in ("open", "high", "low", "close", "volume"):
        if resolved[field] != field:
            data[field] = data[resolved[field]]
        data[field] = pd.to_numeric(data[field], errors="coerce")

    symbol_column = _resolve_column(data, "symbol")
    if symbol_column is None:
        data["_vsa_symbol"] = "__single__"
    else:
        symbols = data[symbol_column].astype("string").str.strip()
        if symbols.isna().any() or symbols.eq("").any():
            raise ValueError("VSA input contains an empty symbol")
        # Numeric A-share codes are normalized here, while string codes retain leading zeroes.
        data["_vsa_symbol"] = symbols.map(
            lambda value: value.zfill(6) if value.isdigit() and len(value) <= 6 else value
        )

    data = data.sort_values(["_vsa_symbol", "timestamp"], kind="mergesort")
    duplicates = data.duplicated(["_vsa_symbol", "timestamp"], keep=False)
    if duplicates.any():
        duplicate_rows = data.loc[duplicates, ["_vsa_symbol", "timestamp"]].head(3)
        raise ValueError(
            f"VSA input contains duplicate symbol/timestamp rows: {duplicate_rows.to_dict('records')}"
        )
    return data.reset_index(drop=True)


def compute_vsa_features(
    frame: pd.DataFrame,
    config: VSAConfig | None = None,
) -> pd.DataFrame:
    """Compute daily VSA features for one or more symbols.

    Rolling baselines use ``shift(1)`` before every window operation.  Consequently changing a
    future bar cannot change an earlier feature.  Invalid or missing source values are retained;
    ``vsa_data_valid`` and ``vsa_invalid_reason`` identify rows that rules must skip.
    """

    settings = config or VSAConfig()
    data = _prepare_frame(frame)
    if data.empty:
        data["vsa_feature_version"] = VSA_FEATURE_VERSION
        return data.drop(columns=["_vsa_symbol"])

    required = data[["open", "high", "low", "close", "volume"]]
    finite = np.isfinite(required.to_numpy(dtype=float)).all(axis=1)
    order_valid = (
        (data["high"] >= data[["open", "close"]].max(axis=1))
        & (data["low"] <= data[["open", "close"]].min(axis=1))
        & (data["high"] >= data["low"])
    )
    volume_valid = data["volume"] >= 0.0
    data["vsa_data_valid"] = finite & order_valid.to_numpy() & volume_valid.to_numpy()
    data["vsa_zero_volume"] = data["volume"].eq(0.0)

    missing = required.isna().any(axis=1)
    non_finite = (~np.isfinite(required.to_numpy(dtype=float)).all(axis=1)) & ~missing
    bad_order = (~order_valid) & ~missing & ~non_finite
    bad_volume = (~volume_valid) & ~missing & ~non_finite
    data["vsa_invalid_reason"] = np.select(
        [missing, non_finite, bad_order, bad_volume],
        ["missing_required", "non_finite_required", "invalid_ohlc_order", "negative_volume"],
        default="",
    )

    data["vsa_spread"] = data["high"] - data["low"]
    data["vsa_body"] = (data["close"] - data["open"]).abs()
    data["vsa_upper_wick"] = data["high"] - data[["open", "close"]].max(axis=1)
    data["vsa_lower_wick"] = data[["open", "close"]].min(axis=1) - data["low"]
    data["vsa_close_location"] = _safe_ratio(data["close"] - data["low"], data["vsa_spread"])
    data["vsa_clv"] = _safe_ratio(
        2.0 * data["close"] - data["high"] - data["low"], data["vsa_spread"]
    )
    data["vsa_body_ratio"] = _safe_ratio(data["vsa_body"], data["vsa_spread"])
    data["vsa_upper_wick_ratio"] = _safe_ratio(data["vsa_upper_wick"], data["vsa_spread"])
    data["vsa_lower_wick_ratio"] = _safe_ratio(data["vsa_lower_wick"], data["vsa_spread"])

    data["vsa_volume_baseline"] = _rolling_prior(
        data,
        "volume",
        settings.volume_window,
        settings.min_periods_for(settings.volume_window),
        "median",
    )
    data["vsa_spread_baseline"] = _rolling_prior(
        data,
        "vsa_spread",
        settings.spread_window,
        settings.min_periods_for(settings.spread_window),
        "median",
    )
    data["vsa_volume_ratio"] = _safe_ratio(data["volume"], data["vsa_volume_baseline"])
    data["vsa_spread_ratio"] = _safe_ratio(data["vsa_spread"], data["vsa_spread_baseline"])

    data["vsa_prior_high"] = _rolling_prior(
        data,
        "high",
        settings.context_window,
        settings.min_periods_for(settings.context_window),
        "max",
    )
    data["vsa_prior_low"] = _rolling_prior(
        data,
        "low",
        settings.context_window,
        settings.min_periods_for(settings.context_window),
        "min",
    )
    data["vsa_range_position"] = _safe_ratio(
        data["close"] - data["vsa_prior_low"],
        data["vsa_prior_high"] - data["vsa_prior_low"],
    )

    prior_close = data.groupby("_vsa_symbol", sort=False, dropna=False)["close"].shift(1)
    trend_anchor = _rolling_prior(
        data,
        "close",
        settings.trend_window,
        settings.min_periods_for(settings.trend_window),
        "mean",
    )
    data["vsa_prior_close"] = prior_close
    data["vsa_trend_return"] = _safe_ratio(prior_close - trend_anchor, trend_anchor)
    data["vsa_trend_direction"] = np.sign(data["vsa_trend_return"])
    data["vsa_bar_return"] = _safe_ratio(data["close"] - prior_close, prior_close)
    data["vsa_gap_return"] = _safe_ratio(data["open"] - prior_close, prior_close)
    data["vsa_history_ready"] = (
        data["vsa_volume_baseline"].notna()
        & data["vsa_spread_baseline"].notna()
        & data["vsa_prior_high"].notna()
        & data["vsa_prior_low"].notna()
        & data["vsa_trend_return"].notna()
    )

    data["vsa_feature_version"] = VSA_FEATURE_VERSION
    return data.drop(columns=["_vsa_symbol"])


def feature_columns() -> tuple[str, ...]:
    """Return the stable numeric feature names useful for an AKQuant extra-field frame."""

    return (
        "vsa_spread",
        "vsa_body",
        "vsa_upper_wick",
        "vsa_lower_wick",
        "vsa_close_location",
        "vsa_clv",
        "vsa_body_ratio",
        "vsa_upper_wick_ratio",
        "vsa_lower_wick_ratio",
        "vsa_volume_baseline",
        "vsa_spread_baseline",
        "vsa_volume_ratio",
        "vsa_spread_ratio",
        "vsa_prior_high",
        "vsa_prior_low",
        "vsa_range_position",
        "vsa_prior_close",
        "vsa_trend_return",
        "vsa_trend_direction",
        "vsa_bar_return",
        "vsa_gap_return",
        "vsa_data_valid",
        "vsa_zero_volume",
        "vsa_history_ready",
    )


__all__ = [
    "VSA_FEATURE_VERSION",
    "VSAConfig",
    "VSAFeatureConfig",
    "compute_vsa_features",
    "feature_columns",
]
