"""AkShare remote daily-bar source adapter.

Wraps the project's ``AkShareMarketProvider`` so bounded retries, the Tencent historical
fallback, and the remote response cache apply consistently with the rest of the repository.
Provider provenance (source, storage, retrieval time) is retained per symbol in ``last_fetch``
for callers that need it in reports.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from ecom_data.errors import DataSourceError
from ecom_data.model import BarRequest, QuoteRequest
from ecom_data.sources import DataSource, register_source
from forbiddenland.config import CompatibilityConfig
from forbiddenland.domain.market import MarketQuery
from forbiddenland.infrastructure.market_data.akshare_provider import AkShareMarketProvider

_FULL_HISTORY_START = date(1990, 1, 1)
_ADJUST_MAP = {
    "": "",
    "NONE": "",
    "QFQ": "qfq",
    "HFQ": "hfq",
}


def market_for_code(code: str) -> str:
    """Map a six-digit A-share code to the ecom-data market prefix (SH/SZ/BJ)."""

    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    return "BJ"


def _bar_to_ecom(bar: Any) -> dict[str, Any]:
    return {
        "datetime": bar.date.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "vol": bar.volume,
        "amount": bar.amount,
    }


class AkShareSource(DataSource):
    """Daily OHLCV bars from the project's remote AkShare provider."""

    name = "akshare"

    def __init__(self, provider: AkShareMarketProvider | None = None) -> None:
        self.provider = provider or AkShareMarketProvider(CompatibilityConfig.from_env())
        self.last_fetch: dict[str, dict[str, Any]] = {}

    @property
    def source_version(self) -> str | None:
        return "akshare-provider-1"

    def fetch_bars(self, request: BarRequest) -> list[dict[str, Any]]:
        if request.period != "DAILY":
            raise DataSourceError(
                f"akshare source supports DAILY bars only; got {request.period!r}"
            )
        if market_for_code(request.code) != request.market.upper():
            raise DataSourceError(
                f"market/code mismatch for {request}: expected "
                f"{market_for_code(request.code)} for {request.code}"
            )
        adjust = _ADJUST_MAP.get(request.adjust, request.adjust.lower())
        try:
            result = self.provider.fetch_history(
                MarketQuery(
                    symbol=request.code,
                    start_date=_FULL_HISTORY_START,
                    end_date=datetime.now(UTC).date(),
                    adjust=adjust,
                )
            )
        except Exception as exc:
            raise DataSourceError(f"AkShare provider failed for {request.code}: {exc}") from exc
        bars = [_bar_to_ecom(bar) for bar in result.bars]
        self.last_fetch[request.code] = {
            "source": result.source,
            "storage": result.storage,
            "retrieved_at_utc": result.retrieved_at_utc,
        }
        return bars

    def fetch_quote(self, request: QuoteRequest) -> dict[str, Any] | None:
        return None


register_source(AkShareSource())
