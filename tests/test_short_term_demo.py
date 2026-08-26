from __future__ import annotations

import json
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
duckdb = pytest.importorskip("duckdb")

from research.short_term.demo import (
    CLOSE_PATHS,
    STOCKS,
    build_fixture,
    build_report,
    fetch_remote_data,
    load_from_duckdb,
    normalize_remote_frame,
    run_backtest,
    write_report,
)


def test_fixture_uses_standard_project_securities() -> None:
    frame = build_fixture()

    assert set(frame["symbol"]) == set(STOCKS)
    assert len(frame) == sum(len(path) for path in CLOSE_PATHS.values())
    assert all(isinstance(symbol, str) for symbol in frame["symbol"])
    assert frame["close"].notna().all()


def test_duckdb_round_trip_and_akquant_backtest(tmp_path: Path) -> None:
    database = tmp_path / "short_term.duckdb"
    loaded = load_from_duckdb(build_fixture(), database)

    connection = duckdb.connect(str(database), read_only=True)
    try:
        row_count = connection.execute("SELECT COUNT(*) FROM short_term_bars").fetchone()[0]
    finally:
        connection.close()

    assert row_count == len(loaded) == 60
    result = run_backtest(loaded)
    metrics = result.metrics_df
    assert int(metrics.loc["total_bars", "value"]) == 20
    assert float(metrics.loc["closed_trade_count", "value"]) > 0
    assert set(result.trades_df["symbol"]).issubset(STOCKS)


def test_remote_fetch_path_normalizes_all_standard_symbols() -> None:
    def fake_fetcher(symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
        assert (start_date, end_date, adjust) == ("20240101", "20240131", "qfq")
        return pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "open": [10.0, 10.5],
                "high": [10.8, 11.0],
                "low": [9.8, 10.2],
                "close": [10.5, 10.8],
                "volume": [100.0, 120.0],
            }
        )

    result = fetch_remote_data("20240101", "20240131", "qfq", fetcher=fake_fetcher)

    assert len(result) == 6
    assert set(result["symbol"]) == set(STOCKS)
    assert result["timestamp"].is_monotonic_increasing
    assert all(isinstance(symbol, str) for symbol in result["symbol"])


def test_remote_response_schema_is_validated() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        normalize_remote_frame(pd.DataFrame({"date": ["2024-01-02"]}), "688256")


def test_report_is_json_serializable(tmp_path: Path) -> None:
    result = run_backtest(load_from_duckdb(build_fixture(), tmp_path / "short_term.duckdb"))
    report = build_report(
        result,
        source="deterministic synthetic fixture (offline test only)",
        storage="DuckDB",
    )
    output = tmp_path / "short_term.json"
    write_report(report, output)

    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["engine"] == "akquant"
    assert loaded["data"]["storage"] == "DuckDB"
    assert {item["code"] for item in loaded["symbols"]} == set(STOCKS)
