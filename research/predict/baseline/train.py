"""Walk-forward training and evaluation for the XGBoost direction baseline.

The module exposes the reusable training pieces (``build_dataset``, ``fit_classifier``, evaluation)
and the standalone training CLI:

    ./.venv/bin/python -m research.predict.baseline.train

The CLI fetches data, builds features and labels, trains with validation early stopping, selects the
classification threshold, and optionally persists the model with its training-config fingerprint.
Prediction and backtesting live in ``predict.py``; ``run.py`` combines both in one pass.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb

from . import data as data_mod
from . import features as feat_mod
from . import labels as lbl_mod
from . import model_cache as cache_mod

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TRAIN_REPORT = PROJECT_ROOT / "reports" / "predict_baseline_train.json"

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


@dataclass(frozen=True, slots=True)
class Dataset:
    """Fetched, featurized, labeled panel plus the chronological split frames."""

    batch: data_mod.PredictDataBatch
    labeled: pd.DataFrame
    features: list[str]
    train_mask: pd.Series
    valid_mask: pd.Series
    test_mask: pd.Series
    train_df: pd.DataFrame
    valid_df: pd.DataFrame
    test_df: pd.DataFrame
    timing: dict[str, float]


def build_dataset(
    symbols: tuple[str, ...],
    start_date: str,
    end_date: str,
    adjust: str,
    split: SplitDates,
    *,
    source: data_mod.DataSource | None = None,
    count: int = data_mod.DEFAULT_BAR_COUNT,
) -> Dataset:
    """Run the fetch -> features -> labels -> chronological split chain once."""

    timing: dict[str, float] = {}
    started = time.perf_counter()
    batch = data_mod.fetch_bars(symbols, start_date, end_date, adjust, count=count, source=source)
    timing["fetch_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    feature_frame = feat_mod.compute_features(batch.frame)
    timing["features_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    labeled = lbl_mod.build_labels(feature_frame)
    timing["labels_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    train_mask, valid_mask, test_mask = split_by_date(labeled, split)
    features = list(feat_mod.FEATURE_COLUMNS)
    train_df = labeled[train_mask & labeled["eligible"]].dropna(subset=features).copy()
    valid_df = labeled[valid_mask & labeled["eligible"]].dropna(subset=features).copy()
    test_df = labeled[test_mask & labeled["eligible"]].dropna(subset=features).copy()
    timing["split_seconds"] = time.perf_counter() - started
    return Dataset(
        batch=batch,
        labeled=labeled,
        features=features,
        train_mask=train_mask,
        valid_mask=valid_mask,
        test_mask=test_mask,
        train_df=train_df,
        valid_df=valid_df,
        test_df=test_df,
        timing=timing,
    )


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


def _date(value: str) -> date:
    return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train the XGBoost next-day direction baseline and persist the model."
    )
    parser.add_argument("--symbols", nargs="+", default=list(data_mod.DEFAULT_SYMBOLS))
    parser.add_argument(
        "--symbols-file",
        default=None,
        help="path to a text file with the training universe, one 6-digit A-share code per line; "
        "lines starting with '#' are comments. Mutually exclusive with --symbols.",
    )
    parser.add_argument("--start-date", default=data_mod.DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=data_mod.DEFAULT_END_DATE)
    parser.add_argument("--adjust", default=data_mod.DEFAULT_ADJUST)
    parser.add_argument(
        "--source",
        default="easy-tdx",
        help="ecom-data source name for daily bars: easy-tdx (default, fast) or akshare "
        "(disk-cached remote AkShare)",
    )
    parser.add_argument(
        "--bar-count",
        type=int,
        default=data_mod.DEFAULT_BAR_COUNT,
        help="maximum bars requested per symbol (easy-tdx QFQ skips deep-history recompute "
        "with 2500; akshare ignores it)",
    )
    parser.add_argument("--train-end", default="20231231")
    parser.add_argument("--valid-end", default="20251231")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="override the validation-selected classification threshold",
    )
    parser.add_argument(
        "--model-cache",
        default=None,
        help="output path for the trained XGBoost model file (.ubj/.json); omitted means the "
        "model is evaluated in memory and not persisted",
    )
    parser.add_argument("--report", default=str(DEFAULT_TRAIN_REPORT))
    return parser.parse_args(argv)


def _load_symbols_file(path: Path) -> tuple[str, ...]:
    """Read a symbol list file: one 6-digit code per line, '#' comments, blank lines skipped."""

    if not path.is_file():
        raise FileNotFoundError(f"symbols file not found: {path}")
    symbols: list[str] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        code = line.split()[0]
        if len(code) != 6 or not code.isdigit():
            raise ValueError(f"{path}:{line_no}: expected a 6-digit A-share code, got {code!r}")
        symbols.append(code)
    if not symbols:
        raise ValueError(f"{path}: no symbols found")
    return tuple(dict.fromkeys(symbols))


def _resolve_symbols(args: argparse.Namespace) -> tuple[str, ...]:
    if args.symbols_file is not None:
        if args.symbols != list(data_mod.DEFAULT_SYMBOLS):
            raise ValueError("--symbols and --symbols-file are mutually exclusive")
        return _load_symbols_file(Path(args.symbols_file))
    return tuple(args.symbols)


def main(argv: list[str] | None = None) -> int:
    """Training CLI: fetch -> features -> labels -> split -> fit -> evaluate -> save model."""

    args = _parse_args(argv)
    try:
        symbols = _resolve_symbols(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"train: {exc}")
        return 2
    symbols_file = str(Path(args.symbols_file)) if args.symbols_file else None
    started_total = time.perf_counter()
    split = SplitDates(train_end=_date(args.train_end), valid_end=_date(args.valid_end))
    data_source = data_mod.get_source(args.source)
    dataset = build_dataset(
        symbols,
        args.start_date,
        args.end_date,
        args.adjust,
        split,
        source=data_source,
        count=args.bar_count,
    )
    features = dataset.features

    cache_path = Path(args.model_cache) if args.model_cache else None
    fingerprint = (
        cache_mod.build_fingerprint(
            symbols,
            args.start_date,
            args.end_date,
            args.adjust,
            args.train_end,
            args.valid_end,
            args.seed,
            features,
            DEFAULT_PARAMS,
            args.source,
            args.bar_count,
        )
        if cache_path is not None
        else None
    )
    cache_mode: str | None = None
    loaded: cache_mod.CacheLoad | None = None
    if cache_path is not None:
        loaded = cache_mod.load_cached(cache_path, fingerprint)
        if loaded.model is not None:
            cache_mode = "loaded"
            print(f"model cache: hit {cache_path}")
        else:
            print(f"model cache: miss ({loaded.reason}) {cache_path}; training")
    if cache_path is None or loaded is None or loaded.model is None:
        started_train = time.perf_counter()
        model, params = fit_classifier(dataset.train_df, dataset.valid_df, features, seed=args.seed)
        dataset.timing["train_seconds"] = time.perf_counter() - started_train
        if cache_path is not None:
            valid_scored_for_cache = predict_probabilities(model, dataset.valid_df, features)
            threshold_for_cache = (
                args.threshold
                if args.threshold is not None
                else select_threshold(valid_scored_for_cache)
            )
            cache_mod.save_cached(
                model,
                cache_path,
                fingerprint,
                params,
                threshold=threshold_for_cache,
            )
            cache_mode = "saved"
            print(f"model cache: saved {cache_path}")

    train_scored = predict_probabilities(model, dataset.train_df, features)
    valid_scored = predict_probabilities(model, dataset.valid_df, features)
    test_scored = predict_probabilities(model, dataset.test_df, features)
    threshold = args.threshold if args.threshold is not None else select_threshold(valid_scored)
    dataset.timing["total_seconds"] = time.perf_counter() - started_total

    exclusion_counts = (
        dataset.labeled.loc[~dataset.labeled["eligible"], "exclude_reason"].value_counts().to_dict()
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "strategy": "xgboost_next_day_direction",
        "target": "sign(open_{T+1} -> close_{T+1})",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "engine": {"xgboost": getattr(xgb, "__version__", "unknown")},
        "universe": list(symbols),
        "skipped": {str(k): str(v) for k, v in dataset.batch.skipped.items()},
        "window": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "adjust": args.adjust,
            "source": args.source,
            "bar_count": args.bar_count,
        },
        "symbols_file": symbols_file,
        "provenance": {
            "source": dataset.batch.source,
            "storage": dataset.batch.storage,
            "retrieved_at_utc": dataset.batch.retrieved_at_utc.isoformat(),
        },
        "features": features,
        "model": {
            "seed": args.seed,
            "params": {key: _json_value(value) for key, value in params.items()},
            "best_iteration": _json_value(getattr(model, "best_iteration", None)),
        },
        "model_cache": {
            "enabled": cache_path is not None,
            "path": str(cache_path) if cache_path is not None else None,
            "mode": cache_mode,
            "fingerprint": fingerprint,
        },
        "timing": {key: round(value, 3) for key, value in dataset.timing.items()},
        "splits": {
            "train_end": args.train_end,
            "valid_end": args.valid_end,
            "train_rows": len(dataset.train_df),
            "valid_rows": len(dataset.valid_df),
            "test_rows": len(dataset.test_df),
            "excluded_rows": int((~dataset.labeled["eligible"]).sum()),
            "exclusion_reasons": {str(k): int(v) for k, v in exclusion_counts.items()},
        },
        "metrics": {
            "threshold": threshold,
            "train": evaluate(train_scored, threshold),
            "validation": evaluate(valid_scored, threshold),
            "test": evaluate(test_scored, threshold),
        },
        "outputs": {
            "model": str(cache_path) if cache_path is not None else None,
            "report": str(report_path),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"universe: {', '.join(symbols)}")
    if dataset.batch.skipped:
        print(
            "skipped: "
            + "; ".join(f"{symbol}: {reason}" for symbol, reason in dataset.batch.skipped.items())
        )
    print(f"provenance: source={dataset.batch.source} storage={dataset.batch.storage}")
    print(
        f"rows: train={len(dataset.train_df)} valid={len(dataset.valid_df)} test={len(dataset.test_df)}"
    )
    timing = dataset.timing
    print(
        "timing: "
        f"fetch={timing.get('fetch_seconds', 0):.1f}s "
        f"features={timing.get('features_seconds', 0):.1f}s "
        f"labels={timing.get('labels_seconds', 0):.1f}s "
        f"train={timing.get('train_seconds', 0):.1f}s "
        f"total={timing.get('total_seconds', 0):.1f}s"
    )
    print(f"threshold: {threshold:.2f}")
    test_metrics = report["metrics"]["test"]
    print(
        "test: "
        f"auc={test_metrics['auc']} acc={test_metrics['accuracy']} "
        f"rank_ic={test_metrics['rank_ic']} "
        f"yesterday={test_metrics['yesterday_direction_accuracy']}"
    )
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
