"""Small, framework-independent market domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import isfinite
from typing import Literal

Adjustment = Literal["", "qfq", "hfq"]


@dataclass(frozen=True, slots=True)
class Security:
    """A security exposed by the initial research workspace."""

    code: str
    name: str


DEFAULT_SECURITIES: tuple[Security, ...] = (
    Security(code="688256", name="寒武纪"),
    Security(code="688072", name="拓荆科技"),
    Security(code="600183", name="生益科技"),
)


@dataclass(frozen=True, slots=True)
class MarketQuery:
    """A validated, normalized historical-bar query."""

    symbol: str
    start_date: date
    end_date: date
    adjust: Adjustment = ""

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if "." in symbol:
            symbol = symbol.rsplit(".", 1)[0]
        if not symbol.isdigit() or len(symbol) > 6:
            raise ValueError("symbol must be a stock-code string with at most six digits")
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be later than end_date")
        if self.adjust not in {"", "qfq", "hfq"}:
            raise ValueError("adjust must be one of '', qfq, or hfq")
        object.__setattr__(self, "symbol", symbol.zfill(6))


@dataclass(frozen=True, slots=True)
class MarketBar:
    """One normalized OHLCV observation returned by the application layer."""

    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None
    change: float | None = None
    change_percent: float | None = None
    turnover_rate: float | None = None


@dataclass(frozen=True, slots=True)
class MarketSummary:
    """Display-ready aggregate values calculated by the backend."""

    bar_count: int
    first_date: date
    latest_date: date
    latest_close: float
    period_change_percent: float | None
    max_close: float
    min_close: float


@dataclass(frozen=True, slots=True)
class MarketDataResult:
    """Bars plus provenance needed to reproduce what the UI displays."""

    query: MarketQuery
    bars: tuple[MarketBar, ...]
    source: str
    backend: str
    storage: str
    retrieved_at_utc: datetime
    local_snapshot_review_required: bool

    def __post_init__(self) -> None:
        if self.retrieved_at_utc.tzinfo is None:
            object.__setattr__(self, "retrieved_at_utc", self.retrieved_at_utc.replace(tzinfo=UTC))

    @property
    def summary(self) -> MarketSummary:
        if not self.bars:
            raise ValueError("cannot summarize an empty market result")
        first = self.bars[0]
        latest = self.bars[-1]
        period_change: float | None = None
        if first.close != 0 and isfinite(first.close) and isfinite(latest.close):
            period_change = (latest.close / first.close - 1) * 100
        closes = [bar.close for bar in self.bars]
        return MarketSummary(
            bar_count=len(self.bars),
            first_date=first.date,
            latest_date=latest.date,
            latest_close=latest.close,
            period_change_percent=period_change,
            max_close=max(closes),
            min_close=min(closes),
        )
