"""Remote daily-bar acquisition for the prediction pipeline.

The default path uses the project's ``AkShareMarketProvider`` so bounded retries, the Tencent
historical fallback, and backend configuration apply consistently with the HTTP service. Remote
responses are cached by the provider under ``data/cache/akshare`` and never become a local backend.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from forbiddenland.config import CompatibilityConfig
from forbiddenland.domain.market import MarketDataResult, MarketQuery
from forbiddenland.infrastructure.market_data.akshare_provider import AkShareMarketProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SYMBOLS = ("688256", "688072", "600183")
DEFAULT_START_DATE = "20190101"
DEFAULT_END_DATE = "20261016"
DEFAULT_ADJUST = "qfq"

_REQUIRED_COLUMNS = frozenset({"timestamp", "open", "high", "low", "close", "volume"})

RemoteFetcher = Callable[[str, str, str, str], pd.DataFrame]


@dataclass(frozen=True, slots=True)
class PredictDataBatch:
    """Normalized OHLCV panel plus the provider provenance used to fetch it."""

    frame: pd.DataFrame
    source: str
    storage: str
    retrieved_at_utc: datetime


def _parse_date(value: str) -> date:
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"date must use YYYYMMDD format: {value!r}")
    return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")


def _result_to_frame(result: MarketDataResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in result.bars
        ]
    )


def _normalize_remote_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if frame.empty:
        raise ValueError(f"AkShare returned no rows for {symbol}")
    data = frame.rename(columns={"date": "timestamp"}).copy()
    missing = sorted(_REQUIRED_COLUMNS - set(data.columns))
    if missing:
        raise ValueError(f"AkShare response for {symbol} is missing columns: {', '.join(missing)}")
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError(f"AkShare response for {symbol} contains invalid dates")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    ohlcv = data[["open", "high", "low", "close", "volume"]]
    if ohlcv.isna().any().any():
        raise ValueError(f"AkShare response for {symbol} contains non-numeric OHLCV values")
    data["symbol"] = str(symbol)
    return data[["timestamp", "open", "high", "low", "close", "volume", "symbol"]]


def fetch_bars(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    adjust: str = DEFAULT_ADJUST,
    *,
    provider: AkShareMarketProvider | None = None,
    fetcher: RemoteFetcher | None = None,
) -> PredictDataBatch:
    """Fetch remote daily bars for each symbol and retain provenance.

    ``fetcher`` is injectable so tests can validate normalization without making network calls;
    the default path uses ``AkShareMarketProvider``.
    """

    frames: list[pd.DataFrame] = []
    sources: list[str] = []
    storages: list[str] = []
    retrieved_at: list[datetime] = []
    configured = provider or AkShareMarketProvider(CompatibilityConfig.from_env())
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start > end:
        raise ValueError("start_date must not be later than end_date")

    for symbol in symbols:
        try:
            if fetcher is None:
                result = configured.fetch_history(
                    MarketQuery(symbol=symbol, start_date=start, end_date=end, adjust=adjust)
                )
                frame = _result_to_frame(result)
                sources.append(result.source)
                storages.append(result.storage)
                retrieved_at.append(result.retrieved_at_utc)
            else:
                frame = fetcher(symbol, start_date, end_date, adjust)
                sources.append("custom remote fetcher")
                storages.append("in-memory DataFrame")
                retrieved_at.append(datetime.now(UTC))
            frames.append(_normalize_remote_frame(frame, symbol))
        except Exception as exc:
            raise RuntimeError(
                f"Unable to load remote AkShare data for {symbol} between "
                f"{start_date} and {end_date}: {exc}"
            ) from exc

    return PredictDataBatch(
        frame=(
            pd.concat(frames, ignore_index=True)
            .sort_values(["timestamp", "symbol"])
            .reset_index(drop=True)
        ),
        source="; ".join(sorted(set(sources))),
        storage="; ".join(sorted(set(storages))),
        retrieved_at_utc=max(retrieved_at),
    )
