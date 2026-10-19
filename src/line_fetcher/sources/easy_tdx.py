"""easy-tdx data-source adapter backed by the easy-tdx Python API and the line-fetcher cache.

Fetching uses ``MacClient`` (one persistent connection, auto-paginated K-lines) instead of
spawning the ``easy-tdx`` binary. Every successful bar/quote response is written back to the
line-fetcher SQLite cache (``data/cache/line_fetcher.db``); a later request with the same
source/market/code/period/adjust/bar_time that already covers the requested window is served
from cache without touching the TDX server.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import easy_tdx
import pandas as pd
from easy_tdx import Adjust, MacClient, Market, Period

from line_fetcher.config import default_db_path
from line_fetcher.errors import DataSourceError
from line_fetcher.model import BarRequest, QuoteRequest
from line_fetcher.sources import DataSource, register_source
from line_fetcher.storage import Cache

_MARKET_MAP = {"SH": Market.SH, "SZ": Market.SZ, "BJ": Market.BJ}
_PERIOD_MAP = {
    "DAILY": Period.DAILY,
    "WEEKLY": Period.WEEKLY,
    "MONTHLY": Period.MONTHLY,
    "QUARTERLY": Period.QUARTERLY,
    "YEARLY": Period.YEARLY,
    "MIN1": Period.MIN_1,
    "MIN5": Period.MIN_5,
    "MIN15": Period.MIN_15,
    "MIN30": Period.MIN_30,
    "MIN60": Period.MIN_60,
}
_ADJUST_MAP = {"NONE": Adjust.NONE, "QFQ": Adjust.QFQ, "HFQ": Adjust.HFQ}


def _slice_tail(bars: list[dict[str, Any]], start: int, count: int) -> list[dict[str, Any]]:
    """Take the requested window from the tail of a stored series."""

    if not bars:
        return []
    end = None if start == 0 else len(bars) - start
    begin = max(0, len(bars) - (start + count))
    return bars[begin:end]


def _expired(fetched_at: str, ttl_seconds: int | None) -> bool:
    if not ttl_seconds:
        return False
    fetched = datetime.fromisoformat(fetched_at)
    return (datetime.now(UTC) - fetched).total_seconds() > ttl_seconds


def _scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return value


def _df_to_bars(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a MacClient K-line frame into line-fetcher bar dicts."""

    if frame is None or frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        bar: dict[str, Any] = {
            "datetime": _scalar(pd.Timestamp(row["datetime"])),
            "open": _scalar(row["open"]),
            "high": _scalar(row["high"]),
            "low": _scalar(row["low"]),
            "close": _scalar(row["close"]),
            "vol": _scalar(row["vol"]),
        }
        for extra in ("amount", "float_shares"):
            if extra in row.index and pd.notna(row[extra]):
                bar[extra] = _scalar(row[extra])
        rows.append(bar)
    return rows


