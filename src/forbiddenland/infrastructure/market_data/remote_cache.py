"""Small, validated disk cache for remote historical market responses.

The cache stores normalized bars as JSON rather than pickled provider objects.  This keeps the
cache portable across macOS and Windows, avoids executing untrusted payloads, and makes cache
entries independent of pandas/pyarrow versions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import pairwise
from math import isfinite
from pathlib import Path
from typing import Any

from ...domain.market import MarketBar, MarketQuery

LOGGER = logging.getLogger(__name__)
CACHE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class RemoteCacheEntry:
    """A successfully validated cached response and its original provenance."""

    endpoint: str
    bars: tuple[MarketBar, ...]
    source: str
    storage: str
    retrieved_at_utc: datetime


def _utc_datetime(value: datetime) -> datetime:
    """Normalize a timestamp before it is compared or serialized."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"cached {field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cached {field} must be numeric") from exc
    if not isfinite(result):
        raise ValueError(f"cached {field} must be finite")
    return result


def _optional_float(value: Any, field: str) -> float | None:
    if value is None:
        return None
    return _finite_float(value, field)


def _bar_to_payload(bar: MarketBar) -> dict[str, Any]:
    return {
        "symbol": bar.symbol,
        "date": bar.date.isoformat(),
        "open": _finite_float(bar.open, "open"),
        "high": _finite_float(bar.high, "high"),
        "low": _finite_float(bar.low, "low"),
        "close": _finite_float(bar.close, "close"),
        "volume": _optional_float(bar.volume, "volume"),
        "amount": _optional_float(bar.amount, "amount"),
        "change": _optional_float(bar.change, "change"),
        "change_percent": _optional_float(bar.change_percent, "change_percent"),
        "turnover_rate": _optional_float(bar.turnover_rate, "turnover_rate"),
    }


def _bar_from_payload(value: Any, query: MarketQuery) -> MarketBar:
    if not isinstance(value, Mapping):
        raise TypeError("cached bar must be an object")
    symbol = value.get("symbol")
    if not isinstance(symbol, str) or symbol != query.symbol:
        raise ValueError("cached bar has a different symbol")
    raw_date = value.get("date")
    if not isinstance(raw_date, str):
        raise TypeError("cached bar date must be a string")
    try:
        observation_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError("cached bar has an invalid date") from exc
    if not query.start_date <= observation_date <= query.end_date:
        raise ValueError("cached bar falls outside the requested date range")
    return MarketBar(
        symbol=symbol,
        date=observation_date,
        open=_finite_float(value.get("open"), "open"),
        high=_finite_float(value.get("high"), "high"),
        low=_finite_float(value.get("low"), "low"),
        close=_finite_float(value.get("close"), "close"),
        volume=_optional_float(value.get("volume"), "volume"),
        amount=_optional_float(value.get("amount"), "amount"),
        change=_optional_float(value.get("change"), "change"),
        change_percent=_optional_float(value.get("change_percent"), "change_percent"),
        turnover_rate=_optional_float(value.get("turnover_rate"), "turnover_rate"),
    )


class RemoteBarsCache:
    """Read and write bounded-lifetime caches for remote daily-bar queries."""

    def __init__(self, directory: Path, *, ttl_seconds: float, enabled: bool = True):
        self.directory = Path(directory).expanduser()
        parsed_ttl = float(ttl_seconds)
        if not isfinite(parsed_ttl) or parsed_ttl < 0:
            raise ValueError("ttl_seconds must be finite and non-negative")
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a boolean")
        self.ttl_seconds = parsed_ttl
        self.enabled = enabled

    @staticmethod
    def _request(query: MarketQuery, endpoint: str) -> dict[str, str]:
        # Keep the request semantics explicit so changing an endpoint or adjustment mode cannot
        # accidentally reuse a response produced for another query.
        return {
            "asset_type": query.asset_type,
            "symbol": query.symbol,
            "start_date": query.start_date.isoformat(),
            "end_date": query.end_date.isoformat(),
            "adjust": query.adjust,
            "period": "daily",
            "endpoint": endpoint,
        }

    @classmethod
    def cache_key(cls, query: MarketQuery, endpoint: str) -> str:
        request = cls._request(query, endpoint)
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def path_for(self, query: MarketQuery, endpoint: str) -> Path:
        return self.directory / f"{self.cache_key(query, endpoint)}.json"

    def load(
        self,
        query: MarketQuery,
        endpoint: str,
        *,
        now: datetime,
    ) -> RemoteCacheEntry | None:
        """Return a fresh, validated entry; malformed and expired entries are cache misses."""

        if not self.enabled or self.ttl_seconds <= 0:
            return None
        path = self.path_for(query, endpoint)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            LOGGER.warning("Unable to read remote market cache %s: %s", path, exc)
            return None

        try:
            payload = json.loads(raw)
            if not isinstance(payload, Mapping):
                raise TypeError("cache payload must be an object")
            expected_key = self.cache_key(query, endpoint)
            if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
                raise ValueError("unsupported cache schema version")
            if payload.get("backend") != "remote":
                raise ValueError("cache backend is not remote")
            if payload.get("cache_key") != expected_key:
                raise ValueError("cache key mismatch")
            if payload.get("request") != self._request(query, endpoint):
                raise ValueError("cache request mismatch")
            raw_timestamp = payload.get("retrieved_at_utc")
            if not isinstance(raw_timestamp, str):
                raise TypeError("cache timestamp must be a string")
            retrieved_at = _utc_datetime(datetime.fromisoformat(raw_timestamp))
            age_seconds = (_utc_datetime(now) - retrieved_at).total_seconds()
            if age_seconds < 0 or age_seconds > self.ttl_seconds:
                return None
            raw_bars = payload.get("bars")
            if not isinstance(raw_bars, Sequence) or isinstance(raw_bars, (str, bytes, bytearray)):
                raise TypeError("cache bars must be an array")
            bars = tuple(_bar_from_payload(item, query) for item in raw_bars)
            if not bars:
                raise ValueError("cache bars must not be empty")
            if query.asset_type != "concept" and any(bar.volume is None for bar in bars):
                raise ValueError("cached volume is required for this asset type")
            if any(left.date > right.date for left, right in pairwise(bars)):
                raise ValueError("cached bars are not ordered by date")
            source = payload.get("source")
            storage = payload.get("storage")
            if not isinstance(source, str) or not source:
                raise ValueError("cache source must be a non-empty string")
            if not isinstance(storage, str) or not storage:
                raise ValueError("cache storage must be a non-empty string")
            return RemoteCacheEntry(
                endpoint=endpoint,
                bars=bars,
                source=source,
                storage=storage,
                retrieved_at_utc=retrieved_at,
            )
        except (OSError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            LOGGER.warning("Ignoring invalid remote market cache %s: %s", path, exc)
            return None

    def store(
        self,
        query: MarketQuery,
        endpoint: str,
        bars: Sequence[MarketBar],
        *,
        source: str,
        storage: str,
        retrieved_at_utc: datetime,
    ) -> bool:
        """Atomically persist a successful response, returning whether it was written."""

        if not self.enabled or self.ttl_seconds <= 0:
            return False
        path = self.path_for(query, endpoint)
        temporary_path: Path | None = None
        try:
            bars = tuple(bars)
            if any(
                bar.symbol != query.symbol or not query.start_date <= bar.date <= query.end_date
                for bar in bars
            ):
                raise ValueError("bars do not match the requested symbol and date range")
            payload = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "backend": "remote",
                "cache_key": self.cache_key(query, endpoint),
                "request": self._request(query, endpoint),
                "source": source,
                "storage": storage,
                "retrieved_at_utc": _utc_datetime(retrieved_at_utc).isoformat(),
                "bars": [_bar_to_payload(bar) for bar in bars],
            }
            if not payload["bars"]:
                return False
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{path.stem}.",
                suffix=".tmp",
                dir=self.directory,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(encoded)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
            return True
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.warning("Unable to write remote market cache %s: %s", path, exc)
            return False
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


__all__ = ["CACHE_SCHEMA_VERSION", "RemoteBarsCache", "RemoteCacheEntry"]
