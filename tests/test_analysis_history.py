from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from forbiddenland.api.app import create_app
from forbiddenland.application.analysis_history_service import AnalysisHistoryService
from forbiddenland.infrastructure.analysis_history import AnalysisHistoryRepository
from research.technical_analysis.run import STOCK_CATALOG, generate_record


def _write_record(root: Path, symbol: str, analysis_date: date) -> None:
    generate_record(
        symbol,
        analysis_date=analysis_date,
        start_date=date(analysis_date.year - 1, 1, 1),
        end_date=analysis_date,
        history_root=root,
        source="fixture",
    )


def test_history_is_partitioned_by_stock_and_analysis_date(tmp_path: Path) -> None:
    _write_record(tmp_path, "688183", date(2026, 8, 31))

    expected = tmp_path / "688183" / "2026-08-31.json"
    assert expected.is_file()
    result = AnalysisHistoryRepository(tmp_path).list()
    assert len(result.records) == 1
    assert result.records[0].asset.code == "688183"
    assert result.records[0].analysis_date == date(2026, 8, 31)
    assert result.warnings == ()


@pytest.mark.parametrize(
    ("symbol", "name"),
    (
        ("688362", "甬矽电子"),
        ("002428", "云南锗业"),
        ("300139", "晓程科技"),
        ("300209", "行云科技"),
        ("603228", "景旺电子"),
        ("301717", "超纯应材"),
    ),
)
def test_extended_analysis_catalog_resolves_requested_stocks(
    tmp_path: Path, symbol: str, name: str
) -> None:
    assert STOCK_CATALOG[symbol] == name

    _, record = generate_record(
        symbol,
        analysis_date=date(2026, 8, 31),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 8, 31),
        history_root=tmp_path,
        source="fixture",
    )

    assert record.asset.code == symbol
    assert record.asset.name == name


def test_new_record_contains_review_of_the_previous_same_stock_record(tmp_path: Path) -> None:
    _write_record(tmp_path, "600183", date(2026, 7, 31))
    _write_record(tmp_path, "600183", date(2026, 8, 31))

    current = AnalysisHistoryRepository(tmp_path).get("600183", date(2026, 8, 31))
    assert current.review.status == "reviewed"
    assert current.review.previous_analysis_date == date(2026, 7, 31)
    assert current.review.checks
    assert current.review.outcome


def test_list_filtering_and_malformed_file_warning_are_explicit(tmp_path: Path) -> None:
    _write_record(tmp_path, "688183", date(2026, 8, 31))
    _write_record(tmp_path, "600183", date(2026, 8, 30))
    malformed = tmp_path / "600183" / "2026-08-29.json"
    malformed.write_text("{not-json", encoding="utf-8")

    service = AnalysisHistoryService(AnalysisHistoryRepository(tmp_path))
    result = service.list_history(symbol="600183", query="生益", limit=10)
    assert result.total == 1
    assert result.items[0].asset.code == "600183"
    assert len(result.warnings) == 1
    assert "2026-08-29.json" in result.warnings[0]

    review_search = service.list_history(query="首份分析")
    assert review_search.total == 2
    assert {item.asset.code for item in review_search.items} == {"600183", "688183"}


def test_history_api_exposes_list_detail_and_validation_errors(tmp_path: Path) -> None:
    _write_record(tmp_path, "688183", date(2026, 8, 31))
    app = create_app(
        analysis_history_service=AnalysisHistoryService(AnalysisHistoryRepository(tmp_path))
    )

    with TestClient(app) as client:
        listed = client.get("/api/v1/analysis/history", params={"symbol": "688183"})
        detail = client.get("/api/v1/analysis/history/688183/2026-08-31")
        missing = client.get("/api/v1/analysis/history/688183/2026-08-30")
        reversed_dates = client.get(
            "/api/v1/analysis/history",
            params={"start_date": "2026-08-31", "end_date": "2026-08-01"},
        )

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["asset"]["code"] == "688183"
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["review"]["status"] == "no_prior_analysis"
    assert payload["setup"]["stop_loss"] is not None
    assert payload["setup"]["target_price"] is not None
    assert missing.status_code == 404
    assert reversed_dates.status_code == 422


def test_repository_rejects_invalid_stock_codes(tmp_path: Path) -> None:
    repository = AnalysisHistoryRepository(tmp_path)
    with pytest.raises(ValueError, match="stock symbol"):
        repository.get("../../etc/passwd", date(2026, 8, 31))


def test_repository_rejects_ambiguous_boolean_values(tmp_path: Path) -> None:
    _write_record(tmp_path, "688183", date(2026, 8, 31))
    path = tmp_path / "688183" / "2026-08-31.json"
    payload = path.read_text(encoding="utf-8").replace('"cache_hit": false', '"cache_hit": "maybe"')
    path.write_text(payload, encoding="utf-8")

    result = AnalysisHistoryRepository(tmp_path).list()
    assert result.records == ()
    assert len(result.warnings) == 1
    assert "cache_hit" in result.warnings[0]
