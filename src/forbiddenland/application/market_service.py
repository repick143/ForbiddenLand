"""Market-data use cases used by the API and future command-line clients."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..domain.market import MarketDataResult, MarketQuery, Security


class MarketDataProviderError(RuntimeError):
    """Raised when a provider cannot return a valid market result."""


class MarketDataNotFound(MarketDataProviderError):
    """Raised when a valid query has no observations."""


class MarketDataProvider(Protocol):
    @property
    def backend(self) -> str: ...

    @property
    def source(self) -> str: ...

    def list_securities(self) -> Sequence[Security]: ...

    def fetch_history(self, query: MarketQuery) -> MarketDataResult: ...


class MarketDataService:
    """Coordinate market-data queries without exposing provider or database APIs."""

    def __init__(self, provider: MarketDataProvider):
        self._provider = provider

    @property
    def backend(self) -> str:
        return self._provider.backend

    @property
    def source(self) -> str:
        return self._provider.source

    def list_securities(self) -> tuple[Security, ...]:
        return tuple(self._provider.list_securities())

    def get_history(self, query: MarketQuery) -> MarketDataResult:
        result = self._provider.fetch_history(query)
        if not result.bars:
            raise MarketDataNotFound(
                f"No market bars found for {query.symbol} between "
                f"{query.start_date.isoformat()} and {query.end_date.isoformat()}"
            )
        return result
