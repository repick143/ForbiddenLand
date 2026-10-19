"""Offline tests for the ecom-data cache layer (fake source, temp SQLite)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "skills" / "ecom-data" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from ecom_data import EcomData
from ecom_data.errors import SourceNotFoundError
from ecom_data.model import BarRequest, QuoteRequest
from ecom_data.sources import DataSource, register_source


class FakeSource(DataSource):
    name = "fake"

    def __init__(self, calls: dict[str, int]) -> None:
        self.calls = calls

    @property
    def source_version(self) -> str:
        return "fake-1.0"

    def _bars(self, count: int) -> list[dict]:
        base = datetime(2026, 9, 1, tzinfo=UTC)
        return [
            {
                "datetime": (base + timedelta(days=i)).isoformat(timespec="seconds"),
                "open": 10.0 + i,
                "high": 11.0 + i,
                "low": 9.0 + i,
                "close": 10.5 + i,
                "vol": 1000 + i,
                "amount": 100000.0 + i,
                "float_shares": 5000.0,
            }
            for i in range(count)
        ]

    def fetch_bars(self, request: BarRequest) -> list[dict]:
        self.calls["bars"] = self.calls.get("bars", 0) + 1
        self.calls["last_count"] = request.count
        return self._bars(request.count)

    def fetch_quote(self, request: QuoteRequest) -> dict | None:
        self.calls["quotes"] = self.calls.get("quotes", 0) + 1
        return {"market": request.market, "code": request.code, "close": 12.34}


@pytest.fixture
def fake_source() -> FakeSource:
    source = FakeSource({})
    register_source(source)
    return source


@pytest.fixture
def svc(fake_source: FakeSource, tmp_path: Path) -> EcomData:
    instance = EcomData(source_name="fake", db_path=tmp_path / "cache.db")
    yield instance
    instance.close()


def test_first_get_fetches_then_caches(fake_source: FakeSource, svc: EcomData) -> None:
    request = BarRequest("SH", "688183", count=10)

    first = svc.get_bars(request)
    assert first["provenance"]["cached"] is False
    assert len(first["bars"]) == 10
    assert fake_source.calls["bars"] == 1

    second = svc.get_bars(request)
    assert second["provenance"]["cached"] is True
    assert second["bars"] == first["bars"]
    assert fake_source.calls["bars"] == 1


def test_larger_count_refetches_and_smaller_served_from_cache(
    fake_source: FakeSource, svc: EcomData
) -> None:
    svc.get_bars(BarRequest("SH", "688183", count=5))
    assert fake_source.calls["bars"] == 1

    bigger = svc.get_bars(BarRequest("SH", "688183", count=50))
    assert bigger["provenance"]["cached"] is False
    assert len(bigger["bars"]) == 50
    assert fake_source.calls["bars"] == 2
    assert fake_source.calls["last_count"] == 50

    small = svc.get_bars(BarRequest("SH", "688183", count=5))
    assert small["provenance"]["cached"] is True
    assert fake_source.calls["bars"] == 2
    assert small["bars"][-1]["close"] == bigger["bars"][-1]["close"]


def test_start_offset_slices_from_cache(fake_source: FakeSource, svc: EcomData) -> None:
    svc.get_bars(BarRequest("SH", "688183", count=10))
    request = BarRequest("SH", "688183", count=3, start=2)
    result = svc.get_bars(request)
    assert result["provenance"]["cached"] is True
    assert fake_source.calls["bars"] == 1
    assert len(result["bars"]) == 3
    assert result["bars"][-1]["close"] == 17.5  # window indices 5..7


def test_ttl_expiry_refetches(fake_source: FakeSource, svc: EcomData, tmp_path: Path) -> None:
    request = BarRequest("SH", "688183", count=5)
    svc.get_bars(request)
    assert fake_source.calls["bars"] == 1

    # Backdate the stored entry beyond the requested TTL.
    svc.cache.put_bar_series(
        "fake",
        "SH",
        "688183",
        "DAILY",
        "NONE",
        "start",
        fake_source._bars(5),
        "fake-1.0",
        fetched_at=(datetime.now().astimezone() - timedelta(seconds=100)).isoformat(),
    )

    stale = svc.get_bars(request, ttl_seconds=60)
    assert stale["provenance"]["cached"] is False
    assert fake_source.calls["bars"] == 2

    fresh = svc.get_bars(request, ttl_seconds=3600)
    assert fresh["provenance"]["cached"] is True
    assert fake_source.calls["bars"] == 2


def test_quote_caches_and_refresh(fake_source: FakeSource, svc: EcomData) -> None:
    request = QuoteRequest("SH", "600183")

    first = svc.get_quote(request)
    assert first["provenance"]["cached"] is False
    assert first["quote"]["close"] == 12.34
    assert fake_source.calls["quotes"] == 1

    cached = svc.get_quote(request)
    assert cached["provenance"]["cached"] is True
    assert fake_source.calls["quotes"] == 1

    forced = svc.get_quote(request, refresh=True)
    assert forced["provenance"]["cached"] is False
    assert fake_source.calls["quotes"] == 2


def test_sources_are_isolated(tmp_path: Path) -> None:
    calls_a: dict[str, int] = {}
    calls_b: dict[str, int] = {}
    source_a = FakeSource(calls_a)
    source_a.name = "fake-a"
    source_b = FakeSource(calls_b)
    source_b.name = "fake-b"
    register_source(source_a)
    register_source(source_b)

    with (
        EcomData(source_name="fake-a", db_path=tmp_path / "a.db") as _a,
        EcomData(source_name="fake-b", db_path=tmp_path / "b.db") as b,
    ):
        result_b = b.get_bars(BarRequest("SH", "688183", count=5))
        assert result_b["provenance"]["cached"] is False
        assert calls_b["bars"] == 1
        assert calls_a == {}


def test_stats_and_clear(fake_source: FakeSource, svc: EcomData) -> None:
    svc.get_bars(BarRequest("SH", "688183", count=5))
    svc.get_quote(QuoteRequest("SH", "600183"))

    stats = svc.stats()
    assert stats["bar_series"]["count"] == 1
    assert stats["quotes"]["count"] == 1
    assert stats["by_source"][0]["source"] == "fake"

    deleted = svc.clear(source="fake")
    assert deleted == {"bar_series": 1, "quotes": 1}

    again = svc.get_bars(BarRequest("SH", "688183", count=5))
    assert again["provenance"]["cached"] is False
    assert fake_source.calls["bars"] == 2


def test_unknown_source_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceNotFoundError, match="Unknown data source"):
        EcomData(source_name="does-not-exist", db_path=tmp_path / "x.db")


def test_request_validation() -> None:
    with pytest.raises(ValueError, match="code must not be empty"):
        BarRequest("SH", "")


def test_cli_offline_commands(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPTS_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["ECOM_DATA_DB_PATH"] = str(tmp_path / "cli.db")

    sources = subprocess.run(
        [sys.executable, "-m", "ecom_data", "sources"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert sources.returncode == 0, sources.stderr
    assert '"name": "easy-tdx"' in sources.stdout

    stats = subprocess.run(
        [sys.executable, "-m", "ecom_data", "stats"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert stats.returncode == 0, stats.stderr
    assert "bar_series" in stats.stdout


def test_cli_bars_with_fake_source(tmp_path: Path) -> None:
    """CLI source selection works via a plugin-registered source (interface stable)."""

    plugin = tmp_path / "plugin.py"
    plugin.write_text(
        "from ecom_data.model import BarRequest, QuoteRequest\n"
        "from ecom_data.sources import DataSource, register_source\n"
        "\n"
        "class PluginFake(DataSource):\n"
        '    name = "fake"\n'
        "\n"
        "    @property\n"
        "    def source_version(self):\n"
        '        return "fake-1.0"\n'
        "\n"
        "    def fetch_bars(self, request: BarRequest):\n"
        "        return [\n"
        '            {"datetime": f"2026-09-{i + 1:02d}T00:00:00", '
        '"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, '
        '"vol": 1000, "amount": 100000.0}\n'
        "            for i in range(request.count)\n"
        "        ]\n"
        "\n"
        "    def fetch_quote(self, request: QuoteRequest):\n"
        '        return {"market": request.market, "code": request.code}\n'
        "\n"
        "def register():\n"
        "    register_source(PluginFake())\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPTS_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["ECOM_DATA_DB_PATH"] = str(tmp_path / "cli.db")
    env["ECOM_DATA_SOURCE"] = "fake"
    env["ECOM_DATA_PLUGIN_FILE"] = str(plugin)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ecom_data",
            "bars",
            "SH",
            "688183",
            "--count",
            "3",
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["source"] == "fake"
    assert result["provenance"]["cached"] is False
    assert len(result["bars"]) == 3
