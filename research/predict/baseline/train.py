"""Walk-forward training and evaluation for the XGBoost direction baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb

DEFAULT_PARAMS: dict[str, Any] = {
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "min_child_weight": 5,
    "eval_metric": "auc",
    "tree_method": "hist",
    "seed": 42,
}
NUM_BOOST_ROUNDS = 500
EARLY_STOPPING_ROUNDS = 30


@dataclass(frozen=True, slots=True)
class SplitDates:
    """Chronological cutoff dates for train / validation / test."""

    train_end: date
    valid_end: date


def split_by_date(df: pd.DataFrame, split: SplitDates) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return chronological train, validation, and test masks from the two cutoff dates."""

    dates = pd.to_datetime(df["timestamp"]).dt.date
    train = dates <= split.train_end
    valid = (dates > split.train_end) & (dates <= split.valid_end)
    test = dates > split.valid_end
    return train, valid, test


def fit_classifier(
    train_df: pd.DataFrame,
    valid_df: pd.DataFrame,
    features: list[str],
    *,
    seed: int = 42,
    params: dict[str, Any] | None = None,
) -> tuple[xgb.Booster, dict[str, Any]]:
    """Fit a binary XGBoost classifier with validation early stopping.

    Returns the fitted model plus the effective parameter dict for provenance.
    """

    merged = {**DEFAULT_PARAMS, "seed": seed, **(params or {})}
    num_boost_round = int(merged.pop("n_estimators", NUM_BOOST_ROUNDS))
    early_stopping = int(merged.pop("early_stopping_rounds", EARLY_STOPPING_ROUNDS))
    dtrain = xgb.DMatrix(train_df[features], label=train_df["label"].to_numpy())
    dvalid = xgb.DMatrix(valid_df[features], label=valid_df["label"].to_numpy())
    booster = xgb.train(
        params=merged,
        dtrain=dtrain,
        num_boost_round=num_boost_round,
        evals=[(dvalid, "valid")],
        early_stopping_rounds=early_stopping,
        verbose_eval=False,
    )
    merged["n_estimators"] = num_boost_round
    merged["early_stopping_rounds"] = early_stopping
    return booster, merged


def predict_probabilities(
    model: xgb.Booster,
    df: pd.DataFrame,
    features: list[str],
    prob_col: str = "prob",
) -> pd.DataFrame:
    """Score a frame, leaving ``prob`` missing where features are incomplete."""

    out = df.copy()
    complete = out[features].notna().all(axis=1)
    out[prob_col] = float("nan")
    if complete.any():
        dmatrix = xgb.DMatrix(out.loc[complete, features])
        out.loc[complete, prob_col] = model.predict(dmatrix)
    return out


def select_threshold(valid_df: pd.DataFrame, prob_col: str = "prob") -> float:
    """Maximize Youden's J (TPR - FPR) on the validation set."""

    probs = valid_df[prob_col].to_numpy()
    labels = valid_df["label"].to_numpy()
    best_threshold = 0.5
    best_youden = -1.0
    for threshold in np.arange(0.50, 0.71, 0.01):
        predicted = probs >= threshold
        true_positive = ((predicted == 1) & (labels == 1)).sum()
        false_positive = ((predicted == 1) & (labels == 0)).sum()
        true_negative = ((predicted == 0) & (labels == 0)).sum()
        false_negative = ((predicted == 0) & (labels == 1)).sum()
        tpr = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        fpr = (
            false_positive / (false_positive + true_negative)
            if false_positive + true_negative
            else 0.0
        )
        youden = tpr - fpr
        if youden > best_youden:
            best_youden = youden
            best_threshold = float(threshold)
    return best_threshold


def _auc(probs: np.ndarray, labels: np.ndarray) -> float:
    positive = labels == 1
    if positive.sum() == 0 or (~positive).sum() == 0:
        return float("nan")
    ranks = pd.Series(probs).rank().to_numpy()
    sum_positive_ranks = ranks[positive].sum()
    return float(
        (sum_positive_ranks - positive.sum() * (positive.sum() + 1) / 2)
        / (positive.sum() * (~positive).sum())
    )


def evaluate(
    df: pd.DataFrame,
    threshold: float,
    prob_col: str = "prob",
    baseline_col: str = "ret_1",
) -> dict[str, float | int | None]:
    """Classification metrics plus RankIC and naive baselines on a scored frame."""

    probs = df[prob_col].to_numpy()
    labels = df["label"].to_numpy()
    predicted = (probs >= threshold).astype(int)
    total = len(df)
    accuracy = float((predicted == labels).mean()) if total else float("nan")
    true_positive = ((predicted == 1) & (labels == 1)).sum()
    false_positive = ((predicted == 1) & (labels == 0)).sum()
    false_negative = ((predicted == 0) & (labels == 1)).sum()
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else float("nan")
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else float("nan")
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if not np.isnan(precision) and not np.isnan(recall) and precision + recall > 0
        else float("nan")
    )
    rank_ic = df[prob_col].corr(df["label_return"], method="spearman") if total else float("nan")
    yesterday = (df[baseline_col] > 0).astype(int)
    yesterday_accuracy = float((yesterday == labels).mean()) if total else float("nan")
    return {
        "rows": int(total),
        "accuracy": _round(accuracy),
        "auc": _round(_auc(probs, labels)),
        "precision": _round(precision),
        "recall": _round(recall),
        "f1": _round(f1),
        "rank_ic": _round(rank_ic),
        "yesterday_direction_accuracy": _round(yesterday_accuracy),
        "random_accuracy": 0.5,
        "positive_rate": _round(float(labels.mean())) if total else None,
    }


def _round(value: float) -> float | None:
    if value is None or np.isnan(value):
        return None
    return round(float(value), 6)
