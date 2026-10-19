"""Path and environment configuration for the ecom-data skill."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = PACKAGE_DIR.parent
SKILL_DIR = SCRIPTS_DIR.parent
PROJECT_ROOT = PACKAGE_DIR.parents[3]


def default_db_path() -> Path:
    """Return the SQLite cache path (env override, then repo data/cache)."""

    override = os.environ.get("ECOM_DATA_DB_PATH")
    if override:
        return Path(override).expanduser()
    return PROJECT_ROOT / "data" / "cache" / "ecom_data.db"


def default_source_name() -> str:
    return os.environ.get("ECOM_DATA_SOURCE", "easy-tdx")


def default_source_timeout() -> float:
    raw = os.environ.get("ECOM_DATA_SOURCE_TIMEOUT_SECONDS", "30")
    try:
        return float(raw)
    except ValueError:
        return 30.0
