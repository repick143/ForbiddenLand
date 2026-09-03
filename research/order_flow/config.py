"""Configuration for the easy-tdx transaction-based order-flow proxy.

The feed used by this research direction contains aggregated transaction prints, not exchange
order events.  The configuration therefore names the assumptions explicitly instead of presenting
the output as a complete Level-2 order-flow imbalance (OFI).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Literal

import numpy as np

ORDER_FLOW_V1_VERSION = "order-flow-proxy-1"
ORDER_FLOW_V2_VERSION = "order-flow-proxy-2"
ORDER_FLOW_VERSION = ORDER_FLOW_V1_VERSION

UnknownDirectionPolicy = Literal["neutral", "drop", "error"]
PositionMode = Literal["full", "fixed", "percent"]
ExecutionMode = Literal["next_open", "next_close"]
RejectPolicy = Literal["reduce", "skip"]
SignalPath = Literal["auto", "vector", "loop"]
TransactionAlignment = Literal["auto", "floor", "ceil"]
StrategyVersion = Literal["v1", "v2"]


@dataclass(frozen=True, slots=True)
class OrderFlowConfig:
    """Causal feature and execution parameters.

    Values are deliberately conventional starting points rather than parameters fitted to one
    stock.  Every field is serialized in a run report so a later experiment can be reproduced.
    ``bar_minutes`` describes the target bar assembled from the transaction stream and the
    matching easy-tdx K-line; it is not a claim about transaction-event precision.

    Optional filters use ``None`` or a neutral zero value to mean "disabled".  This makes it
    possible to add a stricter experiment through a JSON file without changing the baseline run.
    """

    # ``v1`` remains the compatibility default.  ``v2`` adds the composite flow/participation
    # path while retaining all v1 columns so experiments can be compared on the same snapshot.
    strategy_version: StrategyVersion = "v1"

    # Normalization and feature baselines.
    bar_minutes: int = 5
    transaction_alignment: TransactionAlignment = "auto"
    transaction_lot_size: int = 100
    volume_baseline_sessions: int = 20
    min_history_sessions: int = 20
    large_trade_lots: int = 100
    min_transaction_coverage: float = 0.0
    max_transaction_coverage: float | None = None
    min_large_trade_share: float = 0.0
    max_large_trade_share: float | None = None
    include_auction: bool = False
    include_after_hours: bool = False
    unknown_direction_policy: UnknownDirectionPolicy = "neutral"
    cvd_reset_each_session: bool = False

    # Strong-participant evidence factor. The score formula is versioned separately and uses
    # equal-weight causal percentile components; these fields only classify and confirm states.
    participation_strong_threshold: float = 75.0
    participation_direction_threshold: float = 30.0
    participation_confirmation_bars: int = 2

    # V2 multi-scale and state parameters.  Spans/windows are bar counts, not calendar periods.
    v2_fast_span: int = 3
    v2_medium_span: int = 12
    v2_slow_span: int = 48
    v2_flow_window: int = 24
    v2_percentile_window: int = 48
    v2_regime_window: int = 24
    v2_min_observations: int = 8
    v2_min_component_count: int = 3
    v2_score_entry_threshold: float = 0.20
    v2_score_exit_threshold: float = -0.20
    v2_exhaustion_threshold: float = 0.10
    v2_min_confidence: float = 0.60
    v2_use_regime_filter: bool = False
    v2_reset_each_session: bool = True
    v2_require_confirmation: bool = True

    # Entry: positive signed delta plus a price/volume response.
    entry_delta_ratio: float = 0.20
    entry_rvol: float = 1.20
    entry_close_location: float = 0.60
    entry_price_return: float = 0.0
    entry_persistence: int = 1
    entry_delta_zscore: float | None = None
    use_vwap_filter: bool = True
    entry_vwap_distance: float = 0.0

    # Exit: supply pressure, bearish absorption, or a time/risk boundary.
    exit_delta_ratio: float = -0.20
    exit_rvol: float = 1.20
    exit_close_location: float = 0.40
    exit_price_return: float | None = None
    exit_persistence: int = 1
    exit_delta_zscore: float | None = None
    use_absorption_exit: bool = True
    absorption_rvol: float = 1.50
    absorption_max_abs_return: float = 0.002
    divergence_price_threshold: float = 0.001
    use_vwap_exit_filter: bool = False
    exit_vwap_distance: float = 0.0
    persistence_same_session: bool = True

    # Position and risk boundaries.
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    min_hold_bars: int = 3
    max_hold_bars: int | None = 48
    cooldown_bars: int = 3
    t_plus_one: bool = True
    flat_at_session_end: bool = False

    # easy-tdx execution assumptions.
    position_mode: PositionMode = "full"
    order_size: int = 0
    position_fraction: float = 1.0
    lot_size: int = 100
    initial_cash: float = 100_000.0
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.001
    slippage_per_share: float = 0.0
    execution: ExecutionMode = "next_open"
    reject_policy: RejectPolicy = "reduce"
    auto_fees: bool = False
    warmup_bars: int = 0
    signal_path: SignalPath = "auto"

    def __post_init__(self) -> None:
        if self.strategy_version not in {"v1", "v2"}:
            raise ValueError("strategy_version must be v1 or v2")
        if (
            isinstance(self.bar_minutes, bool)
            or not isinstance(self.bar_minutes, int)
            or self.bar_minutes not in {1, 5, 15, 30, 60}
        ):
            raise ValueError("bar_minutes must be one of 1, 5, 15, 30, or 60")
        if self.transaction_alignment not in {"auto", "floor", "ceil"}:
            raise ValueError("transaction_alignment must be auto, floor, or ceil")

        for name in ("volume_baseline_sessions", "min_history_sessions"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer of at least 2")
        for name in (
            "entry_persistence",
            "exit_persistence",
            "min_hold_bars",
            "cooldown_bars",
            "participation_confirmation_bars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "v2_fast_span",
            "v2_medium_span",
            "v2_slow_span",
            "v2_flow_window",
            "v2_percentile_window",
            "v2_regime_window",
            "v2_min_observations",
            "v2_min_component_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer of at least 2")
        if not self.v2_fast_span < self.v2_medium_span < self.v2_slow_span:
            raise ValueError("v2 spans must satisfy fast < medium < slow")
        if self.v2_min_observations > self.v2_percentile_window:
            raise ValueError("v2_min_observations must not exceed v2_percentile_window")
        # The signed V2 score has six components.  The participation-strength stage has five;
        # when this value is six it requires all signed components and all available strength
        # components (the feature path caps the latter at five).
        if self.v2_min_component_count > 6:
            raise ValueError("v2_min_component_count must not exceed 6")
        if isinstance(self.warmup_bars, bool) or not isinstance(self.warmup_bars, int):
            raise TypeError("warmup_bars must be a non-negative integer")
        if self.warmup_bars < 0:
            raise ValueError("warmup_bars must be a non-negative integer")
        if self.max_hold_bars is not None:
            if isinstance(self.max_hold_bars, bool) or not isinstance(self.max_hold_bars, int):
                raise ValueError("max_hold_bars must be a positive integer or None")
            if self.max_hold_bars < 1:
                raise ValueError("max_hold_bars must be a positive integer or None")
            if self.max_hold_bars < self.min_hold_bars:
                raise ValueError("max_hold_bars must be at least min_hold_bars")

        if isinstance(self.transaction_lot_size, bool) or not isinstance(
            self.transaction_lot_size, int
        ):
            raise TypeError("transaction_lot_size must be a positive integer")
        if self.transaction_lot_size <= 0:
            raise ValueError("transaction_lot_size must be a positive integer")
        if isinstance(self.large_trade_lots, bool) or not isinstance(self.large_trade_lots, int):
            raise TypeError("large_trade_lots must be a non-negative integer")
        if self.large_trade_lots < 0:
            raise ValueError("large_trade_lots must be a non-negative integer")
        if self.unknown_direction_policy not in {"neutral", "drop", "error"}:
            raise ValueError("unknown_direction_policy must be neutral, drop, or error")

        for name in (
            "include_auction",
            "include_after_hours",
            "cvd_reset_each_session",
            "persistence_same_session",
            "use_vwap_filter",
            "use_absorption_exit",
            "use_vwap_exit_filter",
            "t_plus_one",
            "flat_at_session_end",
            "auto_fees",
            "v2_use_regime_filter",
            "v2_reset_each_session",
            "v2_require_confirmation",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")

        bounded = {
            "entry_close_location": self.entry_close_location,
            "exit_close_location": self.exit_close_location,
            "min_large_trade_share": self.min_large_trade_share,
        }
        for name, raw in bounded.items():
            value = float(raw)
            if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value)

        for name in ("participation_strong_threshold", "participation_direction_threshold"):
            value = float(getattr(self, name))
            if not np.isfinite(value) or not 0.0 <= value <= 100.0:
                raise ValueError(f"{name} must be between 0 and 100")
            object.__setattr__(self, name, value)

        signed_v2 = {
            "v2_score_entry_threshold": self.v2_score_entry_threshold,
            "v2_score_exit_threshold": self.v2_score_exit_threshold,
        }
        for name, raw in signed_v2.items():
            value = float(raw)
            if not np.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between -1 and 1")
            object.__setattr__(self, name, value)
        if self.v2_score_entry_threshold < 0.0:
            raise ValueError("v2_score_entry_threshold must be non-negative")
        if self.v2_score_exit_threshold > 0.0:
            raise ValueError("v2_score_exit_threshold must be non-positive")
        exhaustion = float(self.v2_exhaustion_threshold)
        if not np.isfinite(exhaustion) or not 0.0 <= exhaustion <= 1.0:
            raise ValueError("v2_exhaustion_threshold must be between 0 and 1")
        object.__setattr__(self, "v2_exhaustion_threshold", exhaustion)
        confidence = float(self.v2_min_confidence)
        if not np.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("v2_min_confidence must be between 0 and 1")
        object.__setattr__(self, "v2_min_confidence", confidence)

        optional_bounds = {
            "min_transaction_coverage": self.min_transaction_coverage,
            "max_transaction_coverage": self.max_transaction_coverage,
            "max_large_trade_share": self.max_large_trade_share,
        }
        for name, raw in optional_bounds.items():
            if raw is None:
                continue
            value = float(raw)
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative or None")
            object.__setattr__(self, name, value)
        if (
            self.max_transaction_coverage is not None
            and self.max_transaction_coverage < self.min_transaction_coverage
        ):
            raise ValueError("max_transaction_coverage must be at least min_transaction_coverage")
        if (
            self.max_large_trade_share is not None
            and self.max_large_trade_share < self.min_large_trade_share
        ):
            raise ValueError("max_large_trade_share must be at least min_large_trade_share")

        # Price-return and VWAP-distance controls are signed thresholds.  A negative value is
        # useful for testing demand after a controlled pullback; zero keeps the original rule.
        signed = {
            "entry_price_return": self.entry_price_return,
            "entry_vwap_distance": self.entry_vwap_distance,
            "exit_vwap_distance": self.exit_vwap_distance,
        }
        for name, raw in signed.items():
            value = float(raw)
            if not np.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between -1 and 1")
            object.__setattr__(self, name, value)
        if self.exit_price_return is not None:
            value = float(self.exit_price_return)
            if not np.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError("exit_price_return must be between -1 and 1 or None")
            object.__setattr__(self, "exit_price_return", value)

        for name in ("entry_delta_zscore", "exit_delta_zscore"):
            raw = getattr(self, name)
            if raw is not None:
                value = float(raw)
                if not np.isfinite(value):
                    raise ValueError(f"{name} must be finite or None")
                object.__setattr__(self, name, value)

        non_negative = {
            "entry_rvol": self.entry_rvol,
            "exit_rvol": self.exit_rvol,
            "absorption_rvol": self.absorption_rvol,
            "absorption_max_abs_return": self.absorption_max_abs_return,
            "divergence_price_threshold": self.divergence_price_threshold,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "position_fraction": self.position_fraction,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "stamp_tax_rate": self.stamp_tax_rate,
            "slippage_per_share": self.slippage_per_share,
        }
        for name, raw in non_negative.items():
            value = float(raw)
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        initial_cash = float(self.initial_cash)
        if not np.isfinite(initial_cash) or initial_cash <= 0.0:
            raise ValueError("initial_cash must be finite and positive")
        object.__setattr__(self, "initial_cash", initial_cash)

        entry_delta = float(self.entry_delta_ratio)
        exit_delta = float(self.exit_delta_ratio)
        if not np.isfinite(entry_delta) or not 0.0 <= entry_delta <= 1.0:
            raise ValueError("entry_delta_ratio must be between 0 and 1")
        if not np.isfinite(exit_delta) or not -1.0 <= exit_delta <= 0.0:
            raise ValueError("exit_delta_ratio must be between -1 and 0")
        object.__setattr__(self, "entry_delta_ratio", entry_delta)
        object.__setattr__(self, "exit_delta_ratio", exit_delta)

        if isinstance(self.order_size, bool) or not isinstance(self.order_size, int):
            raise TypeError("order_size must be a non-negative integer")
        if self.order_size < 0:
            raise ValueError("order_size must be a non-negative integer")
        if (
            isinstance(self.lot_size, bool)
            or not isinstance(self.lot_size, int)
            or self.lot_size <= 0
        ):
            raise ValueError("lot_size must be a positive integer")
        if self.position_mode not in {"full", "fixed", "percent"}:
            raise ValueError("position_mode must be full, fixed, or percent")
        if self.position_mode == "fixed" and (
            self.order_size <= 0 or self.order_size % self.lot_size != 0
        ):
            raise ValueError("fixed order_size must be a positive multiple of lot_size")
        if self.position_mode == "percent" and not 0.0 < self.position_fraction <= 1.0:
            raise ValueError("position_fraction must be in (0, 1] for percent mode")
        # easy-tdx 1.30.3's OrderSimulator rounds to 100-share lots internally.  Rejecting another
        # value prevents a report from claiming a lot-size assumption the engine cannot honor.
        if self.lot_size != 100:
            raise ValueError("easy-tdx 1.30.3 backtest execution requires lot_size=100")
        if self.execution not in {"next_open", "next_close"}:
            raise ValueError("execution must be next_open or next_close")
        if self.reject_policy not in {"reduce", "skip"}:
            raise ValueError("reject_policy must be reduce or skip")
        if self.signal_path not in {"auto", "vector", "loop"}:
            raise ValueError("signal_path must be auto, vector, or loop")

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        *,
        base: OrderFlowConfig | None = None,
    ) -> OrderFlowConfig:
        """Build a config from a JSON-like mapping and reject unknown parameters.

        A report's ``parameters`` object can be fed back directly.  When ``base`` is supplied,
        only keys present in ``values`` override it; this is how the CLI combines a file with
        explicit command-line overrides without letting argparse defaults erase file values.
        """

        if not isinstance(values, Mapping):
            raise TypeError("order-flow config must be a mapping")
        raw = dict(values)
        nested = raw.pop("parameters", None)
        if nested is not None:
            if not isinstance(nested, Mapping):
                raise TypeError("config.parameters must be a mapping")
            nested_values = dict(nested)
            nested_values.update(raw)
            raw = nested_values
        raw.pop("order_flow_version", None)
        valid = {field.name for field in fields(cls)}
        unknown = sorted(set(raw).difference(valid))
        if unknown:
            raise ValueError("unknown order-flow config parameter(s): " + ", ".join(unknown))
        if base is None:
            return cls(**raw)
        merged = asdict(base)
        merged.update(raw)
        return cls(**merged)

    @classmethod
    def from_json(
        cls,
        path: str | Path,
        *,
        base: OrderFlowConfig | None = None,
    ) -> OrderFlowConfig:
        """Load a UTF-8 JSON parameter file."""

        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read order-flow config {source}: {exc}") from exc
        return cls.from_mapping(payload, base=base)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly versioned parameter snapshot."""

        values = asdict(self)
        values["order_flow_version"] = (
            ORDER_FLOW_V2_VERSION if self.strategy_version == "v2" else ORDER_FLOW_V1_VERSION
        )
        return values


def order_flow_version_for_strategy(strategy_version: StrategyVersion) -> str:
    """Return the protocol version associated with a strategy path."""

    if strategy_version == "v1":
        return ORDER_FLOW_V1_VERSION
    if strategy_version == "v2":
        return ORDER_FLOW_V2_VERSION
    raise ValueError("strategy_version must be v1 or v2")


__all__ = [
    "ORDER_FLOW_V1_VERSION",
    "ORDER_FLOW_V2_VERSION",
    "ORDER_FLOW_VERSION",
    "ExecutionMode",
    "OrderFlowConfig",
    "PositionMode",
    "RejectPolicy",
    "SignalPath",
    "StrategyVersion",
    "TransactionAlignment",
    "UnknownDirectionPolicy",
    "order_flow_version_for_strategy",
]
