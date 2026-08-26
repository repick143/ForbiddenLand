"""Run a short-term AKQuant backtest with remote AkShare data.

The default path deliberately fetches remote daily data through AKQuant's AkShare helper. The
synthetic fixture and DuckDB round trip remain available for deterministic unit tests only; local
market snapshots are not consumed until they have been revalidated.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

import akquant
import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "cache" / "short_term_demo.duckdb"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "short_term_demo.json"
DEFAULT_START_DATE = "20240101"
DEFAULT_END_DATE = "20240331"
DEFAULT_ADJUST = "qfq"
LOOKBACK = 3
ORDER_SIZE = 100
INITIAL_CASH = 100_000.0

STOCKS = {
    "688256": "寒武纪",
    "688072": "拓荆科技",
    "600183": "生益科技",
}

# Fixed close paths keep the example reproducible and make both entry and exit signals visible.
CLOSE_PATHS = {
    "688256": (
        10.0,
        10.1,
        10.3,
        10.6,
        10.9,
        11.2,
        11.0,
        10.7,
        10.4,
        10.8,
        11.1,
        11.5,
        11.8,
        11.4,
        11.0,
        11.4,
        11.8,
        12.2,
        12.5,
        12.0,
    ),
    "688072": (
        20.0,
        19.8,
        19.6,
        19.4,
        19.8,
        20.2,
        20.6,
        20.3,
        19.9,
        19.5,
        19.9,
        20.4,
        20.8,
        21.1,
        20.7,
        20.3,
        20.0,
        20.4,
        20.8,
        21.2,
    ),
    "600183": (
        30.0,
        30.2,
        30.4,
        30.7,
        31.0,
        31.3,
        31.0,
        30.7,
        30.5,
        30.9,
        31.3,
        31.7,
        32.0,
        32.3,
        31.9,
        31.5,
        31.2,
        31.6,
        32.0,
        32.4,
    ),
}

REMOTE_REQUIRED_COLUMNS = frozenset({"timestamp", "open", "high", "low", "close", "volume"})


def build_fixture() -> pd.DataFrame:
    """Build deterministic OHLCV bars for the standard project test securities."""

    rows: list[dict[str, Any]] = []
    for symbol, closes in CLOSE_PATHS.items():
        for index, close in enumerate(closes):
            timestamp = pd.Timestamp("2024-01-02") + pd.offsets.BDay(index)
            previous_close = closes[index - 1] if index else close * 0.99
            open_price = previous_close * 1.002
            rows.append(
                {
                    "timestamp": timestamp,
                    "open": open_price,
                    "high": max(open_price, close) * 1.01,
                    "low": min(open_price, close) * 0.99,
                    "close": close,
                    "volume": 100_000.0 + index * 1_000.0,
                    "symbol": symbol,
                }
            )

    return pd.DataFrame(rows).sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def load_from_duckdb(frame: pd.DataFrame, database: Path) -> pd.DataFrame:
    """Persist the synthetic test fixture in DuckDB and read it back for offline tests."""

    database = database.expanduser()
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(database))
    try:
        connection.register("short_term_fixture", frame)
        connection.execute(
            "CREATE OR REPLACE TABLE short_term_bars AS "
            "SELECT timestamp, open, high, low, close, volume, symbol "
            "FROM short_term_fixture"
        )
        connection.unregister("short_term_fixture")
        return connection.execute(
            "SELECT timestamp, open, high, low, close, volume, symbol "
            "FROM short_term_bars ORDER BY timestamp, symbol"
        ).fetchdf()
    finally:
        connection.close()


def normalize_remote_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Normalize one remote AkShare response into AKQuant's canonical OHLCV columns."""

    if frame.empty:
        raise ValueError(f"AkShare returned no rows for {symbol}")

    data = frame.rename(columns={"date": "timestamp"}).copy()
    missing = sorted(REMOTE_REQUIRED_COLUMNS - set(data.columns))
    if missing:
        raise ValueError(f"AkShare response for {symbol} is missing columns: {', '.join(missing)}")

    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError(f"AkShare response for {symbol} contains invalid dates")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if data[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError(f"AkShare response for {symbol} contains non-numeric OHLCV values")

    data["symbol"] = str(symbol)
    return (
        data[["timestamp", "open", "high", "low", "close", "volume", "symbol"]]
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def fetch_remote_data(
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    adjust: str = DEFAULT_ADJUST,
    *,
    fetcher: Callable[[str, str, str, str], pd.DataFrame] = akquant.fetch_akshare_symbol,
) -> pd.DataFrame:
    """Fetch all standard project securities from the remote AkShare provider.

    ``fetcher`` is injectable so tests can validate normalization without making network calls.
    A missing or malformed symbol is treated as a failed run rather than silently omitted.
    """

    frames: list[pd.DataFrame] = []
    for symbol in STOCKS:
        try:
            remote_frame = fetcher(symbol, start_date, end_date, adjust)
            frames.append(normalize_remote_frame(remote_frame, symbol))
        except Exception as exc:
            raise RuntimeError(
                f"Unable to load remote AkShare data for {symbol} "
                f"between {start_date} and {end_date}: {exc}"
            ) from exc

    result = pd.concat(frames, ignore_index=True)
    return result.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


class ShortTermMomentumStrategy(akquant.Strategy):
    """A small close-to-next-open moving-average strategy for pipeline validation."""

    lookback = LOOKBACK
    order_size = ORDER_SIZE

    def on_bar(self, bar: akquant.Bar) -> None:
        history = self.get_history(self.lookback, symbol=bar.symbol, field="close")
        if len(history) < self.lookback:
            return

        moving_average = fmean(float(value) for value in history)
        position = self.get_position(bar.symbol)
        if position == 0 and bar.close > moving_average:
            self.buy(symbol=bar.symbol, quantity=self.order_size, tag="momentum-entry")
        elif position > 0 and bar.close < moving_average:
            self.sell(symbol=bar.symbol, quantity=position, tag="momentum-exit")


def run_backtest(frame: pd.DataFrame) -> akquant.BacktestResult:
    """Run the short-term strategy with explicit A-share execution assumptions."""

    return akquant.run_backtest(
        data=frame,
        strategy=ShortTermMomentumStrategy,
        symbols=list(STOCKS),
        initial_cash=INITIAL_CASH,
        commission_rate=0.0003,
        stamp_tax_rate=0.001,
        transfer_fee_rate=0.00001,
        min_commission=0.0,
        slippage={"type": "percent", "value": 0.0002},
        lot_size=ORDER_SIZE,
        t_plus_one=True,
        warmup_period=LOOKBACK,
        history_depth=LOOKBACK,
        fill_policy=akquant.NextOpen(),
        timezone="Asia/Shanghai",
        show_progress=False,
    )


def _json_value(value: Any) -> Any:
    """Convert common pandas/NumPy scalar values into JSON-compatible values."""

    if hasattr(value, "item"):
        value = value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build_report(
    result: akquant.BacktestResult,
    *,
    source: str = "AkShare remote via akquant.fetch_akshare_symbol",
    storage: str = "in-memory DataFrame",
    start_date: str | None = None,
    end_date: str | None = None,
    adjust: str | None = None,
    retrieved_at_utc: str | None = None,
) -> dict[str, Any]:
    """Extract a compact, serializable summary from an AKQuant result."""

    metrics_frame = result.metrics_df
    metric_names = (
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
    )
    metrics = {
        name: _json_value(metrics_frame.loc[name, "value"])
        for name in metric_names
        if name in metrics_frame.index
    }
    report: dict[str, Any] = {
        "engine": "akquant",
        "engine_version": getattr(akquant, "__version__", "unknown"),
        "strategy": "three_bar_momentum",
        "lookback": LOOKBACK,
        "order_size": ORDER_SIZE,
        "symbols": [{"code": code, "name": name} for code, name in STOCKS.items()],
        "data": {
            "source": source,
            "storage": storage,
        },
        "metrics": metrics,
    }
    if start_date is not None:
        report["data"]["start_date"] = start_date
    if end_date is not None:
        report["data"]["end_date"] = end_date
    if adjust is not None:
        report["data"]["adjust"] = adjust
    if retrieved_at_utc is not None:
        report["data"]["retrieved_at_utc"] = retrieved_at_utc
    return report


def write_report(report: dict[str, Any], path: Path) -> None:
    """Write the demo report as UTF-8 JSON."""

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the short-term AKQuant AkShare demo.")
    parser.add_argument(
        "--source",
        choices=("remote", "fixture"),
        default="remote",
        help="Data source (default: remote AkShare; fixture is offline test data).",
    )
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START_DATE,
        help="Remote AkShare start date in YYYYMMDD format (default: 20240101).",
    )
    parser.add_argument(
        "--end-date",
        default=DEFAULT_END_DATE,
        help="Remote AkShare end date in YYYYMMDD format (default: 20240331).",
    )
    parser.add_argument(
        "--adjust",
        choices=("", "qfq", "hfq"),
        default=DEFAULT_ADJUST,
        help="Remote price adjustment mode (default: qfq).",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help="DuckDB path for --source fixture only (default: data/cache/short_term_demo.duckdb).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="JSON report path (default: reports/short_term_demo.json).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    database = args.database
    if not database.is_absolute():
        database = PROJECT_ROOT / database
    report_path = args.report
    if not report_path.is_absolute():
        report_path = PROJECT_ROOT / report_path

    if args.source == "remote":
        bars = fetch_remote_data(args.start_date, args.end_date, args.adjust)
        report_kwargs: Mapping[str, Any] = {
            "source": "AkShare remote via akquant.fetch_akshare_symbol",
            "storage": "in-memory DataFrame",
            "start_date": args.start_date,
            "end_date": args.end_date,
            "adjust": args.adjust,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
        }
    else:
        bars = load_from_duckdb(build_fixture(), database)
        report_kwargs = {
            "source": "deterministic synthetic fixture (offline test only)",
            "storage": "DuckDB",
        }
    result = run_backtest(bars)
    report = build_report(result, **report_kwargs)
    write_report(report, report_path)

    metrics = report["metrics"]
    print(
        f"AKQuant short-term demo completed: {metrics.get('total_bars', 0)} bars, "
        f"{metrics.get('closed_trade_count', 0)} closed trades, "
        f"total PnL {metrics.get('total_pnl', 0)}"
    )
    if args.source == "fixture":
        print(f"DuckDB: {database}")
    else:
        print("Data source: remote AkShare (local snapshots were not read)")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
