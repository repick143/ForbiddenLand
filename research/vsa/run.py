"""Run the 生益电子 daily VSA indicator and AKQuant backtest demo.

The default data path is the project's ``AkShareMarketProvider``.  ``--source fixture`` is an
offline deterministic path for tests and development; it is not market data and must not be used
to draw investment conclusions.
"""

from __future__ import annotations

import argparse
import json
from calendar import monthrange
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import akquant
import numpy as np
import pandas as pd

from forbiddenland.config import CompatibilityConfig
from forbiddenland.domain.market import MarketDataResult, MarketQuery
from forbiddenland.infrastructure.market_data.akshare_provider import AkShareMarketProvider

from .features import VSAConfig, compute_vsa_features
from .rules import apply_vsa_rules, summarize_vsa_events
from .strategy import VSA_ORDER_SIZE, VSAStrategy

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_SYMBOL = "688183"
DEMO_NAME = "生益电子"
DEFAULT_LOOKBACK_MONTHS = 3
# Kept for callers that explicitly reproduce the original demo window.  Runtime defaults use the
# rolling window helpers below instead of these legacy aliases.
DEFAULT_START_DATE = "20240101"
DEFAULT_END_DATE = "20241231"
DEFAULT_ADJUST = "qfq"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "vsa_688183.json"
DEFAULT_INDICATORS = PROJECT_ROOT / "reports" / "vsa_688183_indicators.json"
DEFAULT_INITIAL_CASH = 100_000.0
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

_REQUIRED_COLUMNS = frozenset({"timestamp", "open", "high", "low", "close", "volume"})


@dataclass(frozen=True, slots=True)
class VSAFetchBatch:
    """Normalized source bars and provenance for one demo symbol."""

    frame: pd.DataFrame
    source: str
    storage: str
    retrieved_at_utc: datetime
    backend: str
    cache_hit: bool = False


def _parse_date(value: str) -> date:
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"date must use YYYYMMDD format: {value!r}")
    try:
        return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    except ValueError as exc:
        raise ValueError(f"date must use YYYYMMDD format: {value!r}") from exc


def recent_date_window(
    as_of: date | datetime | None = None,
    months: int = DEFAULT_LOOKBACK_MONTHS,
) -> tuple[str, str]:
    """Return a rolling calendar-month window as ``YYYYMMDD`` strings.

    The end date defaults to the current Shanghai calendar date.  Calendar arithmetic is used
    instead of a fixed number of days, and month-end dates are clamped (for example, May 31 minus
    three months becomes February 29 in a leap year).  ``as_of`` keeps the default reproducible in
    tests and in scheduled callers.
    """

    if isinstance(as_of, datetime):
        end = as_of.date()
    elif isinstance(as_of, date):
        end = as_of
    elif as_of is None:
        end = datetime.now(_SHANGHAI_TZ).date()
    else:
        raise TypeError("as_of must be a date, datetime, or None")
    if isinstance(months, bool) or not isinstance(months, int) or months < 1:
        raise ValueError("months must be a positive integer")

    month_index = end.year * 12 + (end.month - 1) - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    start = date(year, month, min(end.day, monthrange(year, month)[1]))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def resolve_date_window(
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    as_of: date | datetime | None = None,
) -> tuple[str, str]:
    """Resolve optional CLI dates, defaulting to three calendar months ending at ``as_of``."""

    if end_date is not None:
        end = _parse_date(end_date)
    elif isinstance(as_of, datetime):
        end = as_of.date()
    elif isinstance(as_of, date):
        end = as_of
    elif as_of is None:
        end = datetime.now(_SHANGHAI_TZ).date()
    else:
        raise TypeError("as_of must be a date, datetime, or None")

    default_start, default_end = recent_date_window(end)
    resolved_start = start_date if start_date is not None else default_start
    resolved_end = end_date if end_date is not None else default_end
    if _parse_date(resolved_start) > _parse_date(resolved_end):
        raise ValueError("start_date must not be later than end_date")
    return resolved_start, resolved_end


def _frame_from_market_result(result: MarketDataResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "amount": bar.amount,
                "change": bar.change,
                "change_percent": bar.change_percent,
                "turnover_rate": bar.turnover_rate,
                "symbol": bar.symbol,
            }
            for bar in result.bars
        ]
    )


