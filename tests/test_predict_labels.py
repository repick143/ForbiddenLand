from __future__ import annotations

from datetime import date

import pandas as pd
from predict_fixtures import make_bars

from research.predict.baseline.labels import build_labels


def test_label_matches_next_open_to_close_direction() -> None:
    labeled = build_labels(make_bars(days=10))
    rows = []
    for symbol, group in labeled.sort_values(["symbol", "timestamp"]).groupby("symbol"):
        rows.append((group["close"].shift(-1) > group["open"].shift(-1)).astype(int))
    expected = pd.concat(rows).sort_index()
    eligible = labeled[labeled["eligible"]]
    assert list(eligible["label"]) == list(expected[eligible.index])


def test_last_bar_per_symbol_is_excluded() -> None:
    labeled = build_labels(make_bars(days=5))
    last = labeled.groupby("symbol").tail(1)
    assert not last["eligible"].any()
    assert (last["exclude_reason"] == "no_next_bar").all()
    assert last["label"].isna().all()


def test_limit_up_rows_are_excluded_with_reason() -> None:
    rows = [
        {
            "timestamp": "2024-01-02",
            "symbol": "600183",
            "open": 10.0,
            "high": 10.2,
            "low": 9.9,
            "close": 10.0,
            "volume": 1000.0,
        },
        {
            "timestamp": "2024-01-03",
            "symbol": "600183",
            "open": 10.0,
            "high": 11.5,
            "low": 10.0,
            "close": 11.0,
            "volume": 1000.0,
        },
        {
            "timestamp": "2024-01-04",
            "symbol": "600183",
            "open": 11.0,
            "high": 11.5,
            "low": 11.0,
            "close": 11.0,
            "volume": 1000.0,
        },
        {
            "timestamp": "2024-01-05",
            "symbol": "600183",
            "open": 12.1,
            "high": 12.2,
            "low": 11.1,
            "close": 11.1,
            "volume": 1000.0,
        },
    ]
    labeled = build_labels(pd.DataFrame(rows))
    by_date = labeled.set_index(pd.to_datetime(labeled["timestamp"]).dt.date)
    assert by_date.loc[date(2024, 1, 3), "exclude_reason"] == "t_close_limit_up"
    assert by_date.loc[date(2024, 1, 4), "exclude_reason"] == "next_open_limit_up"
    assert by_date.loc[date(2024, 1, 5), "exclude_reason"] == "no_next_bar"
    assert bool(by_date.loc[date(2024, 1, 2), "eligible"])
