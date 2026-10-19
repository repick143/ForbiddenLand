from __future__ import annotations

from datetime import date

import pandas as pd
from predict_fixtures import make_bars

from research.predict.baseline import backtest as bt


def test_build_signal_map_normalizes_dates() -> None:
    signals = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2024-01-05")],
            "symbol": ["688256"],
            "signal": [1],
        }
    )
    signal_map = bt.build_signal_map(signals)
    assert signal_map == {("688256", date(2024, 1, 5)): 1}


def test_backtest_executes_buy_and_sell_signals() -> None:
    frame = make_bars(days=30, symbols=("688256",))
    signals = frame.iloc[10:20].copy()
    signals["signal"] = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    signal_map = bt.build_signal_map(signals[["timestamp", "symbol", "signal"]])
    result = bt.run_backtest(frame, signal_map, ("688256",), order_size=100)
    summary = bt.summarize_backtest(result)
    assert summary["execution_count"] >= 2
    assert summary["closed_trade_count"] >= 1


def test_buy_and_hold_returns() -> None:
    frame = make_bars(days=10, symbols=("688256", "600183"))
    returns = bt.buy_and_hold_returns(frame, ("688256", "600183"))
    assert set(returns) == {"688256", "600183"}
    assert all(pd.notna(value) for value in returns.values())
