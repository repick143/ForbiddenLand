"""Request and result models shared by every data source."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BarRequest:
    """A normalized K-line request, independent of the underlying data source.

    ``start`` is an offset counted from the latest bar (0 = latest), matching
    the easy-tdx CLI semantics.
    """

    market: str
    code: str
    period: str = "DAILY"
    adjust: str = "NONE"
    bar_time: str = "start"
    count: int = 30
    start: int = 0

    def __post_init__(self) -> None:
        if not self.market:
            raise ValueError("market must not be empty")
        if not self.code:
            raise ValueError("code must not be empty")
        if self.count <= 0:
            raise ValueError("count must be positive")
        if self.start < 0:
            raise ValueError("start must be non-negative")
        if self.bar_time not in {"start", "end"}:
            raise ValueError("bar_time must be 'start' or 'end'")


@dataclass(frozen=True)
class QuoteRequest:
    """A normalized real-time quote request."""

    market: str
    code: str

    def __post_init__(self) -> None:
        if not self.market:
            raise ValueError("market must not be empty")
        if not self.code:
            raise ValueError("code must not be empty")
