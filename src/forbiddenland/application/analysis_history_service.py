"""Use cases for browsing and reviewing persisted stock analyses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from ..domain.analysis import AnalysisHistoryRead, AnalysisHistorySummary, AnalysisRecord
from ..infrastructure.analysis_history import (
    AnalysisHistoryNotFound,
    normalize_stock_code,
)


class AnalysisHistoryStore(Protocol):
    def list(self) -> AnalysisHistoryRead: ...

    def get(self, code: str, analysis_date: date) -> AnalysisRecord: ...


@dataclass(frozen=True, slots=True)
class AnalysisHistoryList:
    items: tuple[AnalysisHistorySummary, ...]
    total: int
    warnings: tuple[str, ...]


class AnalysisHistoryService:
    """Keep filtering and ordering logic out of HTTP route declarations."""

    def __init__(self, store: AnalysisHistoryStore):
        self._store = store

    def list_history(
        self,
        *,
        query: str = "",
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
    ) -> AnalysisHistoryList:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must not be later than end_date")
        normalized_symbol = normalize_stock_code(symbol) if symbol else None
        needle = query.strip().casefold()
        result = self._store.list()
        matches = []
        for record in result.records:
            summary = record.summary_row
            if normalized_symbol and summary.asset.code != normalized_symbol:
                continue
            if start_date is not None and summary.analysis_date < start_date:
                continue
            if end_date is not None and summary.analysis_date > end_date:
                continue
            if needle:
                searchable = (
                    f"{summary.asset.code} {summary.asset.name} "
                    f"{summary.headline} {summary.summary} "
                    f"{record.review.status} {record.review.outcome} "
                    f"{record.review.thesis_status} {record.review.summary} "
                    f"{' '.join(record.review.checks)}"
                ).casefold()
                if needle not in searchable:
                    continue
            matches.append(summary)
        matches.sort(key=lambda item: (item.analysis_date, item.asset.code), reverse=True)
        selected = tuple(matches[:limit])
        return AnalysisHistoryList(items=selected, total=len(matches), warnings=result.warnings)

    def get_history(self, symbol: str, analysis_date: date) -> AnalysisRecord:
        """Return a full record, preserving explicit not-found semantics for the API."""

        try:
            return self._store.get(normalize_stock_code(symbol), analysis_date)
        except ValueError as exc:
            raise AnalysisHistoryNotFound(f"invalid stock symbol {symbol!r}") from exc


__all__ = ["AnalysisHistoryList", "AnalysisHistoryService"]
