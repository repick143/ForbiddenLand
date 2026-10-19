"""Next-day open-to-close labels and tradability exclusions.

A signal decided at the T close trades at the T+1 open and is evaluated at the T+1 close, so the
label is the sign (and magnitude) of open_{T+1} -> close_{T+1}. Rows where that trade cannot
happen are excluded with a recorded reason instead of being silently dropped.
"""

from __future__ import annotations

import pandas as pd

from .features import limit_ratio

_EPS = 1e-6


def build_labels(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach next-day labels and an eligibility marker with recorded reasons."""

    data = frame.sort_values(["symbol", "timestamp"]).copy()
    rows: list[pd.DataFrame] = []
    for symbol, group in data.groupby("symbol", sort=False):
        g = group.sort_values("timestamp").copy()
        limit = limit_ratio(str(symbol))
        prev_close = g["close"].shift(1)
        next_open = g["open"].shift(-1)
        next_close = g["close"].shift(-1)
        g["label_return"] = next_close / next_open - 1.0

        reasons = pd.Series("", index=g.index, dtype="object")
        missing_next = next_open.isna() | next_close.isna()
        t_close_limit_up = g["close"] >= prev_close * (1.0 + limit) - _EPS
        next_open_limit_up = next_open >= g["close"] * (1.0 + limit) - _EPS
        reasons.loc[missing_next] = "no_next_bar"
        reasons.loc[~missing_next & t_close_limit_up] = "t_close_limit_up"
        reasons.loc[~missing_next & ~t_close_limit_up & next_open_limit_up] = "next_open_limit_up"
        g["exclude_reason"] = reasons
        g["eligible"] = (g["exclude_reason"] == "").astype(bool)

        g["label"] = pd.Series(pd.NA, index=g.index, dtype="Int64")
        g.loc[g["eligible"], "label"] = (g.loc[g["eligible"], "label_return"] > 0.0).astype(int)
        rows.append(g)

    return (
        pd.concat(rows, ignore_index=True)
        .sort_values(["timestamp", "symbol"])
        .reset_index(drop=True)
    )
