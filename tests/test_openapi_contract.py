from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

app = importlib.import_module("forbiddenland.api.app").app


def test_checked_in_openapi_contract_matches_fastapi_source() -> None:
    contract_path = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"
    checked_in = json.loads(contract_path.read_text(encoding="utf-8"))

    assert checked_in == app.openapi()
