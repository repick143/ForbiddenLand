"""Pydantic schemas exposed by the versioned HTTP API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ..domain.analysis import (
    AnalysisHistorySummary,
    AnalysisPattern,
    AnalysisProvenance,
    AnalysisRecord,
    AnalysisReview,
    AnalysisSetup,
    AnalysisValidation,
)
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


class AnalysisPatternResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    timeframe: str
    status: str
    evidence: str
    volume_confirmation: str
    confidence: str | None

    @classmethod
    def from_domain(cls, pattern: AnalysisPattern) -> AnalysisPatternResponse:
        return cls.model_validate(pattern, from_attributes=True)


class AnalysisSetupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str
    status: str
    trigger_price: float | None
    entry_price: float | None
    stop_loss: float | None
    target_price: float | None
    risk_reward: float | None
    invalidation: str
    risk_note: str

    @classmethod
    def from_domain(cls, setup: AnalysisSetup) -> AnalysisSetupResponse:
        return cls.model_validate(setup, from_attributes=True)


class AnalysisReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    previous_analysis_date: date | None
    previous_stance: str | None
    period_start: date | None
    period_end: date | None
    outcome: str
    thesis_status: str
    checks: list[str]
    summary: str

    @classmethod
    def from_domain(cls, review: AnalysisReview) -> AnalysisReviewResponse:
        return cls(
            status=review.status,
            previous_analysis_date=review.previous_analysis_date,
            previous_stance=review.previous_stance,
            period_start=review.period_start,
            period_end=review.period_end,
            outcome=review.outcome,
            thesis_status=review.thesis_status,
            checks=list(review.checks),
            summary=review.summary,
        )


class AnalysisProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    backend: str
    storage: str
    start_date: date
    end_date: date
    adjust: str
    retrieved_at_utc: datetime | None
    cache_hit: bool
    frequency: str
    bar_count: int

    @classmethod
    def from_domain(cls, provenance: AnalysisProvenance) -> AnalysisProvenanceResponse:
        return cls.model_validate(provenance, from_attributes=True)


class AnalysisValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_size_bars: int
    backtest_trade_count: int
    minimum_reference_trades: int
    sample_sufficient: bool
    out_of_sample: bool
    backtest_available: bool
    warnings: list[str]

    @classmethod
    def from_domain(cls, validation: AnalysisValidation) -> AnalysisValidationResponse:
        return cls(
            sample_size_bars=validation.sample_size_bars,
            backtest_trade_count=validation.backtest_trade_count,
            minimum_reference_trades=validation.minimum_reference_trades,
            sample_sufficient=validation.sample_sufficient,
            out_of_sample=validation.out_of_sample,
            backtest_available=validation.backtest_available,
            warnings=list(validation.warnings),
        )


class AnalysisHistorySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    analysis_date: date
    as_of_date: date
    asset: MarketAssetResponse
    headline: str
    stance: Literal["bullish", "neutral", "bearish"]
    summary: str
    review_status: str
    previous_analysis_date: date | None
    latest_close: float | None
    sample_size_bars: int
    provenance_source: str
    backend: str
    cache_hit: bool

    @classmethod
    def from_domain(cls, item: AnalysisHistorySummary) -> AnalysisHistorySummaryResponse:
        return cls(
            analysis_id=item.analysis_id,
            analysis_date=item.analysis_date,
            as_of_date=item.as_of_date,
            asset=MarketAssetResponse.from_domain(item.asset),
            headline=item.headline,
            stance=item.stance,
            summary=item.summary,
            review_status=item.review_status,
            previous_analysis_date=item.previous_analysis_date,
            latest_close=item.latest_close,
            sample_size_bars=item.sample_size_bars,
            provenance_source=item.provenance_source,
            backend=item.backend,
            cache_hit=item.cache_hit,
        )


class AnalysisHistoryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AnalysisHistorySummaryResponse]
    total: int
    warnings: list[str]


class AnalysisRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    analysis_version: str
    analysis_id: str
    analysis_date: date
    as_of_date: date
    asset: MarketAssetResponse
    headline: str
    stance: Literal["bullish", "neutral", "bearish"]
    summary: str
    latest_close: float | None
    indicators: dict[str, Any]
    structure: dict[str, Any]
    patterns: list[AnalysisPatternResponse]
    setup: AnalysisSetupResponse
    review: AnalysisReviewResponse
    provenance: AnalysisProvenanceResponse
    validation: AnalysisValidationResponse
    parameters: dict[str, Any]
    notes: list[str]
    created_at_utc: datetime | None

    @classmethod
    def from_domain(cls, record: AnalysisRecord) -> AnalysisRecordResponse:
        return cls(
            schema_version=record.schema_version,
            analysis_version=record.analysis_version,
            analysis_id=record.analysis_id,
            analysis_date=record.analysis_date,
            as_of_date=record.as_of_date,
            asset=MarketAssetResponse.from_domain(record.asset),
            headline=record.headline,
            stance=record.stance,
            summary=record.summary,
            latest_close=record.latest_close,
            indicators=record.indicators,
            structure=record.structure,
            patterns=[AnalysisPatternResponse.from_domain(item) for item in record.patterns],
            setup=AnalysisSetupResponse.from_domain(record.setup),
            review=AnalysisReviewResponse.from_domain(record.review),
            provenance=AnalysisProvenanceResponse.from_domain(record.provenance),
            validation=AnalysisValidationResponse.from_domain(record.validation),
            parameters=record.parameters,
            notes=list(record.notes),
            created_at_utc=record.created_at_utc,
        )
