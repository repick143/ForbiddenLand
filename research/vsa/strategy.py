"""AKQuant execution strategy for the pre-computed daily VSA signals."""

from __future__ import annotations

from typing import Any

import akquant
import numpy as np

from .features import VSA_FEATURE_VERSION
from .rules import VSA_RULE_VERSION

VSA_ORDER_SIZE = 100
VSA_MAX_HOLD_BARS = 20


def _extra_number(extra: Any, name: str, default: float = 0.0) -> float:
    """Read an AKQuant extra field without turning missing values into a signal."""

    if not isinstance(extra, dict):
        return default
    value = extra.get(name, default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


class VSAStrategy(akquant.Strategy):
    """Long-only daily VSA strategy with explicit A-share execution assumptions.

    VSA labels are generated before the backtest and carried through ``Bar.extra``.  The strategy
    does not recompute rolling values at runtime, which keeps provider, feature, and execution
    responsibilities separate.  A bullish confirmed signal submits an entry and defers its stop
    until the first T+1-sellable bar; a bearish confirmation or a maximum holding period exits
    only the currently sellable quantity.
    """

    order_size = VSA_ORDER_SIZE
    max_hold_bars = VSA_MAX_HOLD_BARS
    risk_reward = 2.0

    def _state(self) -> tuple[dict[str, tuple[float, float]], dict[str, float], set[str]]:
        """Lazily initialize per-symbol protection state for AKQuant's strategy lifecycle."""

        pending = getattr(self, "_vsa_pending_protection", None)
        if not isinstance(pending, dict):
            pending = {}
            self._vsa_pending_protection = pending
        targets = getattr(self, "_vsa_target_levels", None)
        if not isinstance(targets, dict):
            targets = {}
            self._vsa_target_levels = targets
        stops = getattr(self, "_vsa_stop_submitted", None)
        if not isinstance(stops, set):
            stops = set()
            self._vsa_stop_submitted = stops
        return pending, targets, stops

    def _record_features(self, bar: akquant.Bar) -> None:
        """Record chart-friendly numeric fields; undefined ratios remain absent."""

        extra = bar.extra if isinstance(bar.extra, dict) else {}
        data_valid = _extra_number(extra, "vsa_data_valid") > 0.0
        history_ready = _extra_number(extra, "vsa_history_ready") > 0.0
        volume_baseline = _extra_number(extra, "vsa_volume_baseline")
        spread_baseline = _extra_number(extra, "vsa_spread_baseline")
        spread = _extra_number(extra, "vsa_spread")
        confirmed_signal = round(_extra_number(extra, "vsa_confirmed_signal"))
        metadata = {
            "feature_version": VSA_FEATURE_VERSION,
            "rule_version": VSA_RULE_VERSION,
        }
        definitions = (
            ("vsa_volume_ratio", 1, "line", "ratio", 4, "#2563eb"),
            ("vsa_spread_ratio", 1, "line", "ratio", 4, "#9333ea"),
            ("vsa_clv", 1, "line", "ratio", 4, "#0891b2"),
            ("vsa_candidate_code", 2, "histogram", "code", 0, "#f59e0b"),
            ("vsa_confirmed_signal", 2, "histogram", "signal", 0, "#16a34a"),
            ("vsa_stop_price", 0, "line", "price", 3, "#dc2626"),
            ("vsa_target_price", 0, "line", "price", 3, "#16a34a"),
        )
        for name, pane, render_type, unit, precision, color in definitions:
            # AKQuant's DataFrame adapter uses zero for missing numeric extras.  Reconstruct the
            # domain validity at this boundary so warm-up/undefined observations stay sparse in
            # indicator output instead of becoming plausible-looking zeroes.
            if name == "vsa_volume_ratio" and not (
                data_valid and history_ready and volume_baseline > 0.0
            ):
                continue
            if name == "vsa_spread_ratio" and not (
                data_valid and history_ready and spread_baseline > 0.0
            ):
                continue
            if name == "vsa_clv" and not (data_valid and spread > 0.0):
                continue
            if name in {"vsa_stop_price", "vsa_target_price"} and confirmed_signal <= 0:
                continue
            value = _extra_number(extra, name, default=float("nan"))
            if not np.isfinite(value):
                continue
            self.record_indicator(
                name,
                value,
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                display_name=name,
                pane=pane,
                render_type=render_type,
                unit=unit,
                precision=precision,
                color=color,
                meta=metadata,
            )

    def on_bar(self, bar: akquant.Bar) -> None:
        self._record_features(bar)
        symbol = str(bar.symbol)
        signal = round(_extra_number(bar.extra, "vsa_confirmed_signal"))
        position = self.get_position(symbol)
        open_orders = self.get_open_orders(symbol)
        pending, targets, stops = self._state()

        if position <= 0.0:
            if not open_orders and signal <= 0:
                pending.pop(symbol, None)
                targets.pop(symbol, None)
                stops.discard(symbol)
            if signal <= 0 or open_orders:
                return
            stop_price = _extra_number(bar.extra, "vsa_stop_price", default=float("nan"))
            target_price = _extra_number(bar.extra, "vsa_target_price", default=float("nan"))
            if not np.isfinite(stop_price) or not np.isfinite(target_price):
                return
            if stop_price <= 0.0 or target_price <= stop_price or target_price <= bar.close:
                return
            # T+1 means a protective sell submitted in the same callback as the buy is rejected
            # because the newly acquired shares are not sellable yet.  Store the levels and install
            # the stop on the first later bar with available position; the run-level NextOpen policy
            # still makes this confirmation a next-bar entry.
            self.buy(
                symbol=symbol,
                quantity=self.order_size,
                tag="vsa-confirmed-entry",
            )
            pending[symbol] = (stop_price, target_price)
            return

        stop_price: float | None = None
        target_price = targets.get(symbol)
        if symbol in pending and self.get_available_position(symbol) > 0.0:
            stop_price, target_price = pending.pop(symbol)
            rounder = getattr(self, "round_to_tick", None)
            if callable(rounder):
                stop_price = float(rounder(symbol, stop_price, "down"))
            else:
                stop_price = round(float(stop_price), 2)
            # An overnight gap can put the next open below the candidate-derived stop.  Keep the
            # stop below the observed post-entry price rather than installing an immediately
            # invalid level above the market; the gap loss remains visible in the trade result.
            if stop_price >= bar.close:
                stop_price = bar.close * 0.997
                if callable(rounder):
                    stop_price = float(rounder(symbol, stop_price, "down"))
                else:
                    stop_price = round(float(stop_price), 2)
            if target_price <= bar.close:
                target_price = bar.close + self.risk_reward * (bar.close - stop_price)
            targets[symbol] = target_price
            # Keep one real stop order active.  The target is monitored from the bar's high and
            # exits at the next open, which avoids a second sell order reserving the same T+1 lot.
            if symbol not in stops:
                available = self.get_available_position(symbol)
                if available > 0.0:
                    receipt = self.sell(
                        symbol=symbol,
                        quantity=available,
                        trigger_price=stop_price,
                        tag="vsa-stop-loss",
                    )
                    order_id = str(getattr(receipt, "primary", receipt))
                    order = self.get_order(order_id) if order_id else None
                    status = str(getattr(order, "status", "")).casefold()
                    if "reject" not in status:
                        stops.add(symbol)

        should_exit = signal < 0
        if target_price is not None and np.isfinite(target_price) and bar.high >= target_price:
            should_exit = True
        if not should_exit and self.get_holding_bars(symbol) >= self.max_hold_bars:
            should_exit = True
        if not should_exit:
            return

        # T+1 can leave a newly bought quantity temporarily unavailable.  Keep its protective
        # bracket in place rather than cancelling it and submitting an unfillable exit.
        available = self.get_available_position(symbol)
        if available <= 0.0:
            return
        self.cancel_all_orders(symbol)
        pending.pop(symbol, None)
        targets.pop(symbol, None)
        stops.discard(symbol)
        self.sell(
            symbol=symbol,
            quantity=available,
            tag="vsa-confirmed-exit" if signal < 0 else "vsa-time-exit",
        )


__all__ = ["VSA_MAX_HOLD_BARS", "VSA_ORDER_SIZE", "VSAStrategy"]
