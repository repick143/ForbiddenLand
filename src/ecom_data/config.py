"""Path and environment configuration for the ecom-data library."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def _find_repo_root(start: Path) -> Path:
    """Walk upward from the package until the repository root (.git) is found."""

    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start.parents[2]


PROJECT_ROOT = _find_repo_root(PACKAGE_DIR)


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
