"""Domain models for persisted per-stock technical-analysis history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from math import isfinite
from pathlib import Path
from typing import Any, Literal

from .market import MarketAsset

AnalysisStance = Literal["bullish", "neutral", "bearish"]

_STANCES = frozenset({"bullish", "neutral", "bearish"})
_ADJUSTMENTS = frozenset({"", "qfq", "hfq"})


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"analysis field {field_name!r} must be an object")
    return value


def _text(value: Any, field_name: str, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"analysis field {field_name!r} must be a string")
    result = value.strip()
    if required and not result:
        raise ValueError(f"analysis field {field_name!r} must not be empty")
    return result


def _date(value: Any, field_name: str, *, required: bool = True) -> date | None:
    if value is None and not required:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError(f"analysis field {field_name!r} must be an ISO date")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError(f"analysis field {field_name!r} must be an ISO date") from exc


def _datetime(value: Any, field_name: str, *, required: bool = False) -> datetime | None:
    if value is None and not required:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"analysis field {field_name!r} must be an ISO datetime") from exc
    else:
        raise TypeError(f"analysis field {field_name!r} must be an ISO datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _number(value: Any, field_name: str, *, required: bool = False) -> float | None:
    if value is None and not required:
        return None
    if isinstance(value, bool):
        raise TypeError(f"analysis field {field_name!r} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"analysis field {field_name!r} must be numeric") from exc
    if not isfinite(parsed):
        raise ValueError(f"analysis field {field_name!r} must be finite")
    return parsed


def _bool(value: Any, field_name: str, *, default: bool = False) -> bool:
    """Parse persisted booleans without silently treating arbitrary text as true."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    raise TypeError(f"analysis field {field_name!r} must be a boolean")


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"analysis field {field_name!r} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_text(item, f"{field_name}[{index}]"))
    return tuple(result)


def _stock_code(value: Any) -> str:
    code = _text(value, "asset.code").upper()
    if "." in code:
        code = code.rsplit(".", 1)[0]
    if not code.isdigit() or len(code) > 6:
        raise ValueError("analysis asset.code must be a stock-code string with at most six digits")
    return code.zfill(6)