def normalize_demo_frame(frame: pd.DataFrame, symbol: str = DEMO_SYMBOL) -> pd.DataFrame:
    """Validate and normalize one OHLCV frame before feature generation."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    if frame.empty:
        raise ValueError(f"no bars returned for {symbol}")

    data = frame.copy()
    if "timestamp" not in data.columns:
        for candidate in ("date", "datetime", "日期"):
            if candidate in data.columns:
                data["timestamp"] = data[candidate]
                break
    missing = sorted(_REQUIRED_COLUMNS.difference(data.columns))
    if missing:
        raise ValueError(f"OHLCV frame is missing columns: {', '.join(missing)}")
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError(f"OHLCV frame for {symbol} contains invalid timestamps")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    numeric = data[["open", "high", "low", "close", "volume"]]
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError(f"OHLCV frame for {symbol} contains missing or non-finite values")
    if (data["volume"] < 0).any():
        raise ValueError(f"OHLCV frame for {symbol} contains negative volume")
    if (
        (data["high"] < data[["open", "close"]].max(axis=1)).any()
        or (data["low"] > data[["open", "close"]].min(axis=1)).any()
        or (data["high"] < data["low"]).any()
    ):
        raise ValueError(f"OHLCV frame for {symbol} contains invalid OHLC ordering")

    if "symbol" not in data.columns:
        data["symbol"] = str(symbol)
    else:
        symbols = data["symbol"].astype("string").str.strip()
        if symbols.isna().any() or symbols.eq("").any():
            raise ValueError(f"OHLCV frame for {symbol} contains an empty symbol")
        data["symbol"] = symbols
    data["symbol"] = data["symbol"].map(
        lambda value: value.zfill(6) if value.isdigit() and len(value) <= 6 else value
    )
    data = data.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
    if data.duplicated(["symbol", "timestamp"]).any():
        raise ValueError(f"OHLCV frame for {symbol} contains duplicate timestamps")
    return data


def fetch_demo_data_with_metadata(
    start_date: str | None = None,
    end_date: str | None = None,
    adjust: str = DEFAULT_ADJUST,
    *,
    provider: AkShareMarketProvider | None = None,
) -> VSAFetchBatch:
    """Fetch 生益电子 through the configured project provider and retain provenance."""

    resolved_start, resolved_end = resolve_date_window(start_date, end_date)
    start = _parse_date(resolved_start)
    end = _parse_date(resolved_end)
    configured = provider or AkShareMarketProvider(CompatibilityConfig.from_env())
    query = MarketQuery(
        symbol=DEMO_SYMBOL,
        start_date=start,
        end_date=end,
        adjust=adjust,
    )
    result = configured.fetch_history(query)
    return VSAFetchBatch(
        frame=normalize_demo_frame(_frame_from_market_result(result), DEMO_SYMBOL),
        source=result.source,
        storage=result.storage,
        retrieved_at_utc=result.retrieved_at_utc,
        backend=result.backend,
        cache_hit=result.cache_hit,
    )


def fetch_demo_data(
    start_date: str | None = None,
    end_date: str | None = None,
    adjust: str = DEFAULT_ADJUST,
    *,
    provider: AkShareMarketProvider | None = None,
) -> pd.DataFrame:
    """Fetch only the normalized frame; metadata is available from the sibling function."""

    return fetch_demo_data_with_metadata(start_date, end_date, adjust, provider=provider).frame


def build_fixture() -> pd.DataFrame:
    """Build a deterministic 100-bar 生益电子-shaped fixture with varied VSA events.

    The fixture is intentionally synthetic.  Its event rows make the indicator and execution
    plumbing observable in offline tests, while the report labels it as non-market data.
    """

    dates = pd.bdate_range("2024-01-02", periods=100)
    closes: list[float] = []
    for index in range(len(dates)):
        if index < 25:
            close = 35.0 - 0.30 * index
        elif index < 45:
            close = 27.5 + 0.10 * (index - 25)
        elif index < 60:
            close = 29.5 - 0.16 * (index - 45)
        elif index < 70:
            close = 27.1 + 0.30 * (index - 60)
        elif index < 85:
            close = 30.1 + 0.35 * (index - 70)
        else:
            close = 35.35 + 0.05 * (index - 85)
        closes.append(round(close + 0.04 * np.sin(index * 0.7), 4))

    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(dates):
        previous = closes[index - 1] if index else closes[index] - 0.15
        open_price = previous + 0.03 * np.cos(index * 0.6)
        close = closes[index]
        spread = 0.75 + 0.05 * (index % 3)
        high = max(open_price, close) + spread * 0.55
        low = min(open_price, close) - spread * 0.45
        volume = 1_000.0 + 35.0 * np.sin(index * 0.4)
        rows.append(
            {
                "timestamp": timestamp,
                "open": round(open_price, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": round(volume, 4),
                "symbol": DEMO_SYMBOL,
            }
        )

    def replace(
        index: int, *, open_: float, close_: float, high: float, low: float, volume: float
    ) -> None:
        rows[index].update(
            {
                "open": open_,
                "close": close_,
                "high": high,
                "low": low,
                "volume": volume,
            }
        )

    # Candidate rows and their next-bar directional confirmations.
    replace(30, open_=28.40, close_=28.20, high=28.45, low=27.90, volume=280.0)
    replace(31, open_=28.20, close_=28.65, high=28.95, low=28.00, volume=1_050.0)
    replace(45, open_=29.60, close_=28.80, high=30.00, low=26.80, volume=2_600.0)
    replace(46, open_=28.80, close_=29.45, high=29.70, low=28.50, volume=1_100.0)
    replace(60, open_=27.10, close_=27.00, high=27.15, low=26.50, volume=260.0)
    replace(61, open_=26.95, close_=27.55, high=27.80, low=26.85, volume=1_020.0)
    replace(70, open_=30.10, close_=30.35, high=30.45, low=29.95, volume=270.0)
    replace(71, open_=30.35, close_=29.95, high=30.45, low=29.70, volume=1_050.0)
    replace(85, open_=35.10, close_=35.35, high=37.00, low=34.90, volume=2_700.0)
    replace(86, open_=35.35, close_=34.70, high=35.55, low=34.45, volume=1_100.0)
    return normalize_demo_frame(pd.DataFrame(rows), DEMO_SYMBOL)


def generate_vsa_frame(
    frame: pd.DataFrame,
    config: VSAConfig | None = None,
) -> pd.DataFrame:
    """Normalize bars and append deterministic VSA features plus rule metadata."""

    normalized = normalize_demo_frame(frame, DEMO_SYMBOL)
    return apply_vsa_rules(compute_vsa_features(normalized, config), config)


def _encode_for_akquant(frame: pd.DataFrame) -> pd.DataFrame:
    """Encode missing computed values as the explicit zero sentinel expected by AKQuant extras."""

    data = frame.copy()
    # AKQuant's normalizer maps NaN numeric extras to zero.  Doing this on a private execution copy
    # makes that boundary explicit; the report still uses the original missing-aware feature frame.
    numeric_columns = data.select_dtypes(include=[np.number, "bool"]).columns
    for column in numeric_columns:
        if column not in {"open", "high", "low", "close", "volume"}:
            data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0.0)
    return data


def run_backtest(
    frame: pd.DataFrame,
    *,
    initial_cash: float = DEFAULT_INITIAL_CASH,
) -> akquant.BacktestResult:
    """Run the VSA strategy with explicit costs, next-open fills, and T+1."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    if frame.empty:
        raise ValueError("frame must contain at least one VSA bar")
    required = _REQUIRED_COLUMNS | {"symbol"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("VSA frame is missing columns: " + ", ".join(missing))
    symbols = frame["symbol"].astype("string").str.strip()
    if symbols.isna().any() or symbols.eq("").any():
        raise ValueError("VSA frame contains an empty symbol")
    data = _encode_for_akquant(frame)
    symbol_list = [str(symbol) for symbol in data["symbol"].drop_duplicates().tolist()]
    return akquant.run_backtest(
        data=data,
        strategy=VSAStrategy,
        symbols=symbol_list,
        initial_cash=float(initial_cash),
        commission_rate=0.0003,
        stamp_tax_rate=0.001,
        transfer_fee_rate=0.00001,
        min_commission=0.0,
        slippage={"type": "percent", "value": 0.0002},
        lot_size=VSA_ORDER_SIZE,
        t_plus_one=True,
        history_depth=1,
        warmup_period=0,
        fill_policy=akquant.NextOpen(),
        timezone="Asia/Shanghai",
        show_progress=False,
        indicator_recorder=akquant.IndicatorRecorder(),
    )


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    missing = pd.isna(value)
    if isinstance(missing, (bool, np.bool_)) and bool(missing):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _metrics_from_result(result: akquant.BacktestResult) -> dict[str, Any]:
    names = (
        "start_time",
        "end_time",
        "total_bars",
        "closed_trade_count",
        "execution_count",
        "initial_market_value",
        "end_market_value",
        "total_pnl",
        "total_return_pct",
        "max_drawdown_pct",
        "win_rate",
        "total_commission",
        "avg_trade_bars",
    )
    metrics_frame = result.metrics_df
    return {
        name: _json_value(metrics_frame.loc[name, "value"])
        for name in names
        if name in metrics_frame.index
    }


def _event_rows(frame: pd.DataFrame, limit: int = 100) -> list[dict[str, Any]]:
    columns = (
        "timestamp",
        "symbol",
        "vsa_candidate",
        "vsa_candidate_code",
        "vsa_confirmation_status",
        "vsa_confirmed_signal",
        "vsa_signal_name",
        "vsa_reference_timestamp",
        "vsa_volume_ratio",
        "vsa_spread_ratio",
        "vsa_clv",
        "vsa_stop_price",
        "vsa_target_price",
    )
    available = [column for column in columns if column in frame.columns]
    events = frame.loc[
        frame.get("vsa_candidate_code", pd.Series(0, index=frame.index)).ne(0)
        | frame.get("vsa_confirmed_signal", pd.Series(0, index=frame.index)).ne(0),
        available,
    ].head(limit)
    return [
        {column: _json_value(value) for column, value in row.items()}
        for row in events.to_dict(orient="records")
    ]


def build_report(
    result: akquant.BacktestResult,
    *,
    features: pd.DataFrame | None = None,
    source: str = "AkShare remote provider",
    storage: str = "remote response",
    backend: str = "remote",
    cache_hit: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    adjust: str | None = None,
    retrieved_at_utc: str | None = None,
    config: VSAConfig | None = None,
) -> dict[str, Any]:
    """Build a reproducible, research-only JSON report."""

    settings = config or VSAConfig()
    feature_summary = summarize_vsa_events(features) if features is not None else None
    trades = result.trades_df
    closed_trade_count = len(trades)
    report: dict[str, Any] = {
        "engine": "akquant",
        "engine_version": getattr(akquant, "__version__", "unknown"),
        "strategy": "daily_vsa_long_only",
        "symbol": {"code": DEMO_SYMBOL, "name": DEMO_NAME},
        "data": {
            "source": source,
            "storage": storage,
            "backend": backend,
            "cache_hit": bool(cache_hit),
            "frequency": "daily",
        },
        "parameters": settings.as_dict(),
        "execution": {
            "entry_fill": "NextOpen",
            "t_plus_one": True,
            "order_size": VSA_ORDER_SIZE,
            "commission_rate": 0.0003,
            "stamp_tax_rate": 0.001,
            "transfer_fee_rate": 0.00001,
            "slippage": {"type": "percent", "value": 0.0002},
            "stop_loss": "candidate low minus configured buffer",
            "invalidation": "one-bar confirmation failure or max holding period",
        },
        "metrics": _metrics_from_result(result),
        "validation": {
            "closed_trade_count": closed_trade_count,
            "minimum_reference_trades": 30,
            "sample_sufficient_for_reference": closed_trade_count >= 30,
            "out_of_sample": False,
            "warnings": [
                "VSA thresholds are conventional starting values, not optimized parameters.",
                "A single-symbol demo and its synthetic fixture are not sufficient to establish an edge.",
                "Research output only; not investment advice.",
            ],
        },
    }
    if feature_summary is not None:
        report["features"] = feature_summary
        report["events"] = _event_rows(features)
    if start_date is not None:
        report["data"]["start_date"] = start_date
    if end_date is not None:
        report["data"]["end_date"] = end_date
    if adjust is not None:
        report["data"]["adjust"] = adjust
    if retrieved_at_utc is not None:
        report["data"]["retrieved_at_utc"] = retrieved_at_utc
    return report


def write_report(report: Mapping[str, Any], path: Path) -> None:
    """Write UTF-8 JSON to a rebuildable ignored report path."""

    output = path.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def export_features(frame: pd.DataFrame, path: Path) -> None:
    """Export the missing-aware feature frame for local inspection."""

    output = path.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False, encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the 生益电子 daily VSA AKQuant demo.")
    parser.add_argument(
        "--source",
        choices=("remote", "fixture"),
        default="remote",
        help="Data source (default: remote AkShare; fixture is offline synthetic data).",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="YYYYMMDD start date (default: three calendar months before the end date).",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="YYYYMMDD end date (default: today in Asia/Shanghai).",
    )
    parser.add_argument("--adjust", choices=("", "qfq", "hfq"), default=DEFAULT_ADJUST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--indicators", type=Path, default=DEFAULT_INDICATORS)
    parser.add_argument(
        "--features",
        type=Path,
        default=None,
        help="Optional CSV path for the missing-aware VSA feature frame.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report_path = args.report if args.report.is_absolute() else PROJECT_ROOT / args.report
    indicator_path = (
        args.indicators if args.indicators.is_absolute() else PROJECT_ROOT / args.indicators
    )
    if args.source == "fixture":
        raw = build_fixture()
        requested_start_date = None
        requested_end_date = None
        provenance = {
            "source": "deterministic synthetic fixture (offline test only)",
            "storage": "in-memory DataFrame",
            "backend": "fixture",
            "cache_hit": False,
            "retrieved_at_utc": None,
        }
    else:
        requested_start_date, requested_end_date = resolve_date_window(
            args.start_date,
            args.end_date,
        )
        batch = fetch_demo_data_with_metadata(
            requested_start_date,
            requested_end_date,
            args.adjust,
        )
        raw = batch.frame
        provenance = {
            "source": batch.source,
            "storage": batch.storage,
            "backend": batch.backend,
            "cache_hit": batch.cache_hit,
            "retrieved_at_utc": batch.retrieved_at_utc.isoformat(),
        }

    config = VSAConfig()
    features = generate_vsa_frame(raw, config)
    result = run_backtest(features)
    report = build_report(
        result,
        features=features,
        source=provenance["source"],
        storage=provenance["storage"],
        backend=provenance["backend"],
        cache_hit=provenance["cache_hit"],
        start_date=requested_start_date,
        end_date=requested_end_date,
        adjust=args.adjust if args.source == "remote" else None,
        retrieved_at_utc=provenance["retrieved_at_utc"],
        config=config,
    )
    write_report(report, report_path)
    result.export_indicators(str(indicator_path), format="json")
    if args.features is not None:
        feature_path = (
            args.features if args.features.is_absolute() else PROJECT_ROOT / args.features
        )
        export_features(features, feature_path)

    metrics = report["metrics"]
    print(
        f"AKQuant VSA demo completed for {DEMO_SYMBOL} {DEMO_NAME}: "
        f"{metrics.get('total_bars', 0)} bars, "
        f"{metrics.get('closed_trade_count', 0)} closed trades, "
        f"total PnL {metrics.get('total_pnl', 0)}"
    )
    print(f"Indicators: {indicator_path}")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_ADJUST",
    "DEFAULT_END_DATE",
    "DEFAULT_INDICATORS",
    "DEFAULT_LOOKBACK_MONTHS",
    "DEFAULT_REPORT",
    "DEFAULT_START_DATE",
    "DEMO_NAME",
    "DEMO_SYMBOL",
    "VSAFetchBatch",
    "build_fixture",
    "build_report",
    "export_features",
    "fetch_demo_data",
    "fetch_demo_data_with_metadata",
    "generate_vsa_frame",
    "normalize_demo_frame",
    "parse_args",
    "recent_date_window",
    "resolve_date_window",
    "run_backtest",
    "write_report",
]
