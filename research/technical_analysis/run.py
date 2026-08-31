"""Generate and persist per-stock technical-analysis history records."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from forbiddenland.config import CompatibilityConfig
from forbiddenland.domain.market import MarketAsset, MarketBar, MarketDataResult, MarketQuery
from forbiddenland.infrastructure.analysis_history import AnalysisHistoryRepository
from forbiddenland.infrastructure.market_data.akshare_provider import AkShareMarketProvider

from .analyzer import analyze_market_result

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY_ROOT = PROJECT_ROOT / "analysis_history"
DEFAULT_ADJUST = "qfq"
DEFAULT_LOOKBACK_DAYS = 420
DEFAULT_SYMBOLS: tuple[str, ...] = ("688183", "600183")
STOCK_CATALOG: dict[str, str] = {
    "688183": "生益电子",
    "600183": "生益科技",
    "688362": "甬矽电子",
    "002428": "云南锗业",
    "300139": "晓程科技",
    "300209": "行云科技",
    "603228": "景旺电子",
    "301717": "超纯应材",
}


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD format: {value!r}") from exc


def _business_dates(start: date, end: date) -> list[date]:
    dates: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def build_fixture(symbol: str, start: date, end: date) -> MarketDataResult:
    """Build a deterministic long fixture for offline analyzer and storage tests."""

    dates = _business_dates(start, end)
    if not dates:
        raise ValueError("fixture date range contains no business days")
    rows: list[MarketBar] = []
    base = 35.0 if symbol == "688183" else 50.0
    for index, observation_date in enumerate(dates):
        cycle = (index % 45) / 45
        close = base + index * 0.035 + 2.2 * (cycle - 0.5)
        previous = rows[-1].close if rows else close - 0.1
        open_price = previous + (0.08 if index % 3 else -0.04)
        high = max(open_price, close) + 0.65 + (index % 4) * 0.04
        low = min(open_price, close) - 0.55 - (index % 3) * 0.03
        volume = 100_000.0 + (index % 17) * 2_000.0
        if index == len(dates) - 1:
            close += 3.0
            high += 3.4
            volume *= 2.0
        rows.append(
            MarketBar(
                symbol=symbol,
                date=observation_date,
                open=round(open_price, 4),
                high=round(high, 4),
                low=round(low, 4),
                close=round(close, 4),
                volume=volume,
            )
        )
    query = MarketQuery(symbol=symbol, start_date=start, end_date=end, adjust=DEFAULT_ADJUST)
    return MarketDataResult(
        query=query,
        bars=tuple(rows),
        source="deterministic synthetic fixture (offline test only)",
        backend="fixture",
        storage="in-memory bars",
        retrieved_at_utc=datetime.combine(end, datetime.min.time(), tzinfo=UTC),
        local_snapshot_review_required=False,
    )


def _asset(symbol: str) -> MarketAsset:
    normalized = str(symbol).strip().upper()
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[0]
    if normalized.isdigit() and len(normalized) <= 6:
        normalized = normalized.zfill(6)
    if normalized not in STOCK_CATALOG:
        raise ValueError(
            f"unsupported analysis symbol {symbol!r}; supported codes: {', '.join(STOCK_CATALOG)}"
        )
    return MarketAsset(asset_type="stock", code=normalized, name=STOCK_CATALOG[normalized])


def generate_record(
    symbol: str,
    *,
    analysis_date: date,
    start_date: date,
    end_date: date,
    adjust: str = DEFAULT_ADJUST,
    history_root: Path = DEFAULT_HISTORY_ROOT,
    source: str = "remote",
    provider: AkShareMarketProvider | None = None,
) -> tuple[Path, Any]:
    """Fetch/analyze one symbol, then atomically persist its date partition."""

    asset = _asset(symbol)
    repository = AnalysisHistoryRepository(history_root)
    if source == "fixture":
        result = build_fixture(asset.code, start_date, end_date)
    else:
        configured = provider or AkShareMarketProvider(CompatibilityConfig.from_env())
        result = configured.fetch_history(
            MarketQuery(
                symbol=asset.code,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        )
    previous = repository.latest_before(asset.code, analysis_date)
    record = analyze_market_result(
        result,
        asset=asset,
        analysis_date=analysis_date,
        previous=previous,
    )
    path = repository.save(record)
    return path, record


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate stock technical-analysis history.")
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="Stock code; repeat for multiple symbols (default: 688183 and 600183).",
    )
    parser.add_argument(
        "--analysis-date",
        default=datetime.now(UTC).astimezone().date().isoformat(),
        help="Record date in YYYY-MM-DD format (default: today).",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Market data start date; defaults to analysis date minus 420 calendar days.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Market data end date; defaults to analysis date.",
    )
    parser.add_argument("--adjust", choices=("", "qfq", "hfq"), default=DEFAULT_ADJUST)
    parser.add_argument("--source", choices=("remote", "fixture"), default="remote")
    parser.add_argument("--history-root", type=Path, default=DEFAULT_HISTORY_ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        analysis_date = parse_iso_date(args.analysis_date)
        start_date = (
            parse_iso_date(args.start_date)
            if args.start_date
            else analysis_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        )
        end_date = parse_iso_date(args.end_date) if args.end_date else analysis_date
        if start_date > end_date:
            raise ValueError("start-date must not be later than end-date")
        symbols = args.symbols or list(DEFAULT_SYMBOLS)
        history_root = args.history_root
        if not history_root.is_absolute():
            history_root = PROJECT_ROOT / history_root
    except ValueError as exc:
        print(f"argument error: {exc}")
        return 2

    failures: list[str] = []
    for symbol in symbols:
        try:
            path, record = generate_record(
                symbol,
                analysis_date=analysis_date,
                start_date=start_date,
                end_date=end_date,
                adjust=args.adjust,
                history_root=history_root,
                source=args.source,
            )
            print(
                f"{record.asset.code} {record.asset.name}: {record.as_of_date.isoformat()} "
                f"stance={record.stance}, review={record.review.status}, file={path}"
            )
        except Exception as exc:  # noqa: BLE001  # isolate one provider/file failure in a batch
            message = f"{symbol}: {type(exc).__name__}: {exc}"
            failures.append(message)
            print(f"analysis failed: {message}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_ADJUST",
    "DEFAULT_HISTORY_ROOT",
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_SYMBOLS",
    "STOCK_CATALOG",
    "build_fixture",
    "generate_record",
    "main",
    "parse_args",
    "parse_iso_date",
]
