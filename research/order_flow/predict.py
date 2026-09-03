"""Causal, walk-forward prediction experiments for order-flow features.

The order-flow factor is an observation available at a bar close.  This module turns that
observation into an explicitly indexed future-return target, performs a small event study, and
fits a regularized linear model in chronological windows.  It intentionally uses only NumPy and
Pandas so prediction research does not add a runtime machine-learning dependency to the project.

The model output is an expected-return estimate, not a trading instruction.  Callers must still
apply an execution model, fees, A-share lot rules, and the existing T+1-aware strategy.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from .backtest import OrderFlowBacktestResult
from .config import OrderFlowConfig

PREDICTION_VERSION = "order-flow-prediction-1"
PredictionTarget = Literal["same_segment_open"]

DEFAULT_PREDICTION_FEATURES = (
    "order_flow_delta_ratio",
    "delta_ratio_zscore",
    "relative_transaction_volume",
    "clv",
    "bar_return",
    "vwap_distance",
    "flow_price_divergence",
)
DEFAULT_FACTOR_LAGS = (1, 2, 3, 5)


def _boolean_series(values: pd.Series, *, name: str) -> pd.Series:
    """Parse a CSV-safe boolean column without treating non-empty strings as true."""

    text = values.astype("string").str.strip().str.casefold()
    numeric = pd.to_numeric(values, errors="coerce")
    missing = values.isna() | text.eq("")
    truthy = text.isin({"true", "t"}) | numeric.eq(1.0).fillna(False)
    falsy = text.isin({"false", "f"}) | numeric.eq(0.0).fillna(False)
    invalid = ~(missing | truthy | falsy)
    if invalid.any():
        examples = values.loc[invalid].astype("string").drop_duplicates().head(3).tolist()
        raise ValueError(
            f"prediction input column {name} contains invalid boolean values: {examples}"
        )
    result = pd.Series(False, index=values.index, dtype=bool)
    result.loc[truthy] = True
    return result


@dataclass(frozen=True, slots=True)
class OrderFlowPredictionConfig:
    """Configuration for target construction and chronological model evaluation."""

    bar_minutes: int = 5
    horizon_bars: int = 3
    train_sessions: int = 60
    validation_sessions: int = 0
    test_sessions: int = 5
    min_train_rows: int = 100
    ridge_alpha: float = 10.0
    round_trip_cost: float = 0.0
    edge_buffer: float = 0.0
    prediction_threshold: float | None = None
    threshold_grid: tuple[float, ...] = ()
    min_validation_signals: int = 1
    factor_column: str = "order_flow_delta_ratio"
    factor_lags: tuple[int, ...] = DEFAULT_FACTOR_LAGS
    feature_columns: tuple[str, ...] = DEFAULT_PREDICTION_FEATURES
    require_history_ready: bool = True
    target: PredictionTarget = "same_segment_open"

    def __post_init__(self) -> None:
        if (
            isinstance(self.bar_minutes, bool)
            or not isinstance(self.bar_minutes, int)
            or self.bar_minutes not in {1, 5, 15, 30, 60}
        ):
            raise ValueError("bar_minutes must be one of 1, 5, 15, 30, or 60")
        for name in ("horizon_bars", "train_sessions", "test_sessions", "min_train_rows"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.validation_sessions, bool) or not isinstance(
            self.validation_sessions, int
        ):
            raise TypeError("validation_sessions must be a non-negative integer")
        if self.validation_sessions < 0:
            raise ValueError("validation_sessions must be a non-negative integer")
        for name in ("ridge_alpha", "round_trip_cost", "edge_buffer"):
            value = float(getattr(self, name))
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        if self.prediction_threshold is not None:
            value = float(self.prediction_threshold)
            if not np.isfinite(value):
                raise ValueError("prediction_threshold must be finite or None")
            object.__setattr__(self, "prediction_threshold", value)
        thresholds = tuple(float(value) for value in self.threshold_grid)
        if not all(np.isfinite(value) for value in thresholds):
            raise ValueError("threshold_grid must contain only finite values")
        object.__setattr__(self, "threshold_grid", thresholds)
        if (
            isinstance(self.min_validation_signals, bool)
            or not isinstance(self.min_validation_signals, int)
            or self.min_validation_signals < 1
        ):
            raise ValueError("min_validation_signals must be a positive integer")
        if not isinstance(self.factor_column, str) or not self.factor_column.strip():
            raise ValueError("factor_column must be a non-empty string")
        if not isinstance(self.require_history_ready, bool):
            raise TypeError("require_history_ready must be a boolean")
        if self.target != "same_segment_open":
            raise ValueError("target must be same_segment_open")
        lags = tuple(sorted(set(self.factor_lags)))
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in lags
        ):
            raise ValueError("factor_lags must contain positive integers")
        object.__setattr__(self, "factor_lags", lags)
        columns = tuple(str(value).strip() for value in self.feature_columns)
        if not columns or any(not value for value in columns):
            raise ValueError("feature_columns must contain non-empty names")
        object.__setattr__(self, "feature_columns", tuple(dict.fromkeys(columns)))

    @property
    def signal_threshold(self) -> float:
        """Return the configured minimum expected return for a long signal."""

        if self.prediction_threshold is not None:
            return self.prediction_threshold
        return self.round_trip_cost + self.edge_buffer

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable configuration snapshot."""

        return {
            "bar_minutes": self.bar_minutes,
            "horizon_bars": self.horizon_bars,
            "train_sessions": self.train_sessions,
            "validation_sessions": self.validation_sessions,
            "test_sessions": self.test_sessions,
            "min_train_rows": self.min_train_rows,
            "ridge_alpha": self.ridge_alpha,
            "round_trip_cost": self.round_trip_cost,
            "edge_buffer": self.edge_buffer,
            "prediction_threshold": self.prediction_threshold,
            "signal_threshold": self.signal_threshold,
            "threshold_grid": list(self.threshold_grid),
            "min_validation_signals": self.min_validation_signals,
            "factor_column": self.factor_column,
            "factor_lags": list(self.factor_lags),
            "feature_columns": list(self.feature_columns),
            "require_history_ready": self.require_history_ready,
            "target": self.target,
        }

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        *,
        base: OrderFlowPredictionConfig | None = None,
    ) -> OrderFlowPredictionConfig:
        """Build a prediction config from a JSON-like mapping."""

        if not isinstance(values, Mapping):
            raise TypeError("prediction config must be a mapping")
        raw = dict(values)
        nested = raw.pop("parameters", None)
        if nested is not None:
            if not isinstance(nested, Mapping):
                raise TypeError("config.parameters must be a mapping")
            nested_values = dict(nested)
            nested_values.update(raw)
            raw = nested_values
        raw.pop("prediction_version", None)
        raw.pop("schema_version", None)
        # This is a derived property in reports, not a constructor argument.
        raw.pop("signal_threshold", None)
        valid = {field.name for field in fields(cls)}
        unknown = sorted(set(raw).difference(valid))
        if unknown:
            raise ValueError("unknown prediction config parameter(s): " + ", ".join(unknown))
        if base is None:
            return cls(**raw)
        merged = asdict(base)
        merged.update(raw)
        return cls(**merged)

    @classmethod
    def from_json(
        cls,
        path: str | Path,
        *,
        base: OrderFlowPredictionConfig | None = None,
    ) -> OrderFlowPredictionConfig:
        """Load a UTF-8 JSON prediction configuration."""

        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read prediction config {source}: {exc}") from exc
        return cls.from_mapping(payload, base=base)


