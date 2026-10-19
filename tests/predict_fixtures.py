"""Deterministic OHLCV fixtures for the prediction-baseline tests."""

from __future__ import annotations

import pandas as pd


def make_bars(
    symbols: tuple[str, ...] = ("688256", "600183"),
    days: int = 80,
    start: str = "2024-01-02",
) -> pd.DataFrame:
    """Build deterministic business-day OHLCV bars with a mild next-day signal.

    Daily returns alternate (+1%, -1%) so the next-day direction is the exact opposite of today's
    close-to-close sign; a model can learn it while the naive yesterday-continuation baseline fails.
    """

    rows: list[dict[str, object]] = []
    pattern = (0.01, -0.01)
    for symbol_index, symbol in enumerate(symbols):
        close = 10.0 + symbol_index * 5.0
        for index in range(days):
            timestamp = pd.Timestamp(start) + pd.offsets.BDay(index)
            previous_close = close
            open_price = previous_close * 1.001
            close = previous_close * (1.0 + pattern[index % 2])
            rows.append(
                {
                    "timestamp": timestamp,
                    "open": open_price,
                    "high": max(open_price, close) * 1.01,
                    "low": min(open_price, close) * 0.99,
                    "close": close,
                    "volume": float(1_000_000 + index * 1_000),
                    "symbol": symbol,
                }
            )
    return pd.DataFrame(rows).sort_values(["timestamp", "symbol"]).reset_index(drop=True)