@dataclass(frozen=True, slots=True)
class AnalysisPattern:
    """One context-aware pattern observation, never a guaranteed prediction."""

    name: str
    timeframe: str
    status: str
    evidence: str
    volume_confirmation: str
    confidence: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], index: int) -> AnalysisPattern:
        prefix = f"patterns[{index}]"
        return cls(
            name=_text(value.get("name"), f"{prefix}.name"),
            timeframe=_text(value.get("timeframe"), f"{prefix}.timeframe"),
            status=_text(value.get("status"), f"{prefix}.status"),
            evidence=_text(value.get("evidence"), f"{prefix}.evidence"),
            volume_confirmation=_text(
                value.get("volume_confirmation"), f"{prefix}.volume_confirmation"
            ),
            confidence=(
                _text(value.get("confidence"), f"{prefix}.confidence", required=False) or None
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "timeframe": self.timeframe,
            "status": self.status,
            "evidence": self.evidence,
            "volume_confirmation": self.volume_confirmation,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class AnalysisSetup:
    """A conditional setup with explicit risk boundaries."""

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
    def from_mapping(cls, value: Mapping[str, Any]) -> AnalysisSetup:
        return cls(
            direction=_text(value.get("direction"), "setup.direction"),
            status=_text(value.get("status"), "setup.status"),
            trigger_price=_number(value.get("trigger_price"), "setup.trigger_price"),
            entry_price=_number(value.get("entry_price"), "setup.entry_price"),
            stop_loss=_number(value.get("stop_loss"), "setup.stop_loss"),
            target_price=_number(value.get("target_price"), "setup.target_price"),
            risk_reward=_number(value.get("risk_reward"), "setup.risk_reward"),
            invalidation=_text(value.get("invalidation"), "setup.invalidation"),
            risk_note=_text(value.get("risk_note"), "setup.risk_note"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "status": self.status,
            "trigger_price": self.trigger_price,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "target_price": self.target_price,
            "risk_reward": self.risk_reward,
            "invalidation": self.invalidation,
            "risk_note": self.risk_note,
        }


@dataclass(frozen=True, slots=True)
class AnalysisReview:
    """The explicit comparison with the previous record for the same stock."""

    status: str
    previous_analysis_date: date | None
    previous_stance: str | None
    period_start: date | None
    period_end: date | None
    outcome: str
    thesis_status: str
    checks: tuple[str, ...]
    summary: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AnalysisReview:
        previous_stance = value.get("previous_stance")
        if previous_stance is not None:
            previous_stance = _text(previous_stance, "review.previous_stance")
        return cls(
            status=_text(value.get("status"), "review.status"),
            previous_analysis_date=_date(
                value.get("previous_analysis_date"),
                "review.previous_analysis_date",
                required=False,
            ),
            previous_stance=previous_stance,
            period_start=_date(value.get("period_start"), "review.period_start", required=False),
            period_end=_date(value.get("period_end"), "review.period_end", required=False),
            outcome=_text(value.get("outcome"), "review.outcome"),
            thesis_status=_text(value.get("thesis_status"), "review.thesis_status"),
            checks=_string_list(value.get("checks"), "review.checks"),
            summary=_text(value.get("summary"), "review.summary"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "previous_analysis_date": self.previous_analysis_date,
            "previous_stance": self.previous_stance,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "outcome": self.outcome,
            "thesis_status": self.thesis_status,
            "checks": list(self.checks),
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class AnalysisProvenance:
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
    def from_mapping(cls, value: Mapping[str, Any]) -> AnalysisProvenance:
        adjust = _text(value.get("adjust", ""), "provenance.adjust", required=False)
        if adjust not in _ADJUSTMENTS:
            raise ValueError("provenance.adjust must be one of '', qfq, or hfq")
        raw_count = value.get("bar_count", 0)
        if isinstance(raw_count, bool):
            raise TypeError("provenance.bar_count must be an integer")
        try:
            bar_count = int(raw_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("provenance.bar_count must be an integer") from exc
        if bar_count < 0:
            raise ValueError("provenance.bar_count must not be negative")
        return cls(
            source=_text(value.get("source"), "provenance.source"),
            backend=_text(value.get("backend"), "provenance.backend"),
            storage=_text(value.get("storage"), "provenance.storage"),
            start_date=_date(value.get("start_date"), "provenance.start_date") or date.min,
            end_date=_date(value.get("end_date"), "provenance.end_date") or date.min,
            adjust=adjust,
            retrieved_at_utc=_datetime(
                value.get("retrieved_at_utc"), "provenance.retrieved_at_utc"
            ),
            cache_hit=_bool(value.get("cache_hit"), "provenance.cache_hit"),
            frequency=_text(value.get("frequency", "daily"), "provenance.frequency"),
            bar_count=bar_count,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "backend": self.backend,
            "storage": self.storage,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "adjust": self.adjust,
            "retrieved_at_utc": self.retrieved_at_utc,
            "cache_hit": self.cache_hit,
            "frequency": self.frequency,
            "bar_count": self.bar_count,
        }


@dataclass(frozen=True, slots=True)
class AnalysisValidation:
    sample_size_bars: int
    backtest_trade_count: int
    minimum_reference_trades: int
    sample_sufficient: bool
    out_of_sample: bool
    backtest_available: bool
    warnings: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AnalysisValidation:
        def integer(name: str, default: int = 0) -> int:
            raw = value.get(name, default)
            if isinstance(raw, bool):
                raise TypeError(f"validation.{name} must be an integer")
            try:
                parsed = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"validation.{name} must be an integer") from exc
            if parsed < 0:
                raise ValueError(f"validation.{name} must not be negative")
            return parsed

        return cls(
            sample_size_bars=integer("sample_size_bars"),
            backtest_trade_count=integer("backtest_trade_count"),
            minimum_reference_trades=integer("minimum_reference_trades", 30),
            sample_sufficient=_bool(value.get("sample_sufficient"), "validation.sample_sufficient"),
            out_of_sample=_bool(value.get("out_of_sample"), "validation.out_of_sample"),
            backtest_available=_bool(
                value.get("backtest_available"), "validation.backtest_available"
            ),
            warnings=_string_list(value.get("warnings"), "validation.warnings"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "sample_size_bars": self.sample_size_bars,
            "backtest_trade_count": self.backtest_trade_count,
            "minimum_reference_trades": self.minimum_reference_trades,
            "sample_sufficient": self.sample_sufficient,
            "out_of_sample": self.out_of_sample,
            "backtest_available": self.backtest_available,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class AnalysisHistorySummary:
    """The compact row returned by the history list endpoint."""

    analysis_id: str
    analysis_date: date
    as_of_date: date
    asset: MarketAsset
    headline: str
    stance: AnalysisStance
    summary: str
    review_status: str
    previous_analysis_date: date | None
    latest_close: float | None
    sample_size_bars: int
    provenance_source: str
    backend: str
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class AnalysisHistoryRead:
    """Valid records plus per-file warnings collected by a history store."""

    records: tuple[AnalysisRecord, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisRecord:
    """One canonical, date-partitioned analysis record."""

    schema_version: int
    analysis_version: str
    analysis_id: str
    analysis_date: date
    as_of_date: date
    asset: MarketAsset
    headline: str
    stance: AnalysisStance
    summary: str
    latest_close: float | None
    indicators: dict[str, Any]
    structure: dict[str, Any]
    patterns: tuple[AnalysisPattern, ...]
    setup: AnalysisSetup
    review: AnalysisReview
    provenance: AnalysisProvenance
    validation: AnalysisValidation
    parameters: dict[str, Any]
    notes: tuple[str, ...]
    created_at_utc: datetime | None
    source_path: Path | None = field(default=None, compare=False, repr=False)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        source_path: Path | None = None,
    ) -> AnalysisRecord:
        asset_value = _mapping(value.get("asset"), "asset")
        asset_type = _text(asset_value.get("asset_type", "stock"), "asset.asset_type")
        if asset_type != "stock":
            raise ValueError("analysis asset.asset_type must be 'stock'")
        code = _stock_code(asset_value.get("code"))
        name = _text(asset_value.get("name"), "asset.name")
        analysis_date = _date(value.get("analysis_date"), "analysis_date") or date.min
        as_of_date = _date(value.get("as_of_date"), "as_of_date") or date.min
        if analysis_date < as_of_date:
            raise ValueError("analysis_date must not be earlier than as_of_date")

        raw_stance = _text(value.get("stance"), "stance").lower()
        if raw_stance not in _STANCES:
            raise ValueError("stance must be bullish, neutral, or bearish")
        raw_schema = value.get("schema_version", 1)
        if isinstance(raw_schema, bool):
            raise TypeError("schema_version must be an integer")
        try:
            schema_version = int(raw_schema)
        except (TypeError, ValueError) as exc:
            raise ValueError("schema_version must be an integer") from exc
        if schema_version < 1:
            raise ValueError("schema_version must be positive")

        raw_patterns = value.get("patterns", [])
        if isinstance(raw_patterns, (str, bytes)) or not isinstance(raw_patterns, Sequence):
            raise TypeError("patterns must be a list")
        patterns = tuple(
            AnalysisPattern.from_mapping(_mapping(item, f"patterns[{index}]"), index)
            for index, item in enumerate(raw_patterns)
        )

        return cls(
            schema_version=schema_version,
            analysis_version=_text(
                value.get("analysis_version", "technical-analysis-v1"), "analysis_version"
            ),
            analysis_id=_text(
                value.get("analysis_id", f"{code}/{analysis_date.isoformat()}"), "analysis_id"
            ),
            analysis_date=analysis_date,
            as_of_date=as_of_date,
            asset=MarketAsset(asset_type="stock", code=code, name=name),
            headline=_text(value.get("headline"), "headline"),
            stance=raw_stance,  # type: ignore[assignment]
            summary=_text(value.get("summary"), "summary"),
            latest_close=_number(value.get("latest_close"), "latest_close"),
            indicators=dict(_mapping(value.get("indicators", {}), "indicators")),
            structure=dict(_mapping(value.get("structure", {}), "structure")),
            patterns=patterns,
            setup=AnalysisSetup.from_mapping(_mapping(value.get("setup"), "setup")),
            review=AnalysisReview.from_mapping(_mapping(value.get("review"), "review")),
            provenance=AnalysisProvenance.from_mapping(
                _mapping(value.get("provenance"), "provenance")
            ),
            validation=AnalysisValidation.from_mapping(
                _mapping(value.get("validation", {}), "validation")
            ),
            parameters=dict(_mapping(value.get("parameters", {}), "parameters")),
            notes=_string_list(value.get("notes"), "notes"),
            created_at_utc=_datetime(value.get("created_at_utc"), "created_at_utc"),
            source_path=source_path,
        )

    @property
    def summary_row(self) -> AnalysisHistorySummary:
        return AnalysisHistorySummary(
            analysis_id=self.analysis_id,
            analysis_date=self.analysis_date,
            as_of_date=self.as_of_date,
            asset=self.asset,
            headline=self.headline,
            stance=self.stance,
            summary=self.summary,
            review_status=self.review.status,
            previous_analysis_date=self.review.previous_analysis_date,
            latest_close=self.latest_close,
            sample_size_bars=self.validation.sample_size_bars,
            provenance_source=self.provenance.source,
            backend=self.provenance.backend,
            cache_hit=self.provenance.cache_hit,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "analysis_version": self.analysis_version,
            "analysis_id": self.analysis_id,
            "analysis_date": self.analysis_date,
            "as_of_date": self.as_of_date,
            "asset": {
                "asset_type": self.asset.asset_type,
                "code": self.asset.code,
                "name": self.asset.name,
            },
            "headline": self.headline,
            "stance": self.stance,
            "summary": self.summary,
            "latest_close": self.latest_close,
            "indicators": self.indicators,
            "structure": self.structure,
            "patterns": [pattern.to_mapping() for pattern in self.patterns],
            "setup": self.setup.to_mapping(),
            "review": self.review.to_mapping(),
            "provenance": self.provenance.to_mapping(),
            "validation": self.validation.to_mapping(),
            "parameters": self.parameters,
            "notes": list(self.notes),
            "created_at_utc": self.created_at_utc,
        }


__all__ = [
    "AnalysisHistoryRead",
    "AnalysisHistorySummary",
    "AnalysisPattern",
    "AnalysisProvenance",
    "AnalysisRecord",
    "AnalysisReview",
    "AnalysisSetup",
    "AnalysisStance",
    "AnalysisValidation",
]
