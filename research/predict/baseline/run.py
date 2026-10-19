"""Combined prediction-baseline CLI: train and backtest in one pass.

Run from the repository root with the project's virtual environment:

    ./.venv/bin/python -m research.predict.baseline.run

This is the convenience entry point that trains and backtests in a single run and writes one JSON
report. The same steps are split into standalone CLIs: ``research.predict.baseline.train`` (train
and persist the model) and ``research.predict.baseline.predict`` (load a model, score, and
backtest).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import akquant
import pandas as pd
import xgboost as xgb

from . import backtest as bt
from . import data as data_mod
from . import model_cache as cache_mod
from . import train as train_mod

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "predict_baseline.json"
DEFAULT_SIGNALS = PROJECT_ROOT / "data" / "processed" / "predict_signals.csv"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=list(data_mod.DEFAULT_SYMBOLS))
    parser.add_argument("--start-date", default=data_mod.DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=data_mod.DEFAULT_END_DATE)
    parser.add_argument("--adjust", default=data_mod.DEFAULT_ADJUST)
    parser.add_argument(
        "--source",
        default="easy-tdx",
        help="ecom-data source name for daily bars: easy-tdx (default, fast) or akshare",
    )
    parser.add_argument(
        "--bar-count",
        type=int,
        default=data_mod.DEFAULT_BAR_COUNT,
        help="maximum bars requested per symbol (easy-tdx QFQ is faster with 2500)",
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
        help="opt-in path to an XGBoost model file (.ubj/.json); without it the baseline "
        "trains from scratch on every run",
    )
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    return parser.parse_args(argv)


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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    symbols = tuple(args.symbols)
    split = train_mod.SplitDates(train_end=_date(args.train_end), valid_end=_date(args.valid_end))

    dataset = train_mod.build_dataset(
        symbols,
        args.start_date,
        args.end_date,
        args.adjust,
        split,
        source=data_mod.get_source(args.source),
        count=args.bar_count,
    )
    features = dataset.features
    labeled = dataset.labeled
    train_df = dataset.train_df
    valid_df = dataset.valid_df
    test_df = dataset.test_df

    cache_path = Path(args.model_cache) if args.model_cache else None
    cache_fingerprint = (
        cache_mod.build_fingerprint(
            symbols,
            args.start_date,
            args.end_date,
            args.adjust,
            args.train_end,
            args.valid_end,
            args.seed,
            features,
            train_mod.DEFAULT_PARAMS,
            args.source,
            args.bar_count,
        )
        if cache_path is not None
        else None
    )
    cache_mode: str | None = None
    loaded: cache_mod.CacheLoad | None = None
    if cache_path is not None:
        loaded = cache_mod.load_cached(cache_path, cache_fingerprint)
        if loaded.model is not None:
            cache_mode = "loaded"
            print(f"model cache: hit {cache_path}")
        else:
            print(f"model cache: miss ({loaded.reason}) {cache_path}; training")
    if cache_path is None or loaded is None or loaded.model is None:
        model, params = train_mod.fit_classifier(train_df, valid_df, features, seed=args.seed)
        if cache_path is not None:
            valid_scored_for_cache = train_mod.predict_probabilities(model, valid_df, features)
            threshold_for_cache = (
                args.threshold
                if args.threshold is not None
                else train_mod.select_threshold(valid_scored_for_cache)
            )
            cache_mod.save_cached(
                model,
                cache_path,
                cache_fingerprint,
                params,
                threshold=threshold_for_cache,
            )
            cache_mode = "saved"
            print(f"model cache: saved {cache_path}")
    train_scored = train_mod.predict_probabilities(model, train_df, features)
    valid_scored = train_mod.predict_probabilities(model, valid_df, features)
    test_scored = train_mod.predict_probabilities(model, test_df, features)

    threshold = (
        args.threshold if args.threshold is not None else train_mod.select_threshold(valid_scored)
    )

    signals_all = labeled[dataset.test_mask].copy()
    signals_scored = train_mod.predict_probabilities(model, signals_all, features)
    signals_scored["signal"] = (
        (signals_scored["prob"] >= threshold) & signals_scored["eligible"]
    ).astype(int)
    signal_map = bt.build_signal_map(signals_scored)

    test_frame = dataset.batch.frame[
        pd.to_datetime(dataset.batch.frame["timestamp"]).dt.date > split.valid_end
    ]
    backtest_result = bt.run_backtest(test_frame, signal_map, symbols)
    backtest_metrics = bt.summarize_backtest(backtest_result)
    buy_and_hold = bt.buy_and_hold_returns(test_frame, symbols)

    report_path = Path(args.report)
    signals_path = Path(args.signals)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    signals_path.parent.mkdir(parents=True, exist_ok=True)
    signals_scored.to_csv(signals_path, index=False)

    exclusion_counts = labeled.loc[~labeled["eligible"], "exclude_reason"].value_counts().to_dict()
    report: dict[str, Any] = {
        "strategy": "xgboost_next_day_direction",
        "target": "sign(open_{T+1} -> close_{T+1})",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "engine": {
            "akquant": getattr(akquant, "__version__", "unknown"),
            "xgboost": getattr(xgb, "__version__", "unknown"),
        },
        "universe": list(symbols),
        "skipped": {str(k): str(v) for k, v in dataset.batch.skipped.items()},
        "window": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "adjust": args.adjust,
            "source": args.source,
            "bar_count": args.bar_count,
        },
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
            "fingerprint": cache_fingerprint,
        },
        "splits": {
            "train_end": args.train_end,
            "valid_end": args.valid_end,
            "train_rows": len(train_df),
            "valid_rows": len(valid_df),
            "test_rows": len(test_df),
            "excluded_rows": int((~labeled["eligible"]).sum()),
            "exclusion_reasons": {str(k): int(v) for k, v in exclusion_counts.items()},
        },
        "metrics": {
            "threshold": threshold,
            "train": train_mod.evaluate(train_scored, threshold),
            "validation": train_mod.evaluate(valid_scored, threshold),
            "test": train_mod.evaluate(test_scored, threshold),
        },
        "backtest": {
            "initial_cash": bt.INITIAL_CASH,
            "order_size": bt.ORDER_SIZE,
            "metrics": {key: _json_value(value) for key, value in backtest_metrics.items()},
            "buy_and_hold": {str(k): _json_value(v) for k, v in buy_and_hold.items()},
        },
        "outputs": {
            "signals": str(signals_path),
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
    print(f"rows: train={len(train_df)} valid={len(valid_df)} test={len(test_df)}")
    print(f"threshold: {threshold:.2f}")
    test_metrics = report["metrics"]["test"]
    print(
        "test: "
        f"auc={test_metrics['auc']} acc={test_metrics['accuracy']} "
        f"rank_ic={test_metrics['rank_ic']} "
        f"yesterday={test_metrics['yesterday_direction_accuracy']}"
    )
    print(
        f"backtest: {backtest_metrics.get('total_return_pct', 'n/a')}% "
        f"trades={backtest_metrics.get('closed_trade_count', 'n/a')}"
    )
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
