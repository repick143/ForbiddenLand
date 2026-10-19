"""Causal, bar-close-only feature construction for the prediction baseline.

Every feature at date T uses only information observable at the T close. Rolling statistics are
computed per symbol and may include the current bar (known at close); nothing looks ahead.
"""

from __future__ import annotations

import pandas as pd

FEATURE_COLUMNS = (
    "gap",
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_20",
    "ma_dev_5",
    "ma_dev_10",
    "ma_dev_20",
    "vol_20",
    "high_low_range",
    "body",
    "volume_ratio",
    "is_limit_up",
    "is_limit_down",
)

_KEEP_COLUMNS = ("timestamp", "symbol", "open", "high", "low", "close", "volume")
_EPS = 1e-6


def limit_ratio(symbol: str) -> float:
    """Approximate the daily price-limit ratio for a six-digit A-share code.

    ChiNext (300/301) and STAR Market (688/689) use 20%; other main-board codes use 10%.
    ST stocks (5%) and first-IPO sessions are not detected because security names are unavailable.
    """

    return 0.20 if str(symbol).startswith(("300", "301", "688", "689")) else 0.10


def compute_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal technical features, one row per (symbol, trading day)."""

    data = frame.sort_values(["symbol", "timestamp"]).copy()
    rows: list[pd.DataFrame] = []
    for symbol, group in data.groupby("symbol", sort=False):
        g = group.sort_values("timestamp").copy()
        limit = limit_ratio(str(symbol))
        prev_close = g["close"].shift(1)
        g["gap"] = g["open"] / prev_close - 1.0
        g["ret_1"] = g["close"] / prev_close - 1.0
        for window in (5, 10, 20):
            g[f"ret_{window}"] = g["close"] / g["close"].shift(window) - 1.0
        for window in (5, 10, 20):
            ma = g["close"].rolling(window).mean()
            g[f"ma_dev_{window}"] = g["close"] / ma - 1.0
        g["vol_20"] = g["ret_1"].rolling(20).std(ddof=0)
        g["high_low_range"] = (g["high"] - g["low"]) / prev_close
        g["body"] = (g["close"] - g["open"]) / prev_close
        g["volume_ratio"] = g["volume"] / g["volume"].rolling(5).mean()
        g["is_limit_up"] = (g["close"] >= prev_close * (1.0 + limit) - _EPS).astype(int)
        g["is_limit_down"] = (g["close"] <= prev_close * (1.0 - limit) + _EPS).astype(int)
        rows.append(g)
    result = pd.concat(rows, ignore_index=True)
    columns = [*_KEEP_COLUMNS, *FEATURE_COLUMNS]
    return result[columns].sort_values(["timestamp", "symbol"]).reset_index(drop=True)
