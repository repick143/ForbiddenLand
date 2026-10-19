"""Offline end-to-end tests for the split training and prediction CLIs."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from predict_fixtures import make_bars

from ecom_data.model import BarRequest, QuoteRequest
from ecom_data.sources import DataSource, register_source
from research.predict.baseline import data as data_mod
from research.predict.baseline import features as feat
from research.predict.baseline import predict as predict_mod
from research.predict.baseline import train as train_mod


class FakeSource(DataSource):
    """Deterministic in-memory source registered under ``fake``."""

    name = "fake"

    def __init__(self, bars_by_code: dict[str, list[dict]] | None = None) -> None:
        self.bars_by_code = bars_by_code or {}
        self.last_fetch: dict[str, dict] = {}

    @property
    def source_version(self) -> str | None:
        return "fake-1.0"

    def fetch_bars(self, request: BarRequest) -> list[dict]:
        self.last_fetch[request.code] = {
            "source": "fake-remote",
            "storage": "in-memory",
            "retrieved_at_utc": datetime(2026, 1, 1, tzinfo=UTC),
        }
        return list(self.bars_by_code.get(request.code, []))

    def fetch_quote(self, request: QuoteRequest) -> dict | None:
        return None


def _bars_for_symbols(symbols: tuple[str, ...]) -> dict[str, list[dict]]:
    """Convert the deterministic fixture frame into ecom-data bar dicts per symbol."""

    frame = make_bars(symbols=symbols, days=260)
    by_symbol: dict[str, list[dict]] = {}
    for symbol, group in frame.groupby("symbol", sort=False):
        rows = []
        for _, row in group.sort_values("timestamp").iterrows():
            rows.append(
                {
                    "datetime": pd.Timestamp(row["timestamp"]).isoformat(timespec="seconds"),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "vol": float(row["volume"]),
                }
            )
        by_symbol[str(symbol)] = rows
    return by_symbol


def _install_fake_source(
    monkeypatch: pytest.MonkeyPatch,
    symbols: tuple[str, ...] = ("688256", "600183"),
) -> FakeSource:
    source = FakeSource(_bars_for_symbols(symbols))
    register_source(source)
    monkeypatch.setattr(data_mod, "get_source", lambda name="akshare": source)
    return source


def test_build_dataset_splits_chronologically(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_source(monkeypatch)
    dataset = train_mod.build_dataset(
        ("688256", "600183"),
        "20240101",
        "20241231",
        "qfq",
        train_mod.SplitDates(train_end=date(2024, 5, 1), valid_end=date(2024, 8, 1)),
    )
    assert list(dataset.features) == list(feat.FEATURE_COLUMNS)
    assert dataset.batch.source == "fake-remote"
    assert len(dataset.train_df) > 50
    assert len(dataset.valid_df) > 20
    assert len(dataset.test_df) > 20
    assert dataset.train_df["timestamp"].max().date() <= date(2024, 5, 1)
    assert dataset.valid_df["timestamp"].min().date() > date(2024, 5, 1)
    assert dataset.valid_df["timestamp"].max().date() <= date(2024, 8, 1)
    assert dataset.test_df["timestamp"].min().date() > date(2024, 8, 1)


def test_train_cli_persists_model_and_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_source(monkeypatch)
    model_path = tmp_path / "model.ubj"
    report_path = tmp_path / "train.json"
    rc = train_mod.main(
        [
            "--symbols",
            "688256",
            "600183",
            "--start-date",
            "20240101",
            "--end-date",
            "20241231",
            "--train-end",
            "20240501",
            "--valid-end",
            "20240801",
            "--model-cache",
            str(model_path),
            "--report",
            str(report_path),
        ]
    )
    assert rc == 0
    assert model_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["model_cache"]["mode"] == "saved"
    assert report["model_cache"]["fingerprint"]
    assert report["metrics"]["test"]["auc"] is not None
    assert report["metrics"]["threshold"] > 0
    assert report["outputs"]["model"] == str(model_path)
    assert report["symbols_file"] is None
    assert report["skipped"] == {}
    assert set(report["timing"]) == {
        "fetch_seconds",
        "features_seconds",
        "labels_seconds",
        "split_seconds",
        "train_seconds",
        "total_seconds",
    }
    assert report["timing"]["train_seconds"] > 0


def test_train_cli_symbols_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_source(monkeypatch)
    symbols_file = tmp_path / "symbols.txt"
    symbols_file.write_text(
        "# comment line\n688256 寒武纪\n600183 生益科技\n\n688256\n",
        encoding="utf-8",
    )
    model_path = tmp_path / "model.ubj"
    report_path = tmp_path / "train.json"
    rc = train_mod.main(
        [
            "--symbols-file",
            str(symbols_file),
            "--start-date",
            "20240101",
            "--end-date",
            "20241231",
            "--train-end",
            "20240501",
            "--valid-end",
            "20240801",
            "--model-cache",
            str(model_path),
            "--report",
            str(report_path),
        ]
    )
    assert rc == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["symbols_file"] == str(symbols_file)
    assert report["universe"] == ["688256", "600183"]
    assert model_path.is_file()


def test_train_cli_symbols_file_conflicts_with_symbols(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_source(monkeypatch)
    symbols_file = tmp_path / "symbols.txt"
    symbols_file.write_text("688256\n", encoding="utf-8")
    rc = train_mod.main(
        [
            "--symbols",
            "688256",
            "--symbols-file",
            str(symbols_file),
            "--start-date",
            "20240101",
            "--end-date",
            "20241231",
            "--train-end",
            "20240501",
            "--valid-end",
            "20240801",
        ]
    )
    assert rc == 2


def test_train_cli_symbols_file_rejects_invalid_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_source(monkeypatch)
    symbols_file = tmp_path / "symbols.txt"
    symbols_file.write_text("12345\n", encoding="utf-8")
    rc = train_mod.main(
        [
            "--symbols-file",
            str(symbols_file),
            "--start-date",
            "20240101",
            "--end-date",
            "20241231",
            "--train-end",
            "20240501",
            "--valid-end",
            "20240801",
        ]
    )
    assert rc == 2


def test_train_cli_accepts_source_and_bar_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_source(monkeypatch)
    report_path = tmp_path / "train.json"
    rc = train_mod.main(
        [
            "--symbols",
            "688256",
            "600183",
            "--source",
            "easy-tdx",
            "--bar-count",
            "2500",
            "--start-date",
            "20240101",
            "--end-date",
            "20241231",
            "--train-end",
            "20240501",
            "--valid-end",
            "20240801",
            "--report",
            str(report_path),
        ]
    )
    assert rc == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["window"]["source"] == "easy-tdx"
    assert report["window"]["bar_count"] == 2500


def test_predict_cli_loads_model_and_backtests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_source(monkeypatch)
    model_path = tmp_path / "model.ubj"
    train_report_path = tmp_path / "train.json"
    assert (
        train_mod.main(
            [
                "--symbols",
                "688256",
                "600183",
                "--start-date",
                "20240101",
                "--end-date",
                "20241231",
                "--train-end",
                "20240501",
                "--valid-end",
                "20240801",
                "--model-cache",
                str(model_path),
                "--report",
                str(train_report_path),
            ]
        )
        == 0
    )
    train_report = json.loads(train_report_path.read_text(encoding="utf-8"))

    signals_path = tmp_path / "signals.csv"
    predict_report_path = tmp_path / "predict.json"
    rc = predict_mod.main(
        [
            "--symbols",
            "688256",
            "600183",
            "--start-date",
            "20240101",
            "--end-date",
            "20241231",
            "--valid-end",
            "20240801",
            "--model-cache",
            str(model_path),
            "--signals",
            str(signals_path),
            "--report",
            str(predict_report_path),
        ]
    )
    assert rc == 0
    assert signals_path.is_file()
    report = json.loads(predict_report_path.read_text(encoding="utf-8"))
    assert report["model"]["training_fingerprint"] == train_report["model_cache"]["fingerprint"]
    assert report["threshold"] == train_report["metrics"]["threshold"]
    assert report["metrics"]["scoring_window"]["auc"] is not None
    assert report["backtest"]["metrics"]["closed_trade_count"] is not None
    assert set(report["latest"]) == {"688256", "600183"}
    signals = pd.read_csv(signals_path)
    assert {"timestamp", "symbol", "prob", "signal"}.issubset(signals.columns)
    assert len(signals) > 0


def test_predict_cli_fails_on_missing_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_source(monkeypatch)
    rc = predict_mod.main(
        [
            "--symbols",
            "688256",
            "--start-date",
            "20240101",
            "--end-date",
            "20241231",
            "--valid-end",
            "20240801",
            "--model-cache",
            str(tmp_path / "absent.ubj"),
            "--signals",
            str(tmp_path / "signals.csv"),
            "--report",
            str(tmp_path / "predict.json"),
        ]
    )
    assert rc == 1
