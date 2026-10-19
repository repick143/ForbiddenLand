"""End-to-end prediction baseline: fetch -> features -> labels -> train -> backtest.

Run from the repository root with the project's virtual environment:

    ./.venv/bin/python -m research.predict.run

The default path fetches remote AkShare daily bars through the project provider, trains an
XGBoost next-day open-to-close direction classifier with walk-forward validation, and executes
the resulting signals in AKQuant with explicit A-share costs and T+1 rules.
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
from . import features as feat_mod
from . import labels as lbl_mod
from . import train as train_mod

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "predict_baseline.json"
DEFAULT_SIGNALS = PROJECT_ROOT / "data" / "processed" / "predict_signals.csv"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=list(data_mod.DEFAULT_SYMBOLS))
    parser.add_argument("--start-date", default=data_mod.DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=data_mod.DEFAULT_END_DATE)
    parser.add_argument("--adjust", default=data_mod.DEFAULT_ADJUST)
    parser.add_argument("--train-end", default="20231231")
    parser.add_argument("--valid-end", default="20251231")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="override the validation-selected classification threshold",
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

    batch = data_mod.fetch_bars(symbols, args.start_date, args.end_date, args.adjust)
    feature_frame = feat_mod.compute_features(batch.frame)
    labeled = lbl_mod.build_labels(feature_frame)

    train_mask, valid_mask, test_mask = train_mod.split_by_date(labeled, split)
    features = list(feat_mod.FEATURE_COLUMNS)

    train_df = labeled[train_mask & labeled["eligible"]].dropna(subset=features).copy()
    valid_df = labeled[valid_mask & labeled["eligible"]].dropna(subset=features).copy()
    test_df = labeled[test_mask & labeled["eligible"]].dropna(subset=features).copy()

    model, params = train_mod.fit_classifier(train_df, valid_df, features, seed=args.seed)
    train_scored = train_mod.predict_probabilities(model, train_df, features)
    valid_scored = train_mod.predict_probabilities(model, valid_df, features)
    test_scored = train_mod.predict_probabilities(model, test_df, features)

    threshold = (
        args.threshold if args.threshold is not None else train_mod.select_threshold(valid_scored)
    )

    signals_all = labeled[test_mask].copy()
    signals_scored = train_mod.predict_probabilities(model, signals_all, features)
    signals_scored["signal"] = (
        (signals_scored["prob"] >= threshold) & signals_scored["eligible"]
    ).astype(int)
    signal_map = bt.build_signal_map(signals_scored)

    test_frame = batch.frame[pd.to_datetime(batch.frame["timestamp"]).dt.date > split.valid_end]
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
        "window": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "adjust": args.adjust,
        },
        "provenance": {
            "source": batch.source,
            "storage": batch.storage,
            "retrieved_at_utc": batch.retrieved_at_utc.isoformat(),
        },
        "features": features,
        "model": {
            "seed": args.seed,
            "params": {key: _json_value(value) for key, value in params.items()},
            "best_iteration": _json_value(getattr(model, "best_iteration", None)),
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
    print(f"provenance: source={batch.source} storage={batch.storage}")
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
