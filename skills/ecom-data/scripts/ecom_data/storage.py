"""SQLite-backed cache storage.

The cache is a plain key-value store for fetched series and quotes. SQLite is
the lightest available engine (stdlib, zero extra dependencies) and is
deliberately not an analytical store; DuckDB remains the project's analysis
layer.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_BAR_SERIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS bar_series (
    source TEXT NOT NULL,
    market TEXT NOT NULL,
    code TEXT NOT NULL,
    period TEXT NOT NULL,
    adjust TEXT NOT NULL,
    bar_time TEXT NOT NULL,
    payload TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    first_dt TEXT NOT NULL,
    last_dt TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source_version TEXT,
    PRIMARY KEY (source, market, code, period, adjust, bar_time)
)
"""

_QUOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    source TEXT NOT NULL,
    market TEXT NOT NULL,
    code TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source_version TEXT,
    PRIMARY KEY (source, market, code)
)
"""

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utcnow().isoformat(timespec="seconds")


class CachedSeries:
    """A stored bar series plus its provenance metadata."""

    def __init__(
        self,
        bars: list[dict[str, Any]],
        row_count: int,
        first_dt: str,
        last_dt: str,
        fetched_at: str,
        source_version: str | None,
    ) -> None:
        self.bars = bars
        self.row_count = row_count
        self.first_dt = first_dt
        self.last_dt = last_dt
        self.fetched_at = fetched_at
        self.source_version = source_version


class CachedQuote:
    """A stored quote snapshot plus provenance metadata."""

    def __init__(
        self,
        payload: dict[str, Any],
        fetched_at: str,
        source_version: str | None,
    ) -> None:
        self.payload = payload
        self.fetched_at = fetched_at
        self.source_version = source_version


class Cache:
    """SQLite cache with WAL mode and safe upserts."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            for schema in (_BAR_SERIES_SCHEMA, _QUOTES_SCHEMA, _META_SCHEMA):
                self._conn.execute(schema)

    def close(self) -> None:
        self._conn.close()

    def get_bar_series(
        self,
        source: str,
        market: str,
        code: str,
        period: str,
        adjust: str,
        bar_time: str,
    ) -> CachedSeries | None:
        row = self._conn.execute(
            "SELECT payload, row_count, first_dt, last_dt, fetched_at, source_version "
            "FROM bar_series "
            "WHERE source=? AND market=? AND code=? AND period=? AND adjust=? AND bar_time=?",
            (source, market, code, period, adjust, bar_time),
        ).fetchone()
        if row is None:
            return None
        return CachedSeries(
            bars=json.loads(row["payload"]),
            row_count=row["row_count"],
            first_dt=row["first_dt"],
            last_dt=row["last_dt"],
            fetched_at=row["fetched_at"],
            source_version=row["source_version"],
        )

    def put_bar_series(
        self,
        source: str,
        market: str,
        code: str,
        period: str,
        adjust: str,
        bar_time: str,
        bars: list[dict[str, Any]],
        source_version: str | None,
        fetched_at: str | None = None,
    ) -> None:
        payload = json.dumps(bars, ensure_ascii=False)
        row_count = len(bars)
        first_dt = bars[0]["datetime"] if bars else ""
        last_dt = bars[-1]["datetime"] if bars else ""
        with self._conn:
            self._conn.execute(
                "INSERT INTO bar_series "
                "(source, market, code, period, adjust, bar_time, payload, "
                " row_count, first_dt, last_dt, fetched_at, source_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, market, code, period, adjust, bar_time) "
                "DO UPDATE SET payload=excluded.payload, row_count=excluded.row_count, "
                "first_dt=excluded.first_dt, last_dt=excluded.last_dt, "
                "fetched_at=excluded.fetched_at, source_version=excluded.source_version",
                (
                    source,
                    market,
                    code,
                    period,
                    adjust,
                    bar_time,
                    payload,
                    row_count,
                    first_dt,
                    last_dt,
                    fetched_at or iso_now(),
                    source_version,
                ),
            )

    def get_quote(self, source: str, market: str, code: str) -> CachedQuote | None:
        row = self._conn.execute(
            "SELECT payload, fetched_at, source_version FROM quotes "
            "WHERE source=? AND market=? AND code=?",
            (source, market, code),
        ).fetchone()
        if row is None:
            return None
        return CachedQuote(
            payload=json.loads(row["payload"]),
            fetched_at=row["fetched_at"],
            source_version=row["source_version"],
        )

    def put_quote(
        self,
        source: str,
        market: str,
        code: str,
        payload: dict[str, Any],
        source_version: str | None,
        fetched_at: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO quotes (source, market, code, payload, fetched_at, source_version) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, market, code) "
                "DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at, "
                "source_version=excluded.source_version",
                (
                    source,
                    market,
                    code,
                    json.dumps(payload, ensure_ascii=False),
                    fetched_at or iso_now(),
                    source_version,
                ),
            )

    def stats(self) -> dict[str, Any]:
        bar_row = self._conn.execute(
            "SELECT COUNT(*) AS series, COALESCE(SUM(row_count), 0) AS rows, "
            "MAX(fetched_at) AS latest FROM bar_series"
        ).fetchone()
        quote_row = self._conn.execute(
            "SELECT COUNT(*) AS quotes, MAX(fetched_at) AS latest FROM quotes"
        ).fetchone()
        by_source = [
            dict(row)
            for row in self._conn.execute(
                "SELECT source, COUNT(*) AS series, COALESCE(SUM(row_count), 0) AS rows "
                "FROM bar_series GROUP BY source ORDER BY source"
            ).fetchall()
        ]
        return {
            "db_path": str(self.db_path),
            "bar_series": {
                "count": bar_row["series"],
                "rows": bar_row["rows"],
                "latest_fetched_at": bar_row["latest"],
            },
            "quotes": {
                "count": quote_row["quotes"],
                "latest_fetched_at": quote_row["latest"],
            },
            "by_source": by_source,
        }

    def clear(
        self,
        source: str | None = None,
        older_than_seconds: int | None = None,
    ) -> dict[str, int]:
        """Delete matching rows and return per-table deleted counts."""

        where: list[str] = []
        params: list[Any] = []
        if source is not None:
            where.append("source=?")
            params.append(source)
        if older_than_seconds is not None:
            cutoff = (utcnow() - timedelta(seconds=older_than_seconds)).isoformat(
                timespec="seconds"
            )
            where.append("fetched_at<?")
            params.append(cutoff)
        deleted = {"bar_series": 0, "quotes": 0}
        with self._conn:
            for table in ("bar_series", "quotes"):
                clause = " AND ".join(where) if where else "1=1"
                cursor = self._conn.execute(f"DELETE FROM {table} WHERE {clause}", tuple(params))
                deleted[table] = cursor.rowcount
        return deleted
