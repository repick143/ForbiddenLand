"""Prediction and backtest CLI for the XGBoost direction baseline.

Run from the repository root with the project's virtual environment:

    ./.venv/bin/python -m research.predict.baseline.predict \
      --model-cache data/cache/predict_model.ubj

The CLI loads a model produced by ``train.py``, fetches fresh daily bars through the project
provider, scores the window after ``--valid-end``, writes the signal table, and executes the
signals in AKQuant with explicit A-share costs and T+1 rules. The model's embedded training
fingerprint is reported for provenance but is not enforced, so a trained model may be used to
score a different universe or window.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
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
DEFAULT_PREDICT_REPORT = PROJECT_ROOT / "reports" / "predict_baseline_predict.json"
DEFAULT_SIGNALS = PROJECT_ROOT / "data" / "processed" / "predict_signals.csv"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score and backtest a trained XGBoost next-day direction model."
    )
    parser.add_argument("--symbols", nargs="+", default=list(data_mod.DEFAULT_SYMBOLS))
    parser.add_argument("--start-date", default=data_mod.DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=data_mod.DEFAULT_END_DATE)
    parser.add_argument("--adjust", default=data_mod.DEFAULT_ADJUST)
    parser.add_argument(
        "--valid-end",
        default="20251231",
        help="prediction/backtest window starts the trading day after this cutoff",
    )
    parser.add_argument(
        "--model-cache",
        required=True,
        help="path to a trained XGBoost model file produced by research.predict.baseline.train",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="override the model-embedded validation threshold (default 0.50 when absent)",
    )
    parser.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    parser.add_argument("--report", default=str(DEFAULT_PREDICT_REPORT))
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


def _latest_predictions(
    signals: pd.DataFrame,
    threshold: float,
) -> dict[str, dict[str, Any]]:
    """Per-symbol newest bar: model probability and the tentative next-open direction."""

    result: dict[str, dict[str, Any]] = {}
    for symbol, group in signals.groupby("symbol", sort=False):
        last = group.sort_values("timestamp").iloc[-1]
        prob = last["prob"]
        has_prob = pd.notna(prob)
        result[str(symbol)] = {
            "timestamp": pd.Timestamp(last["timestamp"]).date().isoformat(),
            "prob": float(prob) if has_prob else None,
            "signal": int(prob >= threshold) if has_prob else None,
            "note": (
                "tentative next-open direction; tradability re-checked next day "
                "(limit-up, missing bar)"
            ),
        }
    return result


def main(argv: list[str] | None = None) -> int:
    """Prediction CLI: load model -> fetch -> score -> signals -> AKQuant backtest."""

    args = _parse_args(argv)
    symbols = tuple(args.symbols)
    model_path = Path(args.model_cache)
    loaded = cache_mod.load_model_file(model_path)
    if loaded.model is None:
        print(f"predict: cannot load model {model_path} ({loaded.reason})")
        return 1
    model = loaded.model

    split = train_mod.SplitDates(train_end=_date(args.valid_end), valid_end=_date(args.valid_end))
    dataset = train_mod.build_dataset(symbols, args.start_date, args.end_date, args.adjust, split)
    features = dataset.features
    threshold = (
        args.threshold
        if args.threshold is not None
        else loaded.threshold
        if loaded.threshold is not None
        else 0.50
    )

    signals_all = dataset.labeled[dataset.test_mask].copy()
    signals_scored = train_mod.predict_probabilities(model, signals_all, features)
    signals_scored["signal"] = (
        (signals_scored["prob"] >= threshold) & signals_scored["eligible"]
    ).astype(int)
    test_scored = train_mod.predict_probabilities(model, dataset.test_df, features)
    metrics = train_mod.evaluate(test_scored, threshold)

    test_frame = dataset.batch.frame[
        pd.to_datetime(dataset.batch.frame["timestamp"]).dt.date > split.valid_end
    ]
    signal_map = bt.build_signal_map(signals_scored)
    backtest_result = bt.run_backtest(test_frame, signal_map, symbols)
    backtest_metrics = bt.summarize_backtest(backtest_result)
    buy_and_hold = bt.buy_and_hold_returns(test_frame, symbols)

    signals_path = Path(args.signals)
    report_path = Path(args.report)
    signals_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    signals_scored.to_csv(signals_path, index=False)

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
            "scoring_start": (split.valid_end + timedelta(days=1)).isoformat(),
        },
        "provenance": {
            "source": dataset.batch.source,
            "storage": dataset.batch.storage,
            "retrieved_at_utc": dataset.batch.retrieved_at_utc.isoformat(),
        },
        "features": features,
        "model": {
            "path": str(model_path),
            "training_fingerprint": loaded.fingerprint,
            "training_params": loaded.params,
            "embedded_threshold": loaded.threshold,
        },
        "threshold": threshold,
        "splits": {
            "valid_end": args.valid_end,
            "scored_rows": len(signals_scored),
            "eligible_rows": int(signals_scored["eligible"].sum()),
            "excluded_rows": int((~signals_scored["eligible"]).sum()),
        },
        "metrics": {"scoring_window": metrics},
        "latest": _latest_predictions(signals_scored, threshold),
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
    print(f"model: {model_path} (fingerprint={loaded.fingerprint})")
    print(f"provenance: source={dataset.batch.source} storage={dataset.batch.storage}")
    print(f"rows scored: {len(signals_scored)} eligible={int(signals_scored['eligible'].sum())}")
    print(f"threshold: {threshold:.2f}")
    print(
        "scoring window: "
        f"auc={metrics['auc']} acc={metrics['accuracy']} "
        f"rank_ic={metrics['rank_ic']} "
        f"yesterday={metrics['yesterday_direction_accuracy']}"
    )
    print(
        f"backtest: {backtest_metrics.get('total_return_pct', 'n/a')}% "
        f"trades={backtest_metrics.get('closed_trade_count', 'n/a')}"
    )
    latest = report["latest"]
    latest_lines = []
    for key, value in latest.items():
        line = f"{key}={value['signal']}"
        if value["prob"] is not None:
            line += f" ({value['prob']:.2f})"
        latest_lines.append(line)
    print("latest: " + "; ".join(latest_lines))
    print(f"signals: {signals_path}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
