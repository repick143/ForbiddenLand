"""Run this example from anywhere; it resolves the repo and .venv itself.

Usage:
    python skills/ecom-data/examples/usage.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PYTHON = REPO / ".venv" / "bin" / "python"
CLI = REPO / "skills" / "ecom-data" / "scripts" / "ecom-data"
SCRIPTS = REPO / "skills" / "ecom-data" / "scripts"
DB = Path("/tmp/ecom-data-example.db")

sys.path.insert(0, str(SCRIPTS))

from ecom_data import BarRequest, EcomData, QuoteRequest


def cli_style() -> None:
    """Directly executable CLI, no python -m prefix needed."""

    subprocess.run(
        [str(CLI), "bars", "SH", "600183", "--adjust", "QFQ", "--count", "3", "--table"],
        env={"ECOM_DATA_DB_PATH": str(DB)},
        check=False,
    )


def python_api() -> None:
    """Python API inside your own code (same cache database)."""

    with EcomData(db_path=DB) as svc:
        bars = svc.get_bars(BarRequest("SH", "600183", adjust="QFQ", count=3))
        print("cached:", bars["provenance"]["cached"], "source:", bars["source"])
        print("last bar:", bars["bars"][-1]["datetime"][:10], bars["bars"][-1]["close"])
        quote = svc.get_quote(QuoteRequest("SH", "600183"))
        print("quote name:", quote["quote"]["name"])


if __name__ == "__main__":
    if not PYTHON.is_file():
        raise SystemExit(f"missing project venv at {PYTHON}")
    cli_style()
    python_api()
    python_api()  # second call: cache hit
