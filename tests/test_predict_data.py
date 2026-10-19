from __future__ import annotations

import pandas as pd
import pytest

from research.predict import data as data_mod


def _fake_fetcher(symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
    rows = []
    for index in range(5):
        rows.append(
            {
                "date": pd.Timestamp("2024-01-02") + pd.offsets.BDay(index),
                "open": 10.0 + index,
                "high": 11.0 + index,
                "low": 9.0 + index,
                "close": 10.5 + index,
                "volume": 1000.0 + index,
            }
        )
    return pd.DataFrame(rows)


def test_fetch_bars_normalizes_with_injected_fetcher() -> None:
    batch = data_mod.fetch_bars(("688256",), "20240101", "20240131", "qfq", fetcher=_fake_fetcher)
    assert list(batch.frame.columns) == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "symbol",
    ]
    assert set(batch.frame["symbol"]) == {"688256"}
    assert batch.source == "custom remote fetcher"
    assert batch.frame["timestamp"].is_monotonic_increasing


def test_fetch_bars_rejects_missing_columns() -> None:
    def bad_fetcher(symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
        return pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "close": [10.0]})

    with pytest.raises(RuntimeError, match="missing columns"):
        data_mod.fetch_bars(("688256",), "20240101", "20240131", "qfq", fetcher=bad_fetcher)


def test_fetch_bars_rejects_inverted_window() -> None:
    with pytest.raises(ValueError, match="start_date"):
        data_mod.fetch_bars(("688256",), "20240131", "20240101", "qfq", fetcher=_fake_fetcher)
