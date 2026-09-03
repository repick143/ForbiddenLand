"""Run the standalone easy-tdx transaction-based order-flow research pipeline."""

from __future__ import annotations

import argparse
import json
from dataclasses import fields
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .aggregate import aggregate_transactions_to_bars
from .backtest import OrderFlowBacktestResult, run_order_flow_backtest
from .collector import EasyTdxCollector, EasyTdxOrderFlowSnapshot
from .config import ORDER_FLOW_VERSION, OrderFlowConfig
from .features import compute_order_flow_features, summarize_order_flow
from .normalize import normalize_bar_frame, normalize_transaction_frame, parse_symbol

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYMBOL = "SH:688183"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "order_flow_688183.json"
DEFAULT_FEATURES = PROJECT_ROOT / "reports" / "order_flow_688183_features.csv"
DEFAULT_TRANSACTIONS = PROJECT_ROOT / "reports" / "order_flow_688183_transactions.parquet"
DEFAULT_FACTOR = PROJECT_ROOT / "reports" / "order_flow_688183_factor.parquet"
DEFAULT_FACTOR_MANIFEST = PROJECT_ROOT / "reports" / "order_flow_688183_factor.manifest.json"


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


def _events(frame: pd.DataFrame, limit: int = 100) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    entry = frame.get("of_entry_signal", pd.Series(False, index=frame.index)).fillna(False)
    exit_ = frame.get("of_exit_signal", pd.Series(False, index=frame.index)).fillna(False)
    mask = entry.astype(bool) | exit_.astype(bool)
    columns = [
        "timestamp",
        "symbol",
        "of_entry_candidate",
        "of_exit_candidate",
        "of_entry_signal",
        "of_exit_signal",
        "delta",
        "delta_ratio",
        "relative_volume",
        "relative_transaction_volume",
        "close_location",
        "clv",
        "cvd",
        "session_cvd",
        "flow_price_divergence",
        "bullish_absorption",
        "bearish_absorption",
        "transaction_coverage",
        "order_flow_delta_ratio",
    ]
    available = [column for column in columns if column in frame.columns]
    return [
        {column: _json_value(value) for column, value in row.items()}
        for row in frame.loc[mask, available].head(limit).to_dict(orient="records")
    ]


def _trade_rows(
    backtest: OrderFlowBacktestResult, source_frame: pd.DataFrame
) -> list[dict[str, Any]]:
    trades = backtest.result.trades
    if trades.empty:
        return []
    key_to_timestamp = {
        int(key): timestamp
        for key, timestamp in zip(
            backtest.execution_frame["datetime"], pd.to_datetime(source_frame["timestamp"])
        )
    }
    rows: list[dict[str, Any]] = []
    for item in trades.to_dict(orient="records"):
        row = {key: _json_value(value) for key, value in item.items()}
        try:
            row["execution_timestamp"] = _json_value(key_to_timestamp.get(int(item["datetime"])))
        except (KeyError, TypeError, ValueError):
            row["execution_timestamp"] = None
        rows.append(row)
    return rows


