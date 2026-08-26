"""Adapt the project's AkShare-compatible facade to the application contract."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from http.client import RemoteDisconnected
from math import isfinite, isnan
from socket import gaierror
from typing import Any

from ...application.market_service import MarketDataNotFound, MarketDataProviderError
from ...config import CompatibilityConfig
from ...domain.market import (
    DEFAULT_SECURITIES,
    AssetType,
    MarketAsset,
    MarketBar,
    MarketDataResult,
    MarketQuery,
    Security,
)
from ...integrations.akshare_compat import AkShareCompat, CompatibilityError

_STOCK_COLUMN_MAP = {
    "日期": "date",
    "股票代码": "symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "涨跌额": "change",
    "涨跌幅": "change_percent",
    "换手率": "turnover_rate",
}
_TX_STOCK_COLUMN_MAP = {
    "date": "date",
    "open": "open",
    "close": "close",
    "high": "high",
    "low": "low",
    "volume": "volume",
    "amount": "amount",
    "turnover": "turnover_rate",
}
_INDEX_COLUMN_MAP = {
    "date": "date",
    "open": "open",
    "close": "close",
    "high": "high",
    "low": "low",
    "volume": "volume",
    "amount": "amount",
}
_CONCEPT_COLUMN_MAP = {
    "日期": "date",
    "开盘价": "open",
    "收盘价": "close",
    "最高价": "high",
    "最低价": "low",
    "成交量": "volume",
    "成交额": "amount",
}
_DEFAULT_INDEX_ASSETS: tuple[MarketAsset, ...] = (
    MarketAsset(asset_type="index", code="sh000001", name="上证指数"),
    MarketAsset(asset_type="index", code="sz399001", name="深证成指"),
    MarketAsset(asset_type="index", code="sz399006", name="创业板指"),
    MarketAsset(asset_type="index", code="sh000300", name="沪深300"),
    MarketAsset(asset_type="index", code="sh000905", name="中证500"),
    MarketAsset(asset_type="index", code="sh000852", name="中证1000"),
    MarketAsset(asset_type="index", code="sh000016", name="上证50"),
    MarketAsset(asset_type="index", code="sh000688", name="科创50"),
    MarketAsset(asset_type="index", code="sz399673", name="创业板50"),
)
_OPTIONAL_MISSING_MARKERS = frozenset(
    {"", "-", "--", "na", "n/a", "n.a.", "nan", "nat", "none", "null", "<na>"}
)
_REQUESTS_TRANSIENT_ERRORS = frozenset(
    {"ChunkedEncodingError", "ConnectTimeout", "ConnectionError", "ReadTimeout", "Timeout"}
)
_URLLIB3_TRANSIENT_ERRORS = frozenset(
    {
        "ConnectTimeoutError",
        "MaxRetryError",
        "NewConnectionError",
        "ProtocolError",
        "ReadTimeoutError",
    }
)


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10].replace("/", "-"))
    except ValueError as exc:
        raise MarketDataProviderError(
            f"Invalid market date returned by AkShare: {value!r}"
        ) from exc


def _required_float(value: Any, field: str, symbol: str, observation_date: date) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataProviderError(
            f"AkShare returned a non-numeric {field} for {symbol} on {observation_date}"
        ) from exc
    if not isfinite(result):
        raise MarketDataProviderError(
            f"AkShare returned an invalid {field} for {symbol} on {observation_date}"
        )
    return result


def _optional_float(value: Any, field: str, symbol: str, observation_date: date) -> float | None:
    if value is None:
        return None
    # Pandas nullable scalars are optional-field missing markers without importing pandas here.
    if value.__class__.__name__ in {"NAType", "NaTType"}:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value.casefold() in _OPTIONAL_MISSING_MARKERS:
            return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataProviderError(
            f"AkShare returned a non-numeric {field} for {symbol} on {observation_date}"
        ) from exc
    if isnan(result):  # NaN is a provider-side missing value for optional fields.
        return None
    if not isfinite(result):
        raise MarketDataProviderError(
            f"AkShare returned an invalid {field} for {symbol} on {observation_date}"
        )
    return result


def _records(frame: Any) -> list[Mapping[str, Any]]:
    if not hasattr(frame, "to_dict"):
        raise MarketDataProviderError("AkShare returned an unexpected tabular response")
    return list(frame.to_dict(orient="records"))


def _exception_chain(error: BaseException) -> Sequence[BaseException]:
    """Return an exception and its causal/context chain without looping forever."""

    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _is_transient_remote_error(error: BaseException) -> bool:
    """Identify connection failures that are reasonable to retry."""

    for cause in _exception_chain(error):
        if isinstance(
            cause,
            (ConnectionError, TimeoutError, BrokenPipeError, RemoteDisconnected, gaierror),
        ):
            return True
        module = type(cause).__module__
        name = type(cause).__name__
        if module.startswith("requests.exceptions") and name in _REQUESTS_TRANSIENT_ERRORS:
            return True
        if module.startswith("urllib3.exceptions") and name in _URLLIB3_TRANSIENT_ERRORS:
            return True
    return False


class _RemoteFetchError(RuntimeError):
    """Keep primary and alternate remote endpoint failures together."""


class AkShareMarketProvider:
    """Provider adapter; the API never opens DuckDB or Parquet directly."""

    def __init__(
        self,
        config: CompatibilityConfig | None = None,
        *,
        client: AkShareCompat | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self.config = config or CompatibilityConfig.from_env()
        self.client = client or AkShareCompat(self.config)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper or time.sleep
        self._asset_cache: dict[AssetType, tuple[MarketAsset, ...]] = {}

    @property
    def backend(self) -> str:
        return str(self.config.backend)

    @property
    def source(self) -> str:
        if self.config.backend == "remote":
            return "AkShare remote provider"
        if self.config.backend == "local":
            return "local Parquet snapshot via DuckDB"
        return "AkShare-compatible hybrid provider"

    @property
    def storage(self) -> str:
        if self.config.backend == "remote":
            return "remote response"
        if self.config.backend == "local":
            return "DuckDB/Parquet"
        return "DuckDB/Parquet or remote response (explicit fallback)"

    def list_securities(self) -> Sequence[Security]:
        return DEFAULT_SECURITIES

    @staticmethod
    def _catalog_assets(frame: Any, asset_type: AssetType) -> tuple[MarketAsset, ...]:
        assets: list[MarketAsset] = []
        seen: set[str] = set()
        for record in _records(frame):
            raw_code = record.get("code")
            raw_name = record.get("name")
            if raw_code is None or raw_name is None:
                raise MarketDataProviderError(
                    f"AkShare returned an invalid {asset_type} catalog response"
                )
            code = str(raw_code).strip()
            name = str(raw_name).strip()
            if asset_type == "stock":
                code = code.zfill(6)
            elif code.lower().endswith(".ti"):
                code = code.upper()
            if not code or not name or code in seen:
                continue
            seen.add(code)
            assets.append(MarketAsset(asset_type=asset_type, code=code, name=name))
        return tuple(assets)

    def list_assets(self, asset_type: AssetType) -> Sequence[MarketAsset]:
        cached = self._asset_cache.get(asset_type)
        if cached is not None:
            return cached
        if asset_type == "index":
            assets = _DEFAULT_INDEX_ASSETS
        else:
            try:
                frame = (
                    self.client.stock_info_a_code_name()
                    if asset_type == "stock"
                    else self.client.stock_board_concept_name_ths()
                )
                assets = self._catalog_assets(frame, asset_type)
            except MarketDataProviderError:
                raise
            except CompatibilityError as exc:
                raise MarketDataProviderError(str(exc)) from exc
            except Exception as exc:
                raise MarketDataProviderError(
                    f"Unable to load AkShare {asset_type} catalog: {exc}"
                ) from exc
        self._asset_cache[asset_type] = assets
        return assets

    def _concept_query_symbol(self, symbol: str) -> str:
        normalized = symbol.strip()
        for asset in self.list_assets("concept"):
            if normalized in {asset.code, asset.name}:
                return asset.name
        raise MarketDataNotFound(f"No concept asset found for {symbol}")

    def _request_frame(self, query: MarketQuery) -> tuple[Any, Mapping[str, str]]:
        start = query.start_date.strftime("%Y%m%d")
        end = query.end_date.strftime("%Y%m%d")
        if query.asset_type == "stock":
            frame, column_map, _, _, _ = self._request_stock_frame(query)
            return frame, column_map
        if query.asset_type == "index":
            return (
                self.client.stock_zh_index_daily_em(
                    symbol=query.symbol,
                    start_date=start,
                    end_date=end,
                ),
                _INDEX_COLUMN_MAP,
            )
        return (
            self.client.stock_board_concept_index_ths(
                symbol=self._concept_query_symbol(query.symbol),
                start_date=start,
                end_date=end,
            ),
            _CONCEPT_COLUMN_MAP,
        )

    def _call_with_retries(self, function: Callable[..., Any], **kwargs: Any) -> Any:
        attempts = self.config.remote_retry_attempts if self.config.backend != "local" else 1
        for attempt in range(attempts):
            try:
                return function(**kwargs)
            except Exception as exc:
                if not _is_transient_remote_error(exc) or attempt == attempts - 1:
                    raise
                delay = self.config.remote_retry_backoff_seconds * (2**attempt)
                if delay > 0:
                    self._sleeper(delay)
        raise AssertionError("remote retry loop completed without returning or raising")

    def _request_stock_frame(
        self, query: MarketQuery
    ) -> tuple[Any, Mapping[str, str], str, str, bool]:
        start = query.start_date.strftime("%Y%m%d")
        end = query.end_date.strftime("%Y%m%d")
        primary_kwargs = {
            "symbol": query.symbol,
            "period": "daily",
            "start_date": start,
            "end_date": end,
            "adjust": query.adjust,
            "timeout": self.config.remote_request_timeout_seconds,
        }
        try:
            frame = self._call_with_retries(self.client.stock_zh_a_hist, **primary_kwargs)
            return frame, _STOCK_COLUMN_MAP, self.source, self.storage, False
        except Exception as primary_exc:
            can_use_alternate = (
                self.config.backend != "local"
                and self.config.remote_alternate_source
                and _is_transient_remote_error(primary_exc)
            )
            if not can_use_alternate:
                raise

            alternate = getattr(self.client, "stock_zh_a_hist_tx", None)
            if not callable(alternate):
                raise
            alternate_kwargs = {
                "symbol": query.symbol,
                "start_date": start,
                "end_date": end,
                "adjust": query.adjust,
                "timeout": self.config.remote_request_timeout_seconds,
            }
            try:
                frame = self._call_with_retries(alternate, **alternate_kwargs)
            except Exception as alternate_exc:
                raise _RemoteFetchError(
                    "Primary AkShare stock_zh_a_hist failed with a transient network error "
                    f"({primary_exc}); Tencent stock_zh_a_hist_tx fallback also failed "
                    f"({alternate_exc})"
                ) from alternate_exc
            return (
                frame,
                _TX_STOCK_COLUMN_MAP,
                f"{self.source} (Tencent historical fallback)",
                f"{self.storage} (Tencent historical fallback)",
                True,
            )

    @staticmethod
    def _map_bars(
        records: Sequence[Mapping[str, Any]],
        column_map: Mapping[str, str],
        symbol: str,
        *,
        turnover_rate_scale: float = 1.0,
        derive_changes: bool = False,
        volume_required: bool = True,
    ) -> tuple[MarketBar, ...]:
        prepared: list[tuple[date, Mapping[str, Any]]] = []
        for record in records:
            mapped = {(column_map.get(key, key)): value for key, value in record.items()}
            observation_date = _date_value(mapped.get("date"))
            prepared.append((observation_date, mapped))
        prepared.sort(key=lambda item: item[0])

        bars: list[MarketBar] = []
        previous_close: float | None = None
        for observation_date, mapped in prepared:
            close = _required_float(mapped.get("close"), "close", symbol, observation_date)
            change = _optional_float(mapped.get("change"), "change", symbol, observation_date)
            change_percent = _optional_float(
                mapped.get("change_percent"),
                "change_percent",
                symbol,
                observation_date,
            )
            if derive_changes and change is None and previous_close is not None:
                change = close - previous_close
            if (
                derive_changes
                and change_percent is None
                and change is not None
                and previous_close not in (None, 0)
            ):
                change_percent = change / previous_close * 100
            turnover_rate = _optional_float(
                mapped.get("turnover_rate"),
                "turnover_rate",
                symbol,
                observation_date,
            )
            if turnover_rate is not None:
                # Tencent returns turnover as a ratio; the primary endpoint uses percentage points.
                turnover_rate *= turnover_rate_scale
            bars.append(
                MarketBar(
                    symbol=symbol,
                    date=observation_date,
                    open=_required_float(mapped.get("open"), "open", symbol, observation_date),
                    high=_required_float(mapped.get("high"), "high", symbol, observation_date),
                    low=_required_float(mapped.get("low"), "low", symbol, observation_date),
                    close=close,
                    volume=(
                        _required_float(mapped.get("volume"), "volume", symbol, observation_date)
                        if volume_required
                        else _optional_float(
                            mapped.get("volume"), "volume", symbol, observation_date
                        )
                    ),
                    amount=_optional_float(
                        mapped.get("amount"), "amount", symbol, observation_date
                    ),
                    change=change,
                    change_percent=change_percent,
                    turnover_rate=turnover_rate,
                )
            )
            previous_close = close
        return tuple(bars)

    def fetch_history(self, query: MarketQuery) -> MarketDataResult:
        try:
            if query.asset_type == "stock":
                frame, column_map, source, storage, alternate_source = self._request_stock_frame(
                    query
                )
            else:
                frame, column_map = self._request_frame(query)
                source, storage, alternate_source = self.source, self.storage, False
        except MarketDataProviderError:
            raise
        except CompatibilityError as exc:
            raise MarketDataProviderError(str(exc)) from exc
        except Exception as exc:
            raise MarketDataProviderError(
                f"Unable to load AkShare {query.asset_type} data for {query.symbol}: {exc}"
            ) from exc

        records = _records(frame)
        if not records:
            raise MarketDataNotFound(
                f"No market bars found for {query.symbol} between "
                f"{query.start_date.isoformat()} and {query.end_date.isoformat()}"
            )
        bars = self._map_bars(
            records,
            column_map,
            query.symbol,
            turnover_rate_scale=100.0 if alternate_source else 1.0,
            derive_changes=alternate_source,
            volume_required=query.asset_type != "concept",
        )
        return MarketDataResult(
            query=query,
            bars=bars,
            source=source,
            backend=self.backend,
            storage=storage,
            retrieved_at_utc=self._clock(),
            local_snapshot_review_required=self.config.backend != "remote",
        )
