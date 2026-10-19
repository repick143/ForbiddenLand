"""AKQuant backtest adapter that executes a precomputed signal table."""

from __future__ import annotations

from typing import Any, ClassVar

import akquant
import pandas as pd

ORDER_SIZE = 100
INITIAL_CASH = 100_000.0
BACKTEST_TIMEZONE = "Asia/Shanghai"


class SignalStrategy(akquant.Strategy):
    """Look up a precomputed (symbol, date) buy/sell signal at each bar close."""

    signals: ClassVar[dict[tuple[str, object], int]] = {}
    order_size = ORDER_SIZE

    def on_bar(self, bar: akquant.Bar) -> None:
        signal = self.signals.get((str(bar.symbol), _bar_date(bar.timestamp)), 0)
        position = self.get_position(bar.symbol)
        if signal == 1 and position == 0:
            self.buy(symbol=bar.symbol, quantity=self.order_size, tag="predict-entry")
        elif signal == 0 and position > 0:
            self.sell(symbol=bar.symbol, quantity=position, tag="predict-exit")


def _bar_date(timestamp_ns: int) -> object:
    """Convert AKQuant's UTC nanosecond bar label to the local trading date."""

    return (
        pd.Timestamp(int(timestamp_ns), unit="ns")
        .tz_localize("UTC")
        .tz_convert(BACKTEST_TIMEZONE)
        .date()
    )


def build_signal_map(signals: pd.DataFrame) -> dict[tuple[str, object], int]:
    """Convert the (timestamp, symbol, signal) table into the strategy lookup map."""

    result: dict[tuple[str, object], int] = {}
    for timestamp, symbol, signal in zip(
        signals["timestamp"], signals["symbol"], signals["signal"], strict=True
    ):
        result[(str(symbol), pd.Timestamp(timestamp).date())] = int(signal)
    return result


def run_backtest(
    frame: pd.DataFrame,
    signal_map: dict[tuple[str, object], int],
    symbols: tuple[str, ...],
    *,
    initial_cash: float = INITIAL_CASH,
    order_size: int = ORDER_SIZE,
) -> akquant.BacktestResult:
    """Run the signal strategy with explicit A-share execution assumptions."""

    SignalStrategy.signals = signal_map
    SignalStrategy.order_size = order_size
    return akquant.run_backtest(
        data=frame,
        strategy=SignalStrategy,
        symbols=list(symbols),
        initial_cash=initial_cash,
        commission_rate=0.0003,
        stamp_tax_rate=0.001,
        transfer_fee_rate=0.00001,
        min_commission=0.0,
        slippage={"type": "percent", "value": 0.0002},
        lot_size=order_size,
        t_plus_one=True,
        warmup_period=0,
        history_depth=0,
        fill_policy=akquant.NextOpen(),
        timezone=BACKTEST_TIMEZONE,
        show_progress=False,
    )


def summarize_backtest(result: akquant.BacktestResult) -> dict[str, Any]:
    """Extract a compact, serializable summary from an AKQuant result."""

    metrics_frame = result.metrics_df
    metric_names = (
        "start_time",
        "end_time",
        "total_bars",
        "closed_trade_count",
        "execution_count",
        "initial_market_value",
        "end_market_value",
        "total_pnl",
        "total_return_pct",
        "max_drawdown_pct",
        "win_rate",
    )
    metrics: dict[str, Any] = {}
    for name in metric_names:
        if name not in metrics_frame.index:
            continue
        value = metrics_frame.loc[name, "value"]
        if hasattr(value, "item"):
            value = value.item()
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        metrics[name] = value
    return metrics


def buy_and_hold_returns(frame: pd.DataFrame, symbols: tuple[str, ...]) -> dict[str, float]:
    """Per-symbol buy-and-hold return over the (already windowed) frame."""

    result: dict[str, float] = {}
    for symbol in symbols:
        group = frame[frame["symbol"] == symbol].sort_values("timestamp")
        if len(group) >= 2:
            result[symbol] = float(group["close"].iloc[-1] / group["close"].iloc[0] - 1.0)
    return result