class EasyTdxSource(DataSource):
    """Cache-through easy-tdx source using the easy-tdx Python package."""

    name = "easy-tdx"

    def __init__(
        self,
        db_path: Path | None = None,
        client_factory: Callable[[], MacClient] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._cache = Cache(db_path or default_db_path())
        self._client_factory = client_factory or MacClient.from_best_host
        self._client: MacClient | None = None
        self._ttl_seconds = ttl_seconds
        self.last_fetch: dict[str, dict[str, Any]] = {}

    @property
    def source_version(self) -> str | None:
        return getattr(easy_tdx, "__version__", None)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001, S110  # cleanup must not fail the source
                pass
            self._client = None

    def _ensure_client(self) -> MacClient:
        if self._client is None:
            try:
                self._client = self._client_factory()
            except Exception as exc:
                raise DataSourceError(f"easy-tdx connect failed: {exc}") from exc
        return self._client

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        client = self._ensure_client()
        try:
            return getattr(client, method)(*args, **kwargs)
        except Exception:  # noqa: BLE001  # transient TDX drop; reconnect once below
            self.close()
            try:
                return getattr(self._ensure_client(), method)(*args, **kwargs)
            except Exception as retry_exc:
                raise DataSourceError(
                    f"easy-tdx {method} failed for {args}: {retry_exc}"
                ) from retry_exc

    def _market(self, value: str) -> Market:
        try:
            return _MARKET_MAP[value.upper()]
        except KeyError as exc:
            raise DataSourceError(
                f"easy-tdx unsupported market {value!r}; supported: {', '.join(_MARKET_MAP)}"
            ) from exc

    def _period(self, value: str) -> Period:
        key = value.upper().replace("_", "")
        try:
            return _PERIOD_MAP[key]
        except KeyError as exc:
            raise DataSourceError(
                f"easy-tdx unsupported period {value!r}; supported: {', '.join(_PERIOD_MAP)}"
            ) from exc

    def _adjust(self, value: str) -> Adjust:
        try:
            return _ADJUST_MAP[value.upper()]
        except KeyError as exc:
            raise DataSourceError(
                f"easy-tdx unsupported adjust {value!r}; supported: {', '.join(_ADJUST_MAP)}"
            ) from exc

    def _mark_fetch(
        self,
        request: BarRequest,
        *,
        cached: bool,
        fetched_at: str | None = None,
    ) -> None:
        self.last_fetch[request.code] = {
            "source": self.name,
            "storage": "line-fetcher sqlite cache" if cached else "line-fetcher source",
            "retrieved_at_utc": (
                datetime.fromisoformat(fetched_at) if fetched_at else datetime.now(UTC)
            ),
        }

    def fetch_bars(self, request: BarRequest) -> list[dict[str, Any]]:
        entry = self._cache.get_bar_series(
            self.name,
            request.market,
            request.code,
            request.period,
            request.adjust,
            request.bar_time,
        )
        if (
            entry is not None
            and entry.row_count >= request.start + request.count
            and not _expired(entry.fetched_at, self._ttl_seconds)
        ):
            self._mark_fetch(request, cached=True, fetched_at=entry.fetched_at)
            return _slice_tail(entry.bars, request.start, request.count)

        frame = self._call(
            "get_stock_kline",
            self._market(request.market),
            request.code,
            self._period(request.period),
            start=request.start,
            count=request.start + request.count,
            adjust=self._adjust(request.adjust),
            bar_time=request.bar_time,
        )
        bars = _df_to_bars(frame)
        if bars:
            self._cache.put_bar_series(
                self.name,
                request.market,
                request.code,
                request.period,
                request.adjust,
                request.bar_time,
                bars,
                self.source_version,
            )
        self._mark_fetch(request, cached=False)
        return _slice_tail(bars, request.start, request.count)

    def fetch_quote(self, request: QuoteRequest) -> dict[str, Any] | None:
        entry = self._cache.get_quote(self.name, request.market, request.code)
        if entry is not None and not _expired(entry.fetched_at, self._ttl_seconds):
            return entry.payload

        frame = self._call("get_stock_quotes", [(self._market(request.market), request.code)])
        if frame is None or frame.empty:
            return None
        row = frame.iloc[0]
        payload = {str(key): _scalar(value) for key, value in row.items() if pd.notna(value)}
        payload["market"] = request.market
        payload["code"] = request.code
        self._cache.put_quote(
            self.name,
            request.market,
            request.code,
            payload,
            self.source_version,
        )
        return payload

    def ping(self) -> dict[str, Any]:
        try:
            client = self._client_factory()
        except Exception as exc:  # noqa: BLE001  # probe must not raise
            return {"reachable": False, "detail": str(exc)}
        try:
            return {"reachable": True, "servers": [getattr(client, "host", "python-api")]}
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001, S110  # probe cleanup best-effort
                pass


register_source(EasyTdxSource())
