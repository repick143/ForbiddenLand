"""Adapt the project's AkShare-compatible facade to the application contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from math import isfinite, isnan
from typing import Any

from ...application.market_service import MarketDataNotFound, MarketDataProviderError
from ...config import CompatibilityConfig
from ...domain.market import DEFAULT_SECURITIES, MarketBar, MarketDataResult, MarketQuery, Security
from ...integrations.akshare_compat import AkShareCompat, CompatibilityError

_COLUMN_MAP = {
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
_OPTIONAL_MISSING_MARKERS = frozenset(
    {"", "-", "--", "na", "n/a", "n.a.", "nan", "nat", "none", "null", "<na>"}
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


class AkShareMarketProvider:
    """Provider adapter; the API never opens DuckDB or Parquet directly."""

    def __init__(
        self,
        config: CompatibilityConfig | None = None,
        *,
        client: AkShareCompat | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config = config or CompatibilityConfig.from_env()
        self.client = client or AkShareCompat(self.config)
        self._clock = clock or (lambda: datetime.now(UTC))

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

    def list_securities(self) -> Sequence[Security]:
        return DEFAULT_SECURITIES

    def fetch_history(self, query: MarketQuery) -> MarketDataResult:
        try:
            frame = self.client.stock_zh_a_hist(
                symbol=query.symbol,
                period="daily",
                start_date=query.start_date.strftime("%Y%m%d"),
                end_date=query.end_date.strftime("%Y%m%d"),
                adjust=query.adjust,
            )
        except CompatibilityError as exc:
            raise MarketDataProviderError(str(exc)) from exc
        except Exception as exc:
            raise MarketDataProviderError(
                f"Unable to load AkShare data for {query.symbol}: {exc}"
            ) from exc

        records = _records(frame)
        if not records:
            raise MarketDataNotFound(
                f"No market bars found for {query.symbol} between "
                f"{query.start_date.isoformat()} and {query.end_date.isoformat()}"
            )
        bars: list[MarketBar] = []
        for record in records:
            mapped = {(_COLUMN_MAP.get(key, key)): value for key, value in record.items()}
            observation_date = _date_value(mapped.get("date"))
            bars.append(
                MarketBar(
                    symbol=query.symbol,
                    date=observation_date,
                    open=_required_float(
                        mapped.get("open"), "open", query.symbol, observation_date
                    ),
                    high=_required_float(
                        mapped.get("high"), "high", query.symbol, observation_date
                    ),
                    low=_required_float(mapped.get("low"), "low", query.symbol, observation_date),
                    close=_required_float(
                        mapped.get("close"), "close", query.symbol, observation_date
                    ),
                    volume=_required_float(
                        mapped.get("volume"), "volume", query.symbol, observation_date
                    ),
                    amount=_optional_float(
                        mapped.get("amount"), "amount", query.symbol, observation_date
                    ),
                    change=_optional_float(
                        mapped.get("change"), "change", query.symbol, observation_date
                    ),
                    change_percent=_optional_float(
                        mapped.get("change_percent"),
                        "change_percent",
                        query.symbol,
                        observation_date,
                    ),
                    turnover_rate=_optional_float(
                        mapped.get("turnover_rate"),
                        "turnover_rate",
                        query.symbol,
                        observation_date,
                    ),
                )
            )
        bars.sort(key=lambda item: item.date)
        return MarketDataResult(
            query=query,
            bars=tuple(bars),
            source=self.source,
            backend=self.backend,
            storage="remote response" if self.config.backend == "remote" else "DuckDB/Parquet",
            retrieved_at_utc=self._clock(),
            local_snapshot_review_required=self.config.backend != "remote",
        )
