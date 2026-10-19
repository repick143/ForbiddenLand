from __future__ import annotations

from datetime import date

import pandas as pd
from predict_fixtures import make_bars

from research.predict.baseline.features import FEATURE_COLUMNS, compute_features, limit_ratio


def test_limit_ratio_rules() -> None:
    assert limit_ratio("600183") == 0.10
    assert limit_ratio("000001") == 0.10
    assert limit_ratio("300750") == 0.20
    assert limit_ratio("301000") == 0.20
    assert limit_ratio("688256") == 0.20
    assert limit_ratio("689009") == 0.20


def test_features_keep_expected_columns() -> None:
    out = compute_features(make_bars())
    expected = [
        "timestamp",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
        *FEATURE_COLUMNS,
    ]
    assert list(out.columns) == expected
    assert len(out) == len(make_bars())


def test_warmup_rows_are_missing_not_zero() -> None:
    out = compute_features(make_bars(days=25))
    first = out.groupby("symbol").head(1)
    assert first["ret_1"].isna().all()
    assert first["ret_5"].isna().all()
    assert first["ma_dev_5"].isna().all()
    assert first["volume_ratio"].isna().all()


def test_limit_flags_on_constructed_bars() -> None:
    rows = [
        {
            "timestamp": "2024-01-02",
            "symbol": "600183",
            "open": 10.0,
            "high": 10.5,
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
            "high": 11.2,
            "low": 10.8,
            "close": 11.1,
            "volume": 1000.0,
        },
    ]
    out = compute_features(pd.DataFrame(rows))
    flags = out.set_index(pd.to_datetime(out["timestamp"]).dt.date)["is_limit_up"]
    assert int(flags.loc[date(2024, 1, 3)]) == 1
    assert int(flags.loc[date(2024, 1, 4)]) == 0