def _session_part(timestamp: pd.Series, is_session_bar: pd.Series | None = None) -> pd.Series:
    """Map timestamps to continuous morning/afternoon segments."""

    minutes = timestamp.dt.hour * 60 + timestamp.dt.minute
    values = np.select(
        [minutes.between(9 * 60 + 30, 11 * 60 + 30), minutes.between(13 * 60, 15 * 60 - 1)],
        ["am", "pm"],
        default="out",
    )
    result = pd.Series(values, index=timestamp.index, dtype="string")
    if is_session_bar is not None:
        result = result.where(_boolean_series(is_session_bar, name="is_session_bar"), "out")
    return result


def _prepare_input(
    frame: pd.DataFrame, settings: OrderFlowPredictionConfig
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Validate input and add causal lag columns and future-return labels."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("prediction input must be a pandas DataFrame")
    required = {"timestamp", "open", settings.factor_column}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("prediction input is missing columns: " + ", ".join(missing))

    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("prediction input contains invalid timestamps")
    if "symbol" not in data.columns:
        data["symbol"] = "__single__"
    data["symbol"] = data["symbol"].astype("string").str.strip()
    if data["symbol"].isna().any() or data["symbol"].eq("").any():
        raise ValueError("prediction input contains an empty symbol")
    data["open"] = pd.to_numeric(data["open"], errors="coerce")
    data[settings.factor_column] = pd.to_numeric(data[settings.factor_column], errors="coerce")
    data = data.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
    if data.duplicated(["symbol", "timestamp"]).any():
        raise ValueError("prediction input contains duplicate symbol/timestamp rows")

    if "session_date_key" in data.columns:
        session_key = pd.to_numeric(data["session_date_key"], errors="coerce")
        fallback = data["timestamp"].dt.strftime("%Y%m%d").astype("int64")
        data["prediction_session_date"] = session_key.fillna(fallback).astype("int64")
    else:
        data["prediction_session_date"] = data["timestamp"].dt.strftime("%Y%m%d").astype("int64")
    is_session_bar = data["is_session_bar"] if "is_session_bar" in data.columns else None
    data["prediction_session_part"] = _session_part(data["timestamp"], is_session_bar)
    group_keys = ["symbol", "prediction_session_date", "prediction_session_part"]
    grouped = data.groupby(group_keys, sort=False, dropna=False)

    # The current factor is available at the close.  Entry starts at the next open and the exit
    # open is h bars after that entry, all within the same continuous trading segment.
    data["prediction_entry_open"] = grouped["open"].shift(-1)
    data["prediction_exit_open"] = grouped["open"].shift(-(settings.horizon_bars + 1))
    data["prediction_entry_timestamp"] = grouped["timestamp"].shift(-1)
    data["prediction_exit_timestamp"] = grouped["timestamp"].shift(-(settings.horizon_bars + 1))
    data["future_return"] = data["prediction_exit_open"] / data["prediction_entry_open"] - 1.0
    data["label_end_timestamp"] = grouped["timestamp"].shift(-(settings.horizon_bars + 1))

    if "of_data_valid" in data.columns:
        data_valid = _boolean_series(data["of_data_valid"], name="of_data_valid")
    else:
        data_valid = data[settings.factor_column].notna()
    if "of_history_ready" in data.columns:
        history_ready = _boolean_series(data["of_history_ready"], name="of_history_ready")
    else:
        history_ready = pd.Series(True, index=data.index)
    current_open_valid = pd.Series(
        np.isfinite(data["open"].to_numpy(dtype=float)) & data["open"].gt(0.0).to_numpy(),
        index=data.index,
    )
    factor_missing = data[settings.factor_column].isna()
    factor_finite = pd.Series(
        np.isfinite(data[settings.factor_column].to_numpy(dtype=float)), index=data.index
    )
    in_session = data["prediction_session_part"].ne("out").to_numpy()
    data["prediction_feature_eligible"] = (
        data_valid.to_numpy()
        & factor_finite.to_numpy()
        & current_open_valid.to_numpy()
        & in_session
        & (history_ready.to_numpy() if settings.require_history_ready else True)
    )
    future_price_valid = data["prediction_entry_open"].gt(0.0) & data["prediction_exit_open"].gt(
        0.0
    )
    future_price_valid &= np.isfinite(data["prediction_entry_open"].to_numpy(dtype=float))
    future_price_valid &= np.isfinite(data["prediction_exit_open"].to_numpy(dtype=float))
    target_valid = (
        future_price_valid & data["future_return"].notna() & data["label_end_timestamp"].notna()
    )
    target_valid &= np.isfinite(data["future_return"].to_numpy(dtype=float))
    bar_delta = pd.to_timedelta(settings.bar_minutes, unit="min")
    expected_entry_timestamp = data["timestamp"] + bar_delta
    expected_exit_timestamp = data["timestamp"] + bar_delta * (settings.horizon_bars + 1)
    entry_timestamp_present = data["prediction_entry_timestamp"].notna()
    exit_timestamp_present = data["prediction_exit_timestamp"].notna()
    entry_time_aligned = data["prediction_entry_timestamp"].eq(expected_entry_timestamp)
    exit_time_aligned = data["prediction_exit_timestamp"].eq(expected_exit_timestamp)
    data["prediction_time_aligned"] = (entry_time_aligned & exit_time_aligned).astype(bool)
    target_valid &= data["prediction_time_aligned"]
    data["prediction_target_available"] = target_valid.astype(bool)
    data["prediction_eligible"] = (
        data["prediction_feature_eligible"] & data["prediction_target_available"]
    ).astype(bool)
    data["prediction_invalid_reason"] = np.select(
        [
            ~in_session,
            ~data_valid.to_numpy(),
            settings.require_history_ready & ~history_ready.to_numpy(),
            factor_missing.to_numpy(),
            ~factor_finite.to_numpy(),
            ~current_open_valid.to_numpy(),
            ~(entry_timestamp_present & exit_timestamp_present).to_numpy(),
            (~entry_time_aligned | ~exit_time_aligned).to_numpy(),
            ~future_price_valid.to_numpy(),
            ~target_valid.to_numpy(),
        ],
        [
            "out_of_session",
            "invalid_order_flow_data",
            "history_not_ready",
            "factor_missing",
            "factor_non_finite",
            "current_open_invalid",
            "future_window_missing",
            "bar_interval_or_gap",
            "future_price_invalid",
            "future_return_invalid",
        ],
        default="",
    )

    available_features: list[str] = []
    for column in settings.feature_columns:
        if column not in data.columns:
            continue
        data[column] = pd.to_numeric(data[column], errors="coerce")
        available_features.append(column)
    if settings.factor_column not in available_features:
        available_features.insert(0, settings.factor_column)
    for lag in settings.factor_lags:
        lag_name = f"{settings.factor_column}_lag_{lag}"
        data[lag_name] = grouped[settings.factor_column].shift(lag)
        available_features.append(lag_name)
    available_features = list(dict.fromkeys(available_features))
    if not available_features:
        raise ValueError("prediction input has no usable numeric feature columns")
    data.attrs["prediction_feature_columns"] = available_features
    data.attrs["prediction_group_keys"] = group_keys
    data.attrs["prediction_version"] = PREDICTION_VERSION
    return data, available_features, group_keys


def build_prediction_frame(
    frame: pd.DataFrame,
    *,
    config: OrderFlowPredictionConfig | None = None,
) -> pd.DataFrame:
    """Build a labeled, causal prediction frame while retaining invalid rows as visible gaps."""

    settings = config or OrderFlowPredictionConfig()
    data, _features, _groups = _prepare_input(frame, settings)
    return data


def factor_event_study(
    frame: pd.DataFrame,
    *,
    config: OrderFlowPredictionConfig | None = None,
    bins: int = 5,
) -> pd.DataFrame:
    """Summarize future returns by factor quantile for exploratory validation."""

    if isinstance(bins, bool) or not isinstance(bins, int) or bins < 2:
        raise ValueError("bins must be an integer of at least 2")
    settings = config or OrderFlowPredictionConfig()
    data = build_prediction_frame(frame, config=settings)
    eligible = data.loc[data["prediction_eligible"]].copy()
    columns = [
        "factor_bucket",
        "n",
        "factor_min",
        "factor_max",
        "factor_mean",
        "mean_return",
        "median_return",
        "mean_net_return",
        "hit_rate",
    ]
    if eligible.empty:
        return pd.DataFrame(columns=columns)
    factor = pd.to_numeric(eligible[settings.factor_column], errors="coerce")
    eligible = eligible.loc[factor.notna() & eligible["future_return"].notna()].copy()
    if eligible.empty:
        return pd.DataFrame(columns=columns)
    ranks = eligible[settings.factor_column].rank(method="first")
    bucket_count = min(bins, len(eligible))
    eligible["factor_bucket"] = (
        pd.qcut(ranks, q=bucket_count, labels=False, duplicates="drop").astype(int) + 1
    )
    grouped = eligible.groupby("factor_bucket", sort=True, observed=True)
    result = grouped.agg(
        n=("future_return", "size"),
        factor_min=(settings.factor_column, "min"),
        factor_max=(settings.factor_column, "max"),
        factor_mean=(settings.factor_column, "mean"),
        mean_return=("future_return", "mean"),
        median_return=("future_return", "median"),
        mean_net_return=("future_return", lambda values: values.mean() - settings.round_trip_cost),
        hit_rate=("future_return", lambda values: (values > 0.0).mean()),
    )
    return result.reset_index()[columns]


class RidgeReturnModel:
    """Small standardized Ridge regression with train-only imputation."""

    def __init__(self, alpha: float = 10.0) -> None:
        value = float(alpha)
        if not np.isfinite(value) or value < 0.0:
            raise ValueError("alpha must be finite and non-negative")
        self.alpha = value
        self.feature_columns_: list[str] = []
        self.medians_: np.ndarray | None = None
        self.means_: np.ndarray | None = None
        self.scales_: np.ndarray | None = None
        self.coefficients_: np.ndarray | None = None
        self.n_samples_: int = 0

    @staticmethod
    def _numeric_matrix(frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
        values = frame.reindex(columns=columns).apply(pd.to_numeric, errors="coerce")
        return values.to_numpy(dtype=float)

    def fit(self, features: pd.DataFrame, target: pd.Series) -> RidgeReturnModel:
        if not isinstance(features, pd.DataFrame):
            raise TypeError("model features must be a pandas DataFrame")
        if len(features) != len(target):
            raise ValueError("model features and target must have the same length")
        if features.empty:
            raise ValueError("model cannot fit an empty frame")
        columns = [str(column) for column in features.columns]
        matrix = self._numeric_matrix(features, columns)
        y = pd.to_numeric(target, errors="coerce").to_numpy(dtype=float)
        valid_target = np.isfinite(y)
        if not valid_target.any():
            raise ValueError("model target contains no finite values")
        matrix = matrix[valid_target]
        y = y[valid_target]

        active: list[int] = []
        medians: list[float] = []
        for index in range(matrix.shape[1]):
            finite = matrix[:, index][np.isfinite(matrix[:, index])]
            if finite.size == 0:
                continue
            active.append(index)
            medians.append(float(np.median(finite)))
        if not active:
            raise ValueError("model features contain no finite training values")
        matrix = matrix[:, active]
        matrix = np.where(np.isfinite(matrix), matrix, np.asarray(medians, dtype=float))
        means = matrix.mean(axis=0)
        scales = matrix.std(axis=0)
        scales = np.where(np.isfinite(scales) & (scales > 1e-12), scales, 1.0)
        standardized = (matrix - means) / scales
        design = np.column_stack((np.ones(len(standardized)), standardized))
        regularizer = np.eye(design.shape[1], dtype=float)
        regularizer[0, 0] = 0.0
        lhs = design.T @ design + self.alpha * regularizer
        rhs = design.T @ y
        try:
            coefficients = np.linalg.solve(lhs, rhs)
        except np.linalg.LinAlgError:
            coefficients = np.linalg.lstsq(lhs, rhs, rcond=None)[0]

        self.feature_columns_ = [columns[index] for index in active]
        self.medians_ = np.asarray(medians, dtype=float)
        self.means_ = np.asarray(means, dtype=float)
        self.scales_ = np.asarray(scales, dtype=float)
        self.coefficients_ = np.asarray(coefficients, dtype=float)
        self.n_samples_ = len(y)
        return self

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        if self.coefficients_ is None or self.medians_ is None:
            raise RuntimeError("model must be fitted before predict")
        matrix = self._numeric_matrix(features, self.feature_columns_)
        matrix = np.where(np.isfinite(matrix), matrix, self.medians_)
        standardized = (matrix - self.means_) / self.scales_
        design = np.column_stack((np.ones(len(standardized)), standardized))
        return design @ self.coefficients_

    def as_dict(self) -> dict[str, Any]:
        """Return model metadata without serializing fitted arrays."""

        return {
            "model": "ridge_return",
            "alpha": self.alpha,
            "feature_columns": list(self.feature_columns_),
            "train_rows": self.n_samples_,
        }


@dataclass(frozen=True, slots=True)
class OrderFlowPredictionResult:
    """Walk-forward predictions and compact validation artifacts."""

    frame: pd.DataFrame
    folds: pd.DataFrame
    event_study: pd.DataFrame
    metrics: dict[str, Any]


def _fit_mask(
    data: pd.DataFrame,
    dates: list[int],
    *,
    boundary: pd.Timestamp | None,
) -> pd.Series:
    mask = data["prediction_eligible"] & data["prediction_session_date"].isin(dates)
    if boundary is not None:
        mask &= data["label_end_timestamp"].lt(boundary)
    return mask


def _choose_threshold(
    validation: pd.DataFrame,
    predictions: np.ndarray,
    settings: OrderFlowPredictionConfig,
) -> float:
    base = settings.signal_threshold
    candidates = settings.threshold_grid or (base,)
    if validation.empty:
        return base
    values = pd.to_numeric(validation["future_return"], errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(values) & np.isfinite(predictions)
    if not finite.any():
        return base
    best_threshold = base
    best_score = -np.inf
    for threshold in candidates:
        selected = finite & (predictions >= threshold)
        if int(selected.sum()) < settings.min_validation_signals:
            continue
        score = float(np.mean(values[selected] - settings.round_trip_cost))
        if score > best_score:
            best_score = score
            best_threshold = float(threshold)
    return best_threshold


def summarize_predictions(
    frame: pd.DataFrame,
    *,
    config: OrderFlowPredictionConfig | None = None,
) -> dict[str, Any]:
    """Summarize predictive and signal outcomes without annualizing intraday rows."""

    settings = config or OrderFlowPredictionConfig()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("prediction summary input must be a pandas DataFrame")
    target = frame.get("future_return", pd.Series(dtype=float))
    predicted = frame.get("predicted_return", pd.Series(dtype=float))
    signal = frame.get("prediction_signal", pd.Series(False, index=frame.index))
    target = pd.to_numeric(target, errors="coerce")
    predicted = pd.to_numeric(predicted, errors="coerce")
    evaluable = target.notna() & predicted.notna()
    signal_mask = _boolean_series(signal, name="prediction_signal") & evaluable
    metrics: dict[str, Any] = {
        "prediction_version": PREDICTION_VERSION,
        "rows": len(frame),
        "evaluable_predictions": int(evaluable.sum()),
        "signal_rows": int(signal_mask.sum()),
        "signal_threshold": settings.signal_threshold,
        "round_trip_cost": settings.round_trip_cost,
    }
    if evaluable.any():
        actual = target.loc[evaluable]
        estimate = predicted.loc[evaluable]
        metrics["mean_predicted_return"] = float(estimate.mean())
        metrics["mean_future_return"] = float(actual.mean())
        # Pandas delegates Spearman correlation to SciPy, which is not a project dependency.
        # Ranking first gives the same statistic for finite, non-constant samples.
        ranked_actual = actual.rank(method="average")
        ranked_estimate = estimate.rank(method="average")
        correlation = ranked_actual.corr(ranked_estimate)
        metrics["spearman_ic"] = float(correlation) if pd.notna(correlation) else None
    else:
        metrics.update(
            {
                "mean_predicted_return": None,
                "mean_future_return": None,
                "spearman_ic": None,
            }
        )
    if signal_mask.any():
        signal_returns = target.loc[signal_mask]
        metrics["signal_mean_return"] = float(signal_returns.mean())
        metrics["signal_mean_net_return"] = float(signal_returns.mean() - settings.round_trip_cost)
        metrics["signal_hit_rate"] = float((signal_returns > 0.0).mean())
    else:
        metrics.update(
            {
                "signal_mean_return": None,
                "signal_mean_net_return": None,
                "signal_hit_rate": None,
            }
        )
    if "prediction_fold" in frame.columns:
        metrics["predicted_rows"] = int(frame["prediction_fold"].notna().sum())
        metrics["folds_with_predictions"] = int(frame["prediction_fold"].dropna().nunique())
    if "prediction_threshold" in frame.columns:
        thresholds = pd.to_numeric(frame["prediction_threshold"], errors="coerce").dropna()
        metrics["thresholds_used"] = sorted(float(value) for value in thresholds.unique())
    return metrics


def walk_forward_predict(
    frame: pd.DataFrame,
    *,
    config: OrderFlowPredictionConfig | None = None,
) -> OrderFlowPredictionResult:
    """Fit Ridge models on prior sessions and return strictly chronological predictions."""

    settings = config or OrderFlowPredictionConfig()
    data, feature_columns, _groups = _prepare_input(frame, settings)
    data["predicted_return"] = np.nan
    data["prediction_signal"] = False
    data["prediction_threshold"] = np.nan
    data["prediction_fold"] = pd.Series(pd.NA, index=data.index, dtype="Int64")
    data["prediction_train_rows"] = pd.Series(pd.NA, index=data.index, dtype="Int64")

    eligible = data["prediction_eligible"].astype(bool)
    feature_eligible = data["prediction_feature_eligible"].astype(bool)
    # Keep sessions with an incomplete label window in the chronological calendar.  They can be
    # valid test sessions for scoring even though their realized return is not available yet.
    dates = sorted(data.loc[feature_eligible, "prediction_session_date"].unique().tolist())
    first_test = settings.train_sessions + settings.validation_sessions
    fold_rows: list[dict[str, Any]] = []
    fold_id = 0
    for test_start in range(first_test, len(dates), settings.test_sessions):
        validation_start = test_start - settings.validation_sessions
        train_start = max(0, validation_start - settings.train_sessions)
        train_dates = dates[train_start:validation_start]
        validation_dates = dates[validation_start:test_start]
        test_dates = dates[test_start : test_start + settings.test_sessions]
        if not test_dates or not train_dates:
            continue
        test_mask = data["prediction_session_date"].isin(test_dates)
        test_start_timestamp = data.loc[test_mask, "timestamp"].min()
        validation_mask = data["prediction_session_date"].isin(validation_dates)
        validation_start_timestamp = (
            data.loc[validation_mask, "timestamp"].min()
            if validation_mask.any()
            else test_start_timestamp
        )
        train_mask = _fit_mask(data, train_dates, boundary=validation_start_timestamp)
        train_rows = int(train_mask.sum())
        fold_info: dict[str, Any] = {
            "fold": fold_id,
            "train_sessions": len(train_dates),
            "validation_sessions": len(validation_dates),
            "test_sessions": len(test_dates),
            "train_rows": train_rows,
            "validation_rows": 0,
            "test_rows": int((test_mask & eligible).sum()),
            "status": "skipped",
        }
        if train_rows < settings.min_train_rows:
            fold_info["reason"] = "insufficient_training_rows"
            fold_rows.append(fold_info)
            fold_id += 1
            continue
        model = RidgeReturnModel(settings.ridge_alpha).fit(
            data.loc[train_mask, feature_columns], data.loc[train_mask, "future_return"]
        )
        threshold = settings.signal_threshold
        validation_eligible = validation_mask & eligible
        validation_eligible &= data["label_end_timestamp"].lt(test_start_timestamp)
        if validation_eligible.any():
            validation_predictions = model.predict(data.loc[validation_eligible, feature_columns])
            threshold = _choose_threshold(
                data.loc[validation_eligible], validation_predictions, settings
            )
            fold_info["validation_rows"] = int(validation_eligible.sum())

        final_mask = _fit_mask(
            data,
            train_dates + validation_dates,
            boundary=test_start_timestamp,
        )
        if int(final_mask.sum()) >= settings.min_train_rows:
            model = RidgeReturnModel(settings.ridge_alpha).fit(
                data.loc[final_mask, feature_columns], data.loc[final_mask, "future_return"]
            )
        test_predict_mask = test_mask & data["prediction_feature_eligible"]
        if test_predict_mask.any():
            predictions = model.predict(data.loc[test_predict_mask, feature_columns])
            data.loc[test_predict_mask, "predicted_return"] = predictions
            data.loc[test_predict_mask, "prediction_signal"] = predictions >= threshold
            data.loc[test_predict_mask, "prediction_threshold"] = threshold
            data.loc[test_predict_mask, "prediction_fold"] = fold_id
            data.loc[test_predict_mask, "prediction_train_rows"] = model.n_samples_
            evaluable_test = test_predict_mask & data["prediction_target_available"]
            signal_test = evaluable_test & data["prediction_signal"]
            if signal_test.any():
                fold_info["test_signal_rows"] = int(signal_test.sum())
                fold_info["test_signal_mean_net_return"] = float(
                    data.loc[signal_test, "future_return"].mean() - settings.round_trip_cost
                )
            else:
                fold_info["test_signal_rows"] = 0
                fold_info["test_signal_mean_net_return"] = None
            fold_info["status"] = "ok"
        else:
            fold_info["reason"] = "no_test_features"
        fold_rows.append(fold_info)
        fold_id += 1

    data.attrs["prediction_feature_columns"] = feature_columns
    folds = pd.DataFrame(fold_rows)
    event_study = factor_event_study(data, config=settings)
    metrics = summarize_predictions(data, config=settings)
    metrics["fold_count"] = len(folds)
    metrics["successful_fold_count"] = int(folds["status"].eq("ok").sum()) if not folds.empty else 0
    return OrderFlowPredictionResult(data, folds, event_study, metrics)


def fit_predict_latest(
    frame: pd.DataFrame,
    *,
    config: OrderFlowPredictionConfig | None = None,
) -> pd.DataFrame:
    """Fit on completed prior sessions and score the latest session's available bars."""

    settings = config or OrderFlowPredictionConfig()
    data, feature_columns, _groups = _prepare_input(frame, settings)
    data["predicted_return"] = np.nan
    data["prediction_signal"] = False
    data["prediction_threshold"] = np.nan
    data["prediction_fold"] = pd.Series(pd.NA, index=data.index, dtype="Int64")
    data["prediction_train_rows"] = pd.Series(pd.NA, index=data.index, dtype="Int64")
    feature_eligible = data["prediction_feature_eligible"].astype(bool)
    dates = sorted(data.loc[feature_eligible, "prediction_session_date"].unique().tolist())
    if len(dates) < 2:
        raise ValueError("latest prediction needs at least one prior session")
    latest_date = dates[-1]
    train_dates = dates[:-1][-settings.train_sessions :]
    latest_mask = feature_eligible & data["prediction_session_date"].eq(latest_date)
    boundary = data.loc[latest_mask, "timestamp"].min()
    train_mask = _fit_mask(data, train_dates, boundary=boundary)
    if int(train_mask.sum()) < settings.min_train_rows:
        raise ValueError("latest prediction has insufficient completed training rows")
    model = RidgeReturnModel(settings.ridge_alpha).fit(
        data.loc[train_mask, feature_columns], data.loc[train_mask, "future_return"]
    )
    predictions = model.predict(data.loc[latest_mask, feature_columns])
    data.loc[latest_mask, "predicted_return"] = predictions
    data.loc[latest_mask, "prediction_signal"] = predictions >= settings.signal_threshold
    data.loc[latest_mask, "prediction_threshold"] = settings.signal_threshold
    data.loc[latest_mask, "prediction_fold"] = 0
    data.loc[latest_mask, "prediction_train_rows"] = model.n_samples_
    data.attrs["prediction_feature_columns"] = feature_columns
    data.attrs["prediction_latest_session"] = int(latest_date)
    data.attrs["prediction_training_rows"] = model.n_samples_
    # ``prediction_feature_eligible`` is intentionally used here: the latest future label is not
    # known yet, but the current feature can still be scored.
    return data


def apply_prediction_signals(
    frame: pd.DataFrame,
    *,
    exit_threshold: float = 0.0,
    require_target_available: bool = False,
) -> pd.DataFrame:
    """Map expected-return predictions to the existing order-flow signal columns.

    Historical backtests should set ``require_target_available=True`` so rows whose future window
    is incomplete cannot create a trade.  Live scoring deliberately leaves that flag false because
    the future label is not known yet.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("prediction signal input must be a pandas DataFrame")
    required = {"predicted_return", "prediction_signal"}
    if require_target_available:
        required.add("prediction_target_available")
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("prediction signal input is missing columns: " + ", ".join(missing))
    threshold = float(exit_threshold)
    if not np.isfinite(threshold):
        raise ValueError("exit_threshold must be finite")
    data = frame.copy()
    if "prediction_feature_eligible" in data.columns:
        eligible = _boolean_series(
            data["prediction_feature_eligible"], name="prediction_feature_eligible"
        )
    elif "prediction_target_available" in data.columns:
        eligible = _boolean_series(
            data["prediction_target_available"], name="prediction_target_available"
        )
    else:
        eligible = pd.Series(True, index=data.index)
    if require_target_available:
        eligible &= _boolean_series(
            data["prediction_target_available"], name="prediction_target_available"
        )
    if "of_data_valid" in data.columns:
        eligible &= _boolean_series(data["of_data_valid"], name="of_data_valid")
    else:
        data["of_data_valid"] = eligible.astype(float)
    predicted = pd.to_numeric(data["predicted_return"], errors="coerce")
    data["of_entry_signal"] = (
        eligible
        & _boolean_series(data["prediction_signal"], name="prediction_signal")
        & predicted.notna()
    ).astype(bool)
    data["of_exit_signal"] = (eligible & predicted.le(threshold) & predicted.notna()).astype(bool)
    data["prediction_execution_signal"] = np.select(
        [data["of_entry_signal"], data["of_exit_signal"]], [1, -1], default=0
    ).astype(int)
    return data


def run_prediction_backtest(
    predictions: pd.DataFrame,
    *,
    config: OrderFlowConfig | None = None,
    exit_threshold: float = 0.0,
) -> OrderFlowBacktestResult:
    """Run prediction-derived entries through the existing easy-tdx simulator."""

    from .backtest import run_order_flow_backtest

    execution = apply_prediction_signals(
        predictions,
        exit_threshold=exit_threshold,
        require_target_available=True,
    )
    # Future labels are useful in the research report but must not cross the simulator boundary.
    execution = execution.drop(
        columns=[
            "prediction_entry_open",
            "prediction_exit_open",
            "future_return",
            "label_end_timestamp",
            "prediction_target_available",
            "prediction_eligible",
            "prediction_invalid_reason",
        ],
        errors="ignore",
    )
    return run_order_flow_backtest(execution, config=config)


def _read_frame(path: Path) -> pd.DataFrame:
    if path.suffix.casefold() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, parse_dates=["timestamp"])


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON report {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"JSON report {path} must contain an object")
    return payload


def _write_frame(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.casefold() == ".parquet":
        try:
            frame.to_parquet(path, index=False)
            return path
        except (ImportError, ModuleNotFoundError):
            path = path.with_suffix(".csv")
    frame.to_csv(path, index=False)
    return path


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if pd.isna(value) if not isinstance(value, (list, tuple, dict)) else False:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON prediction configuration")
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument(
        "--source-report",
        type=Path,
        help="optional order-flow source report to carry provider provenance",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--latest", action="store_true", help="score the latest session")
    parser.add_argument("--bar-minutes", type=int, choices=(1, 5, 15, 30, 60))
    parser.add_argument("--horizon-bars", type=int)
    parser.add_argument("--train-sessions", type=int)
    parser.add_argument("--validation-sessions", type=int)
    parser.add_argument("--test-sessions", type=int)
    parser.add_argument("--min-train-rows", type=int)
    parser.add_argument("--ridge-alpha", type=float)
    parser.add_argument("--round-trip-cost", type=float)
    parser.add_argument("--edge-buffer", type=float)
    parser.add_argument("--prediction-threshold", type=float)
    parser.add_argument("--factor-column")
    parser.add_argument(
        "--factor-lags",
        default=None,
        help="comma-separated positive factor lags; empty string disables lags",
    )
    parser.add_argument(
        "--threshold-grid",
        default=None,
        help="comma-separated validation thresholds; empty uses the configured threshold",
    )
    parser.add_argument("--allow-history-not-ready", action="store_true")
    return parser


def _floats(value: str) -> tuple[float, ...]:
    if not value.strip():
        return ()
    return tuple(float(item.strip()) for item in value.split(",") if item.strip())


def _ints(value: str) -> tuple[int, ...]:
    if not value.strip():
        return ()
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def _config_from_args(args: argparse.Namespace) -> OrderFlowPredictionConfig:
    """Merge an optional JSON config with only explicitly supplied CLI values."""

    config_path = getattr(args, "config", None)
    settings = (
        OrderFlowPredictionConfig.from_json(config_path)
        if config_path is not None
        else OrderFlowPredictionConfig()
    )
    names = (
        "bar_minutes",
        "horizon_bars",
        "train_sessions",
        "validation_sessions",
        "test_sessions",
        "min_train_rows",
        "ridge_alpha",
        "round_trip_cost",
        "edge_buffer",
        "prediction_threshold",
        "factor_column",
    )
    overrides = {
        name: getattr(args, name) for name in names if getattr(args, name, None) is not None
    }
    if getattr(args, "factor_lags", None) is not None:
        overrides["factor_lags"] = _ints(args.factor_lags)
    if getattr(args, "threshold_grid", None) is not None:
        overrides["threshold_grid"] = _floats(args.threshold_grid)
    if getattr(args, "allow_history_not_ready", False):
        overrides["require_history_ready"] = False
    return OrderFlowPredictionConfig.from_mapping(overrides, base=settings)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _config_from_args(args)
    source = _read_frame(args.features)
    source_report = _read_json(args.source_report) if args.source_report is not None else None
    if args.latest:
        output = fit_predict_latest(source, config=settings)
        folds = pd.DataFrame()
        event_study = factor_event_study(source, config=settings)
        metrics = summarize_predictions(output, config=settings)
    else:
        result = walk_forward_predict(source, config=settings)
        output, folds, event_study = result.frame, result.folds, result.event_study
        metrics = result.metrics
    output_path = _write_frame(output, args.output)
    report = {
        "schema_version": 1,
        "prediction_version": PREDICTION_VERSION,
        "mode": "latest" if args.latest else "walk_forward",
        "input": str(args.features),
        "source_report": str(args.source_report) if args.source_report is not None else None,
        "output": str(output_path),
        "config": settings.as_dict(),
        "feature_columns": list(output.attrs.get("prediction_feature_columns", [])),
        "metrics": metrics,
        "folds": folds.to_dict(orient="records"),
        "event_study": event_study.to_dict(orient="records"),
        "source_provenance": source_report.get("data", {}) if source_report else None,
        "warnings": [
            "prediction target uses next-open to future-open returns within one continuous session",
            "transaction direction is an aggressor-side proxy, not a complete Level-2 order stream",
            "factor event study uses all eligible input rows and is exploratory, not out-of-sample",
            "walk-forward estimates are research output and require fee-aware out-of-sample validation",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(_json_value(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(_json_value({"output": str(output_path), "report": str(args.report)})))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the module entry point.
    raise SystemExit(main())


__all__ = [
    "DEFAULT_FACTOR_LAGS",
    "DEFAULT_PREDICTION_FEATURES",
    "PREDICTION_VERSION",
    "OrderFlowPredictionConfig",
    "OrderFlowPredictionResult",
    "PredictionTarget",
    "RidgeReturnModel",
    "apply_prediction_signals",
    "build_prediction_frame",
    "factor_event_study",
    "fit_predict_latest",
    "run_prediction_backtest",
    "summarize_predictions",
    "walk_forward_predict",
]