def _fixture_data(
    symbol: str,
    days: int = 45,
    *,
    bar_minutes: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Build an offline fixture that exercises direction, coverage, and session boundaries."""

    _, _, qualified = parse_symbol(symbol)
    if (
        isinstance(bar_minutes, bool)
        or not isinstance(bar_minutes, int)
        or bar_minutes not in {1, 5, 15, 30, 60}
    ):
        raise ValueError("bar_minutes must be one of 1, 5, 15, 30, or 60")
    dates = pd.bdate_range("2026-01-05", periods=days)
    bar_rows: list[dict[str, Any]] = []
    tx_rows_by_day: dict[date, list[dict[str, Any]]] = {}
    for day_index, day_value in enumerate(dates):
        day = day_value.date()
        day_tx: list[dict[str, Any]] = []
        base = 30.0 + day_index * 0.08 + 0.8 * np.sin(day_index / 5.0)
        bars_per_session = 120 // bar_minutes
        bar_times = [
            (datetime.combine(day, time(9, 30)) + timedelta(minutes=bar_minutes * offset)).time()
            for offset in range(bars_per_session)
        ] + [
            (datetime.combine(day, time(13, 0)) + timedelta(minutes=bar_minutes * offset)).time()
            for offset in range(bars_per_session)
        ]
        for bar_index, bar_time in enumerate(bar_times):
            timestamp = pd.Timestamp.combine(day, bar_time)
            wave = 0.12 * np.sin((day_index * 48 + bar_index) / 4.0)
            open_price = base + wave
            # A few deterministic demand/supply bursts make signals visible without fitting them.
            demand_burst = day_index % 17 == 8 and bar_index in {12, 13, 14}
            supply_burst = day_index % 19 == 12 and bar_index in {30, 31}
            close_price = open_price + (
                0.20 if demand_burst else -0.20 if supply_burst else 0.03 * np.sin(bar_index)
            )
            spread = 0.18 if not (demand_burst or supply_burst) else 0.38
            high = max(open_price, close_price) + spread
            low = min(open_price, close_price) - spread
            bar_volume_lots = (
                700 + (500 if demand_burst or supply_burst else 0) + (bar_index % 5) * 20
            )
            bar_volume = float(bar_volume_lots * 100)
            bar_rows.append(
                {
                    "timestamp": timestamp,
                    "open": round(open_price, 4),
                    "high": round(high, 4),
                    "low": round(low, 4),
                    "close": round(close_price, 4),
                    "volume": bar_volume,
                    "amount": bar_volume * close_price,
                    "symbol": qualified,
                }
            )
            buy_lots = int(
                bar_volume_lots * (0.78 if demand_burst else 0.28 if supply_burst else 0.56)
            )
            sell_lots = int(
                bar_volume_lots * (0.18 if demand_burst else 0.68 if supply_burst else 0.40)
            )
            neutral_lots = max(0, bar_volume_lots - buy_lots - sell_lots)
            for offset_seconds, lots, flag in (
                (20, buy_lots, 0),
                (35, sell_lots, 1),
                (50, neutral_lots, 2),
            ):
                if lots > 0:
                    day_tx.append(
                        {
                            "time": (
                                datetime.combine(day, bar_time) + timedelta(seconds=offset_seconds)
                            ).time(),
                            "price": close_price,
                            "vol": lots,
                            "trade_count": max(1, lots // 10),
                            "bs_flag": flag,
                        }
                    )
        tx_rows_by_day[day] = day_tx

    raw_bars = pd.DataFrame(bar_rows)
    bars = normalize_bar_frame(raw_bars, symbol=qualified, bar_minutes=bar_minutes)
    normalized_tx: list[pd.DataFrame] = []
    for day, rows in tx_rows_by_day.items():
        normalized_tx.append(
            normalize_transaction_frame(
                pd.DataFrame(rows),
                trade_date=day,
                symbol=qualified,
                transaction_lot_size=100,
            )
        )
    transactions = pd.concat(normalized_tx, ignore_index=True)
    provenance = {
        "source": "synthetic fixture",
        "protocol": "none",
        "package_version": None,
        "host": None,
        "retrieved_at_utc": None,
        "market": qualified.split(":", 1)[0],
        "symbol": qualified,
        "period": f"{bar_minutes}MIN",
        "adjustment": "NONE",
        "bar_time": "start",
        "kline_volume_unit": "shares",
        "transaction_volume_unit": "protocol_lots",
        "transaction_lot_size": 100,
        "transaction_alignment": "floor",
        "bar_label_semantics": "left_endpoint",
        "transaction_dates": [day.strftime("%Y%m%d") for day in sorted(tx_rows_by_day)],
    }
    return bars, transactions, provenance


def build_feature_frame(
    bars: pd.DataFrame,
    transactions: pd.DataFrame,
    *,
    symbol: str,
    config: OrderFlowConfig | None = None,
) -> pd.DataFrame:
    """Run normalization-independent aggregation and feature generation."""

    settings = config or OrderFlowConfig()
    aggregated = aggregate_transactions_to_bars(
        bars,
        transactions,
        symbol=symbol,
        bar_minutes=settings.bar_minutes,
        large_trade_lots=settings.large_trade_lots,
        transaction_alignment=settings.transaction_alignment,
    )
    features = compute_order_flow_features(aggregated, settings)
    # Register and compute the custom factor through easy-tdx's public FactorEngine contract.  Keep
    # the import local so normalization-only callers do not need to import the optional factor API.
    from .easy_tdx_factor import EASY_TDX_FACTOR_NAME, compute_order_flow_factor

    features[EASY_TDX_FACTOR_NAME] = compute_order_flow_factor(features)
    features.attrs["easy_tdx_factor_name"] = EASY_TDX_FACTOR_NAME
    features.attrs.update(aggregated.attrs)
    return features


def build_report(
    backtest: OrderFlowBacktestResult,
    features: pd.DataFrame,
    *,
    symbol: str,
    config: OrderFlowConfig,
    provenance: dict[str, Any],
    quality: dict[str, Any] | None = None,
    factor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a reproducible report without embedding the large source frames."""

    quality_payload = quality or {}
    timestamps = pd.to_datetime(features["timestamp"], errors="coerce")
    source_range = {
        "start": timestamps.min().isoformat() if not timestamps.empty else None,
        "end": timestamps.max().isoformat() if not timestamps.empty else None,
    }
    try:
        import easy_tdx

        engine_version = str(getattr(easy_tdx, "__version__", "unknown"))
    except ImportError:
        engine_version = "unavailable"
    warnings = list(quality_payload.get("warnings", []))
    warnings.extend(
        [
            "transaction.bs_flag is an aggressor-side direction proxy, not an account or institution label",
            "transaction records may aggregate multiple executions and do not provide full Level-2 order events",
            "easy-tdx raw intraday performance metrics are retained but corrected metrics use one value per trading day",
            "single-symbol historical results are research output and require out-of-sample and independent-source validation",
        ]
    )
    trade_count = int(backtest.corrected_performance.get("total_trades", 0))
    if trade_count < 30:
        warnings.append(
            f"only {trade_count} completed trades; the configured 30-trade reference threshold is not met"
        )
    open_position = float(backtest.corrected_performance.get("open_position_shares", 0.0) or 0.0)
    if abs(open_position) > 1e-9:
        warnings.append(
            f"backtest ends with {open_position:g} shares open; end value includes mark-to-market PnL"
        )
    report = {
        "schema_version": 1,
        "order_flow_version": ORDER_FLOW_VERSION,
        "engine": "easy_tdx.backtest.BacktestEngine",
        "engine_version": engine_version,
        "strategy": "transaction_direction_proxy_long_only",
        "symbol": {"code": symbol.split(":", 1)[-1], "exchange": symbol.split(":", 1)[0]},
        "data": {
            **provenance,
            "analysis_range": source_range,
            "quality": quality_payload,
        },
        "parameters": config.as_dict(),
        "execution": {
            "engine": "easy-tdx OrderSimulator",
            "execution": config.execution,
            "position_mode": config.position_mode,
            "order_size": config.order_size,
            "position_fraction": config.position_fraction,
            "lot_size": config.lot_size,
            "commission_rate": config.commission_rate,
            "min_commission": config.min_commission,
            "stamp_tax_rate": config.stamp_tax_rate,
            "slippage_per_share": config.slippage_per_share,
            "t_plus_one": config.t_plus_one,
            "reject_policy": config.reject_policy,
            "auto_fees": config.auto_fees,
            "warmup_bars": config.warmup_bars,
            "signal_path": config.signal_path,
        },
        "metrics": {
            key: _json_value(value) for key, value in backtest.corrected_performance.items()
        },
        "engine_metrics_raw": {
            key: _json_value(value) for key, value in backtest.raw_engine_performance.items()
        },
        "signal_summary": summarize_order_flow(features),
        "events": _events(features),
        "trades": _trade_rows(backtest, features),
        "validation": {
            "warnings": list(dict.fromkeys(warnings)),
            "out_of_sample": False,
            "minimum_reference_trades": 30,
            "sample_sufficient_for_reference": trade_count >= 30,
        },
    }
    if factor is not None:
        report["factor"] = factor
    return report


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(_json_value(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return destination


def _write_frame(frame: pd.DataFrame, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.casefold() == ".parquet":
        try:
            frame.to_parquet(destination, index=False)
        except (ImportError, ModuleNotFoundError):
            fallback = destination.with_suffix(".csv")
            frame.to_csv(fallback, index=False)
            return fallback
    else:
        frame.to_csv(destination, index=False)
    return destination


def _optional_float(value: str) -> float | None:
    """Parse a nullable CLI float; ``none``/``off`` explicitly disables a filter."""

    if value.strip().casefold() in {"none", "null", "off", "disable", "disabled"}:
        return None
    return float(value)


def _parser() -> argparse.ArgumentParser:
    # Suppressed defaults let a JSON file provide a value without being overwritten by argparse's
    # default.  Runtime defaults come from OrderFlowConfig; collector/output defaults stay here.
    parser = argparse.ArgumentParser(
        description="easy-tdx transaction-direction order-flow proxy",
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument("--source", choices=("live", "fixture"), default="live")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="exchange:code, e.g. SH:688183")
    parser.add_argument(
        "--host", default=None, help="explicit MAC host; avoids host-selection races"
    )
    parser.add_argument("--start-date", default=None, help="YYYYMMDD")
    parser.add_argument("--end-date", default=None, help="YYYYMMDD")
    parser.add_argument("--config", type=Path, default=None, help="UTF-8 JSON strategy config")
    parser.add_argument("--bar-minutes", type=int, choices=(1, 5, 15, 30, 60))
    parser.add_argument(
        "--transaction-alignment",
        choices=("auto", "floor", "ceil"),
        help="map prints to left-endpoint bars, right-endpoint bars, or infer from labels",
    )
    parser.add_argument(
        "--transaction-days", type=int, default=120, help="0 means all available dates"
    )
    parser.add_argument("--transaction-max-rows", type=int, default=20_000)
    parser.add_argument("--transaction-page-size", type=int, default=1_000)
    parser.add_argument("--transaction-lot-size", type=int)
    parser.add_argument("--bar-count", type=int, default=30_000)
    parser.add_argument("--daily-count", type=int, default=2_000)
    parser.add_argument("--collector-timeout", type=float, default=20.0)
    parser.add_argument("--validation-days", type=int, default=3, help="1-minute cross-check days")
    parser.add_argument(
        "--warmup-sessions",
        type=int,
        default=None,
        help="extra sessions before a date window; 0 disables automatic warm-up",
    )
    parser.add_argument(
        "--fetch-quote",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="fetch a quote snapshot for the quality report",
    )
    parser.add_argument(
        "--fetch-auction",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="fetch the auction snapshot for the quality report",
    )
    parser.add_argument("--include-auction", action=argparse.BooleanOptionalAction)
    parser.add_argument("--include-after-hours", action=argparse.BooleanOptionalAction)
    parser.add_argument("--unknown-direction-policy", choices=("neutral", "drop", "error"))
    parser.add_argument("--cvd-reset-each-session", action=argparse.BooleanOptionalAction)
    parser.add_argument("--persistence-same-session", action=argparse.BooleanOptionalAction)
    parser.add_argument("--min-transaction-coverage", type=float)
    parser.add_argument("--max-transaction-coverage", type=_optional_float)
    parser.add_argument("--min-large-trade-share", type=float)
    parser.add_argument("--max-large-trade-share", type=_optional_float)
    parser.add_argument("--volume-baseline-sessions", type=int)
    parser.add_argument("--min-history-sessions", type=int)
    parser.add_argument("--large-trade-lots", type=int)
    parser.add_argument("--entry-delta-ratio", type=float)
    parser.add_argument("--entry-rvol", type=float)
    parser.add_argument("--entry-close-location", type=float)
    parser.add_argument("--entry-price-return", type=float)
    parser.add_argument("--entry-persistence", type=int)
    parser.add_argument("--entry-delta-zscore", type=_optional_float)
    parser.add_argument("--use-vwap-filter", action=argparse.BooleanOptionalAction)
    parser.add_argument("--entry-vwap-distance", type=float)
    parser.add_argument("--exit-delta-ratio", type=float)
    parser.add_argument("--exit-rvol", type=float)
    parser.add_argument("--exit-close-location", type=float)
    parser.add_argument("--exit-price-return", type=_optional_float)
    parser.add_argument("--exit-persistence", type=int)
    parser.add_argument("--exit-delta-zscore", type=_optional_float)
    parser.add_argument("--use-absorption-exit", action=argparse.BooleanOptionalAction)
    parser.add_argument("--absorption-rvol", type=float)
    parser.add_argument("--absorption-max-abs-return", type=float)
    parser.add_argument("--divergence-price-threshold", type=float)
    parser.add_argument("--use-vwap-exit-filter", action=argparse.BooleanOptionalAction)
    parser.add_argument("--exit-vwap-distance", type=float)
    parser.add_argument("--stop-loss-pct", type=float)
    parser.add_argument("--take-profit-pct", type=float)
    parser.add_argument("--min-hold-bars", type=int)
    parser.add_argument("--max-hold-bars", type=int, help="0 disables time exit")
    parser.add_argument("--cooldown-bars", type=int)
    parser.add_argument("--t-plus-one", action=argparse.BooleanOptionalAction)
    parser.add_argument("--flat-at-session-end", action=argparse.BooleanOptionalAction)
    parser.add_argument("--position-mode", choices=("full", "fixed", "percent"))
    parser.add_argument("--order-size", type=int)
    parser.add_argument("--position-fraction", type=float)
    parser.add_argument("--initial-cash", type=float)
    parser.add_argument("--commission-rate", type=float)
    parser.add_argument("--min-commission", type=float)
    parser.add_argument("--stamp-tax-rate", type=float)
    parser.add_argument("--slippage-per-share", type=float)
    parser.add_argument("--execution", choices=("next_open", "next_close"))
    parser.add_argument("--reject-policy", choices=("reduce", "skip"))
    parser.add_argument("--auto-fees", action=argparse.BooleanOptionalAction)
    parser.add_argument("--warmup-bars", type=int)
    parser.add_argument("--signal-path", choices=("auto", "vector", "loop"))
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--features", type=Path, default=DEFAULT_FEATURES)
    parser.add_argument("--transactions", type=Path, default=DEFAULT_TRANSACTIONS)
    parser.add_argument(
        "--factor-output",
        type=Path,
        default=DEFAULT_FACTOR,
        help="easy-tdx-compatible factor data path (.parquet or .csv)",
    )
    parser.add_argument(
        "--factor-manifest",
        type=Path,
        default=DEFAULT_FACTOR_MANIFEST,
        help="JSON contract/provenance manifest for the factor export",
    )
    parser.add_argument(
        "--factor-frequency",
        choices=("daily", "bar"),
        default="daily",
        help="factor export granularity; daily is directly usable by FactorAnalyzer",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> OrderFlowConfig:
    settings = OrderFlowConfig()
    config_path = getattr(args, "config", None)
    if config_path is not None:
        settings = OrderFlowConfig.from_json(config_path, base=settings)
    names = {field.name for field in fields(OrderFlowConfig)}
    overrides: dict[str, Any] = {name: getattr(args, name) for name in names if hasattr(args, name)}
    # CLI uses 0 as a convenient spelling for an unbounded holding period, while the config API
    # uses None so an omitted value remains distinguishable from an explicit disable.
    if "max_hold_bars" in overrides:
        overrides["max_hold_bars"] = (
            None if overrides["max_hold_bars"] == 0 else overrides["max_hold_bars"]
        )
    return OrderFlowConfig.from_mapping(overrides, base=settings)


def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = _config_from_args(args)
    _, _, qualified = parse_symbol(args.symbol)
    factor_frequency = getattr(args, "factor_frequency", "daily")
    factor_output = getattr(args, "factor_output", DEFAULT_FACTOR)
    factor_manifest = getattr(args, "factor_manifest", DEFAULT_FACTOR_MANIFEST)
    if args.source == "fixture":
        bars, transactions, provenance = _fixture_data(qualified, bar_minutes=settings.bar_minutes)
        quality = {"warnings": ["synthetic fixture; not market data"]}
    else:
        collector = EasyTdxCollector(host=args.host, timeout=args.collector_timeout)
        snapshot: EasyTdxOrderFlowSnapshot = collector.collect(
            qualified,
            config=settings,
            start_date=args.start_date,
            end_date=args.end_date,
            bar_count=args.bar_count,
            daily_count=args.daily_count,
            transaction_days=args.transaction_days,
            transaction_max_rows=args.transaction_max_rows,
            transaction_page_size=args.transaction_page_size,
            validation_days=args.validation_days,
            warmup_sessions=args.warmup_sessions,
            fetch_quote=args.fetch_quote,
            fetch_auction=args.fetch_auction,
        )
        bars, transactions, provenance, quality = (
            snapshot.bars,
            snapshot.transactions,
            snapshot.provenance,
            snapshot.quality,
        )
    features = build_feature_frame(bars, transactions, symbol=qualified, config=settings)
    # Record the resolved convention from aggregation so an ``auto`` run is reproducible.
    provenance = dict(provenance)
    resolved_alignment = features.attrs.get("transaction_alignment")
    if resolved_alignment is None and "transaction_alignment" in features.columns:
        values = features["transaction_alignment"].dropna().astype(str).unique().tolist()
        resolved_alignment = values[0] if values else settings.transaction_alignment
    provenance["transaction_alignment"] = resolved_alignment
    provenance["transaction_alignment_requested"] = settings.transaction_alignment
    if "is_warmup" in features.columns:
        # Warm-up bars contribute to causal baselines but must not contribute trades or performance
        # for a user-selected date window.
        features = features.loc[~features["is_warmup"].astype(bool)].reset_index(drop=True)
    if features.empty:
        raise ValueError("no analysis bars remain after applying the date/warm-up window")
    from .easy_tdx_factor import (
        build_easy_tdx_factor_frame,
        factor_definition,
        save_easy_tdx_factor_bundle,
    )

    factor_frame = build_easy_tdx_factor_frame(
        features,
        frequency=factor_frequency,
        symbol=qualified,
    )
    factor_bundle = save_easy_tdx_factor_bundle(
        factor_frame,
        factor_output,
        manifest_path=factor_manifest,
        provenance=provenance,
        frequency=factor_frequency,
    )
    backtest = run_order_flow_backtest(features, config=settings)
    factor_report = {
        **factor_definition(),
        "frequency": factor_frequency,
        "output": str(factor_bundle.data_path),
        "manifest": str(factor_bundle.manifest_path),
    }
    report = build_report(
        backtest,
        features,
        symbol=qualified,
        config=settings,
        provenance=provenance,
        quality=quality,
        factor=factor_report,
    )
    write_report(report, args.report)
    features_path = _write_frame(features, args.features)
    transactions_path = _write_frame(transactions, args.transactions)
    print(
        json.dumps(
            {
                "report": str(args.report),
                "features": str(features_path),
                "transactions": str(transactions_path),
                "factor": str(factor_bundle.data_path),
                "factor_manifest": str(factor_bundle.manifest_path),
                "symbol": qualified,
                "bars": len(features),
                "transaction_rows": len(transactions),
                "metrics": report["metrics"],
                "signal_summary": report["signal_summary"],
                "warnings": report["validation"]["warnings"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DEFAULT_FACTOR",
    "DEFAULT_FACTOR_MANIFEST",
    "DEFAULT_SYMBOL",
    "build_feature_frame",
    "build_report",
    "main",
    "run",
]
