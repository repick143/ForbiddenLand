"""easy-tdx BacktestEngine strategy for the order-flow proxy signals."""

from __future__ import annotations

from typing import Any

import numpy as np

from .config import OrderFlowConfig

try:  # easy-tdx is an optional data/research dependency for the project.
    from easy_tdx.backtest import Strategy as _EasyTdxStrategy
except ImportError:  # pragma: no cover - exercised only in a minimal installation.

    class _EasyTdxStrategy:  # type: ignore[no-redef]
        """Import-time placeholder that keeps feature modules usable without easy-tdx."""

        def __init__(self) -> None:
            self._bar_index = 0


def _number(strategy: Any, name: str, default: float = float("nan")) -> float:
    """Read one custom DataProxy field without turning missing data into a signal."""

    try:
        value = getattr(strategy.data, name)[0]
    except (AttributeError, RuntimeError, IndexError, TypeError):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


class OrderFlowStrategy(_EasyTdxStrategy):
    """Long-only strategy consuming precomputed causal ``of_*`` columns.

    The strategy deliberately emits ordinary market signals and lets easy-tdx apply its configured
    next-bar execution and fees.  It does not use the engine's bracket stops because those stops can
    fire on the same calendar day on an intraday frame; close/high/low risk exits below honor the
    configured T+1 gate instead.
    """

    config = OrderFlowConfig()

    def init(self) -> None:
        self._entry_signal_index: int | None = None
        self._entry_available_date: int | None = None
        self._entry_reference_price: float | None = None
        self._exit_pending = False
        self._cooldown_until = -1
        self._last_exit_reason = ""

    def _size(self) -> float:
        settings = self.config
        if settings.position_mode == "full":
            return 0.0
        if settings.position_mode == "fixed":
            return float(settings.order_size)
        return float(settings.position_fraction)

    def _current_date(self) -> int:
        value = _number(self, "session_date_key")
        return int(value) if np.isfinite(value) else 0

    def _entry_date_for_execution(self, current_date: int) -> int:
        """Use the next bar's calendar date as the T+1 availability boundary.

        The column is a calendar/schedule key prepared before the backtest; it contains no future
        price or flow value.  Falling back to the current date is conservative for hand-built test
        frames that omit it.
        """

        value = _number(self, "next_session_date_key", float(current_date))
        return int(value) if np.isfinite(value) else current_date

    def _can_exit(self, current_date: int) -> bool:
        settings = self.config
        if self._entry_signal_index is None:
            return False
        if self._bar_index - self._entry_signal_index < settings.min_hold_bars:
            return False
        return not (
            settings.t_plus_one
            and self._entry_available_date is not None
            and current_date <= self._entry_available_date
        )

    def _risk_exit(self) -> str:
        settings = self.config
        reference = self._entry_reference_price
        if reference is None or not np.isfinite(reference) or reference <= 0:
            return ""
        close = _number(self, "close")
        low = _number(self, "low")
        high = _number(self, "high")
        if (
            settings.stop_loss_pct > 0.0
            and np.isfinite(low)
            and low <= reference * (1.0 - settings.stop_loss_pct)
        ):
            return "stop_loss"
        if (
            settings.take_profit_pct > 0.0
            and np.isfinite(high)
            and high >= reference * (1.0 + settings.take_profit_pct)
        ):
            return "take_profit"
        # ``close`` is read to make malformed custom frames fail closed rather than emit an exit.
        if not np.isfinite(close):
            return ""
        return ""

    def next(self) -> None:
        settings = self.config
        current_date = self._current_date()
        position = float(self.position.get("size", 0.0))

        if position <= 0.0:
            if self._exit_pending:
                self._exit_pending = False
                self._entry_signal_index = None
                self._entry_available_date = None
                self._entry_reference_price = None
            if self._bar_index < self._cooldown_until:
                return
            if _number(self, "of_data_valid", 0.0) <= 0.0:
                return
            if _number(self, "of_entry_signal", 0.0) <= 0.0:
                return
            self.buy(size=self._size())
            self._entry_signal_index = self._bar_index
            self._entry_available_date = self._entry_date_for_execution(current_date)
            reference = _number(self, "close")
            self._entry_reference_price = reference if np.isfinite(reference) else None
            self._last_exit_reason = ""
            return

        if self._entry_signal_index is None:
            # This branch supports an engine-injected initial position gracefully.
            self._entry_signal_index = self._bar_index
            self._entry_available_date = current_date
            reference = _number(self, "close")
            self._entry_reference_price = reference if np.isfinite(reference) else None
        if self._exit_pending or not self._can_exit(current_date):
            return

        reason = self._risk_exit()
        if (
            not reason
            and settings.max_hold_bars is not None
            and self._bar_index - self._entry_signal_index >= settings.max_hold_bars
        ):
            reason = "max_hold"
        if not reason and settings.flat_at_session_end and _number(self, "is_session_last") > 0.0:
            reason = "session_end"
        if not reason and _number(self, "of_exit_signal") > 0.0:
            reason = "supply_or_divergence"
        if reason:
            self.sell(size=0.0)
            self._exit_pending = True
            self._last_exit_reason = reason
            self._cooldown_until = self._bar_index + settings.cooldown_bars


def make_order_flow_strategy(config: OrderFlowConfig | None = None) -> type[OrderFlowStrategy]:
    """Return an easy-tdx strategy class bound to an immutable configuration."""

    settings = config or OrderFlowConfig()

    class ConfiguredOrderFlowStrategy(OrderFlowStrategy):
        config = settings

    ConfiguredOrderFlowStrategy.__name__ = "ConfiguredOrderFlowStrategy"
    ConfiguredOrderFlowStrategy.__qualname__ = "ConfiguredOrderFlowStrategy"
    return ConfiguredOrderFlowStrategy


__all__ = ["OrderFlowStrategy", "make_order_flow_strategy"]
