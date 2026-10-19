"""Cache-through orchestration: local-first reads, fetch-and-write-back."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Self

from line_fetcher.config import default_db_path, default_source_name
from line_fetcher.model import BarRequest, QuoteRequest
from line_fetcher.sources import DataSource, get_source
from line_fetcher.storage import Cache, iso_now, utcnow


def _slice_tail(bars: list[dict[str, Any]], start: int, count: int) -> list[dict[str, Any]]:
    """Take the requested window from the tail of a stored series.

    ``start`` is the offset from the latest bar; the window is
    ``[latest - start - count, latest - start)``.
    """

    if not bars:
        return []
    end = None if start == 0 else len(bars) - start
    begin = max(0, len(bars) - (start + count))
    return bars[begin:end]


class LineFetcher:
    """Cache-through facade bound to one data source and one SQLite cache."""

    def __init__(self, source_name: str | None = None, db_path: Path | None = None) -> None:
        self.source: DataSource = get_source(source_name or default_source_name())
        self.cache = Cache(db_path or default_db_path())

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.cache.close()

    @staticmethod
    def _expired(fetched_at: str, ttl_seconds: int | None) -> bool:
        if not ttl_seconds:
            return False
        fetched = datetime.fromisoformat(fetched_at)
        return (utcnow() - fetched).total_seconds() > ttl_seconds

    def get_bars(
        self,
        request: BarRequest,
        ttl_seconds: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Return bars from cache when possible, otherwise fetch and write back."""

        entry = (
            None
            if refresh
            else self.cache.get_bar_series(
                self.source.name,
                request.market,
                request.code,
                request.period,
                request.adjust,
                request.bar_time,
            )
        )
        if (
            entry is not None
            and not self._expired(entry.fetched_at, ttl_seconds)
            and entry.row_count >= request.start + request.count
        ):
            bars = _slice_tail(entry.bars, request.start, request.count)
            return self._bars_result(
                request,
                bars,
                cached=True,
                fetched_at=entry.fetched_at,
                source_version=entry.source_version,
                ttl_seconds=ttl_seconds,
            )

        fetch_request = replace(request, count=request.start + request.count, start=0)
        bars = self.source.fetch_bars(fetch_request)
        self.cache.put_bar_series(
            self.source.name,
            request.market,
            request.code,
            request.period,
            request.adjust,
            request.bar_time,
            bars,
            self.source.source_version,
        )
        selected = _slice_tail(bars, request.start, request.count)
        return self._bars_result(
            request,
            selected,
            cached=False,
            fetched_at=iso_now(),
            source_version=self.source.source_version,
            ttl_seconds=ttl_seconds,
        )

    def get_quote(
        self,
        request: QuoteRequest,
        ttl_seconds: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        entry = (
            None
            if refresh
            else self.cache.get_quote(self.source.name, request.market, request.code)
        )
        if entry is not None and not self._expired(entry.fetched_at, ttl_seconds):
            return self._quote_result(
                request,
                entry.payload,
                cached=True,
                fetched_at=entry.fetched_at,
                source_version=entry.source_version,
                ttl_seconds=ttl_seconds,
            )

        payload = self.source.fetch_quote(request)
        self.cache.put_quote(
            self.source.name,
            request.market,
            request.code,
            payload,
            self.source.source_version,
        )
        return self._quote_result(
            request,
            payload,
            cached=False,
            fetched_at=iso_now(),
            source_version=self.source.source_version,
            ttl_seconds=ttl_seconds,
        )

    def stats(self) -> dict[str, Any]:
        return self.cache.stats()

    def clear(
        self, source: str | None = None, older_than_seconds: int | None = None
    ) -> dict[str, int]:
        return self.cache.clear(source=source, older_than_seconds=older_than_seconds)

    def _bars_result(
        self,
        request: BarRequest,
        bars: list[dict[str, Any]],
        *,
        cached: bool,
        fetched_at: str,
        source_version: str | None,
        ttl_seconds: int | None,
    ) -> dict[str, Any]:
        return {
            "source": self.source.name,
            "source_version": source_version,
            "market": request.market,
            "code": request.code,
            "request": {
                "period": request.period,
                "adjust": request.adjust,
                "bar_time": request.bar_time,
                "count": request.count,
                "start": request.start,
            },
            "provenance": {
                "cached": cached,
                "fetched_at": fetched_at,
                "ttl_seconds": ttl_seconds,
            },
            "bars": bars,
        }

    def _quote_result(
        self,
        request: QuoteRequest,
        payload: dict[str, Any] | None,
        *,
        cached: bool,
        fetched_at: str,
        source_version: str | None,
        ttl_seconds: int | None,
    ) -> dict[str, Any]:
        return {
            "source": self.source.name,
            "source_version": source_version,
            "market": request.market,
            "code": request.code,
            "provenance": {
                "cached": cached,
                "fetched_at": fetched_at,
                "ttl_seconds": ttl_seconds,
            },
            "quote": payload,
        }
