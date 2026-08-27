"""Pydantic schemas exposed by the versioned HTTP API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..domain.market import (
    Adjustment,
    AssetType,
    MarketAsset,
    MarketBar,
    MarketDataResult,
    Security,
)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    service: str
    version: str
    backend: str
    data_policy: str


class SecurityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str

    @classmethod
    def from_domain(cls, security: Security) -> SecurityResponse:
        return cls(code=security.code, name=security.name)


class SecurityListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SecurityResponse]


class MarketAssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_type: AssetType
    code: str
    name: str

    @classmethod
    def from_domain(cls, asset: MarketAsset) -> MarketAssetResponse:
        return cls.model_validate(asset, from_attributes=True)


class MarketAssetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MarketAssetResponse]


class MarketBarResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    amount: float | None = None
    change: float | None = None
    change_percent: float | None = None
    turnover_rate: float | None = None

    @classmethod
    def from_domain(cls, bar: MarketBar) -> MarketBarResponse:
        return cls.model_validate(bar, from_attributes=True)


class MarketSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bar_count: int
    first_date: date
    latest_date: date
    latest_close: float
    period_change_percent: float | None
    max_close: float
    min_close: float


class ProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    backend: str
    storage: str
    start_date: date
    end_date: date
    adjust: Adjustment
    retrieved_at_utc: datetime
    local_snapshot_review_required: bool
    cache_hit: bool


class MarketBarsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_type: AssetType
    symbol: str
    bars: list[MarketBarResponse]
    summary: MarketSummaryResponse
    provenance: ProvenanceResponse

    @classmethod
    def from_domain(cls, result: MarketDataResult) -> MarketBarsResponse:
        return cls(
            asset_type=result.query.asset_type,
            symbol=result.query.symbol,
            bars=[MarketBarResponse.from_domain(bar) for bar in result.bars],
            summary=MarketSummaryResponse.model_validate(result.summary, from_attributes=True),
            provenance=ProvenanceResponse(
                source=result.source,
                backend=result.backend,
                storage=result.storage,
                start_date=result.query.start_date,
                end_date=result.query.end_date,
                adjust=result.query.adjust,
                retrieved_at_utc=result.retrieved_at_utc,
                local_snapshot_review_required=result.local_snapshot_review_required,
                cache_hit=getattr(result, "cache_hit", False),
            ),
        )
