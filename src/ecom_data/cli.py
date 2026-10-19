"""Stable command-line interface for the ecom-data cache layer.

The subcommand surface and request parameters are source-agnostic; only
``--source`` selects the backend adapter. Adding a new data source never
changes this interface.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ecom_data.config import default_source_name
from ecom_data.errors import EcomDataError
from ecom_data.model import BarRequest, QuoteRequest
from ecom_data.service import EcomData
from ecom_data.sources import get_source, list_sources


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", default=None, help="data-source name (default: easy-tdx)")
    p.add_argument("--ttl-seconds", type=int, default=None, help="cache freshness window")
    p.add_argument("--refresh", action="store_true", help="bypass cache and refetch")
    p.add_argument("--db", type=Path, default=None, help="SQLite cache path override")
    p.add_argument("--table", action="store_true", help="print a text table")
    p.add_argument("--output", choices=["json", "table", "csv"], default="json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecom-data",
        description="Cache-through securities data layer with pluggable sources.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    bars = sub.add_parser("bars", help="K-line bars (cache first, fetch-and-write-back)")
    bars.add_argument("market")
    bars.add_argument("code")
    bars.add_argument("--period", default="DAILY")
    bars.add_argument("--adjust", choices=["NONE", "QFQ", "HFQ"], default="NONE")
    bars.add_argument("--bar-time", choices=["start", "end"], default="start")
    bars.add_argument("--count", type=int, default=30)
    bars.add_argument("--start", type=int, default=0)
    _add_common(bars)

    quote = sub.add_parser("quote", help="quote snapshot (cache first, fetch-and-write-back)")
    quote.add_argument("market")
    quote.add_argument("code")
    _add_common(quote)

    stats = sub.add_parser("stats", help="cache statistics")
    stats.add_argument("--source", default=None)
    stats.add_argument("--db", type=Path, default=None)

    clear = sub.add_parser("clear", help="delete cached entries")
    clear.add_argument("--source", default=None)
    clear.add_argument("--older-than-seconds", type=int, default=None)
    clear.add_argument("--yes", action="store_true", help="actually delete (default: dry-run)")
    clear.add_argument("--db", type=Path, default=None)

    sub.add_parser("sources", help="list registered data sources")
    ping = sub.add_parser("ping", help="probe a data source")
    ping.add_argument("--source", default=None)
    return parser


def _format_cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _format_table(headers: list[str], rows: list[list[Any]]) -> str:
    str_rows = [[_format_cell(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    lines.append("  ".join("-" * w for w in widths))
    lines.extend("  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in str_rows)
    return "\n".join(lines)


def _bars_table(result: dict[str, Any]) -> str:
    headers = ["datetime", "open", "high", "low", "close", "vol", "amount"]
    rows = [
        [
            bar["datetime"][:19].replace("T", " "),
            bar.get("open"),
            bar.get("high"),
            bar.get("low"),
            bar.get("close"),
            bar.get("vol"),
            bar.get("amount"),
        ]
        for bar in result["bars"]
    ]
    return _format_table(headers, rows)


def _quote_table(result: dict[str, Any]) -> str:
    quote = result.get("quote") or {}
    rows = [[k, v] for k, v in quote.items()]
    return _format_table(["field", "value"], rows)


def _emit_bars(result: dict[str, Any], fmt: str) -> str:
    if fmt == "table":
        return _bars_table(result)
    if fmt == "csv":
        headers = ["datetime", "open", "high", "low", "close", "vol", "amount"]
        stream = [",".join(headers)]
        for bar in result["bars"]:
            stream.append(
                ",".join(
                    str(bar.get(h, ""))
                    for h in ["datetime", "open", "high", "low", "close", "vol", "amount"]
                )
            )
        return "\n".join(stream)
    return json.dumps(result, ensure_ascii=False, indent=2)


def _emit_quote(result: dict[str, Any], fmt: str) -> str:
    if fmt == "table":
        return _quote_table(result)
    if fmt == "csv":
        quote = result.get("quote") or {}
        writer = csv.StringIO()
        writer.writerow(["field", "value"])
        for k, v in quote.items():
            writer.writerow([k, v])
        return writer.getvalue().rstrip("\r\n")
    return json.dumps(result, ensure_ascii=False, indent=2)


def _resolve_source(name: str | None) -> str:
    return name or default_source_name()


def _dispatch(args: argparse.Namespace) -> int:
    command = args.command
    if command == "bars":
        request = BarRequest(
            market=args.market,
            code=args.code,
            period=args.period,
            adjust=args.adjust,
            bar_time=args.bar_time,
            count=args.count,
            start=args.start,
        )
        fmt = "table" if args.table else args.output
        with EcomData(_resolve_source(args.source), args.db) as svc:
            result = svc.get_bars(request, ttl_seconds=args.ttl_seconds, refresh=args.refresh)
        print(_emit_bars(result, fmt))
        return 0

    if command == "quote":
        request = QuoteRequest(market=args.market, code=args.code)
        fmt = "table" if args.table else args.output
        with EcomData(_resolve_source(args.source), args.db) as svc:
            result = svc.get_quote(request, ttl_seconds=args.ttl_seconds, refresh=args.refresh)
        print(_emit_quote(result, fmt))
        return 0

    if command == "stats":
        with EcomData(_resolve_source(args.source), args.db) as svc:
            stats = svc.stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    if command == "clear":
        with EcomData(_resolve_source(args.source), args.db) as svc:
            if args.yes:
                deleted = svc.clear(source=args.source, older_than_seconds=args.older_than_seconds)
                print(json.dumps(deleted, ensure_ascii=False, indent=2))
            else:
                preview = svc.stats()
                print(
                    "dry-run: " + json.dumps(preview, ensure_ascii=False) + "\nuse --yes to delete"
                )
        return 0

    if command == "sources":
        entries = []
        for name in list_sources():
            source = get_source(name)
            try:
                version = source.source_version
            except EcomDataError:
                version = None
            entries.append({"name": name, "version": version})
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0

    if command == "ping":
        source = get_source(_resolve_source(args.source))
        result = source.ping()
        print(
            json.dumps(
                {"source": source.name, "version": source.source_version, "result": result},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    parser = build_parser()
    parser.error(f"unknown command: {command}")
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except (EcomDataError, OSError, ValueError) as exc:
        print(f"ecom-data: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
