"""Daily-bar acquisition for the prediction baseline.

Raw fetching and normalization live in ``src/line_fetcher`` (the ``akshare`` source wraps the
project's ``AkShareMarketProvider``, so bounded retries, the Tencent historical fallback, and the
remote response cache apply). This module maps the baseline's date-windowed panel needs onto the
line-fetcher ``BarRequest`` contract and retains provider provenance in the batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from line_fetcher.model import BarRequest
from line_fetcher.sources import DataSource, get_source
from line_fetcher.sources.akshare import market_for_code

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_SYMBOLS = ("688256", "688072", "600183")
DEFAULT_START_DATE = "20190101"
DEFAULT_END_DATE = "20261016"
DEFAULT_ADJUST = "qfq"
DEFAULT_BAR_COUNT = 2500

_REQUIRED_KEYS = frozenset({"datetime", "open", "high", "low", "close"})


@dataclass(frozen=True, slots=True)
class PredictDataBatch:
    """Normalized OHLCV panel plus the provider provenance used to fetch it."""

    frame: pd.DataFrame
    source: str
    storage: str
    retrieved_at_utc: datetime
    skipped: dict[str, str]


def _parse_date(value: str) -> date:
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"date must use YYYYMMDD format: {value!r}")
    return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")


def _bars_to_frame(bars: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
    if not bars:
        raise ValueError(f"line-fetcher returned no bars for {symbol}")
    missing = sorted(_REQUIRED_KEYS - set(bars[0]))
    if missing:
        raise ValueError(f"line-fetcher bars for {symbol} are missing keys: {', '.join(missing)}")
    rows = []
    for bar in bars:
        volume = bar.get("vol", bar.get("volume"))
        rows.append(
            {
                "timestamp": pd.Timestamp(bar["datetime"]),
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": volume,
            }
        )
    frame = pd.DataFrame(rows)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError(f"line-fetcher bars for {symbol} contain non-numeric OHLCV values")
    timestamps = pd.to_datetime(frame["timestamp"], errors="coerce")
    if timestamps.dt.tz is not None:
        timestamps = timestamps.dt.tz_localize(None)
    frame["timestamp"] = timestamps
    if frame["timestamp"].isna().any():
        raise ValueError(f"line-fetcher bars for {symbol} contain invalid datetimes")
    frame["symbol"] = str(symbol)
    return frame[["timestamp", "open", "high", "low", "close", "volume", "symbol"]]


def _batch_provenance(
    configured: DataSource,
    symbols: tuple[str, ...],
) -> tuple[str, str, datetime]:
    last = getattr(configured, "last_fetch", {})
    entries = [last[symbol] for symbol in symbols if symbol in last]
    if entries:
        source = "; ".join(sorted({entry["source"] for entry in entries}))
        storage = "; ".join(sorted({entry["storage"] for entry in entries}))
        retrieved = max(entry["retrieved_at_utc"] for entry in entries)
        return source, storage, retrieved
    return configured.name, "line-fetcher source", datetime.now(UTC)


def fetch_bars(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    adjust: str = DEFAULT_ADJUST,
    count: int = DEFAULT_BAR_COUNT,
    *,
    source: DataSource | None = None,
) -> PredictDataBatch:
    """Fetch daily bars through a line-fetcher source and window them by date.

    ``source`` is injectable so tests can validate the mapping without network calls; the default
    path uses the registered line-fetcher source named ``easy-tdx`` (cache-through SQLite).
    """

    configured = source or get_source("easy-tdx")
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start > end:
        raise ValueError("start_date must not be later than end_date")

    frames: list[pd.DataFrame] = []
    skipped: dict[str, str] = {}
    for symbol in symbols:
        try:
            bars = configured.fetch_bars(
                BarRequest(
                    market=market_for_code(symbol),
                    code=symbol,
                    period="DAILY",
                    adjust=adjust,
                    count=count,
                    start=0,
                )
            )
            frame = _bars_to_frame(bars, symbol)
            frame = frame[
                (frame["timestamp"] >= pd.Timestamp(start))
                & (frame["timestamp"] <= pd.Timestamp(end))
            ]
            frames.append(frame)
        except Exception as exc:  # noqa: BLE001  # keep partial results; record the failing symbol
            skipped[symbol] = str(exc)

    if not frames:
        reasons = "; ".join(f"{symbol}: {reason}" for symbol, reason in skipped.items())
        raise RuntimeError(
            f"Unable to load line-fetcher bars for any of {symbols} between "
            f"{start_date} and {end_date}: {reasons}"
        )

    source_name, storage, retrieved_at = _batch_provenance(configured, symbols)
    return PredictDataBatch(
        frame=(
            pd.concat(frames, ignore_index=True)
            .sort_values(["timestamp", "symbol"])
            .reset_index(drop=True)
        ),
        source=source_name,
        storage=storage,
        retrieved_at_utc=retrieved_at,
        skipped=skipped,
    )
