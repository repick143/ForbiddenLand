"""easy-tdx data-source adapter.

Talks to the wrapped ``easy-tdx`` CLI binary and parses its JSON output. The
adapter never depends on easy-tdx internals, so upgrading the binary keeps the
adapter stable.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

from ecom_data.config import PROJECT_ROOT, default_source_timeout
from ecom_data.errors import DataSourceError
from ecom_data.model import BarRequest, QuoteRequest
from ecom_data.sources import DataSource, register_source

_VERSION_RE = re.compile(r"version\s+([0-9][^\s,]*)")
_JSON_OPTION = ("--output", "json")


def _resolve_binary() -> str:
    local = PROJECT_ROOT / ".venv" / "bin" / "easy-tdx"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    found = shutil.which("easy-tdx")
    if found:
        return found
    raise DataSourceError(
        "easy-tdx binary not found; install the project environment first "
        "(python scripts/bootstrap.py)"
    )


class EasyTdxSource(DataSource):
    name = "easy-tdx"

    def __init__(self, binary: str | None = None, timeout: float | None = None) -> None:
        self.binary = binary or _resolve_binary()
        self.timeout = timeout if timeout is not None else default_source_timeout()
        self._version: str | None = None

    @property
    def source_version(self) -> str | None:
        if self._version is None:
            match = _VERSION_RE.search(self._run("--version"))
            self._version = match.group(1) if match else None
        return self._version

    def _run(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DataSourceError(
                f"easy-tdx timed out after {self.timeout}s: {' '.join(args)}"
            ) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            raise DataSourceError(
                f"easy-tdx failed ({proc.returncode}) for {' '.join(args)}: {detail}"
            )
        return proc.stdout

    def fetch_bars(self, request: BarRequest) -> list[dict[str, Any]]:
        args = [
            "kline",
            request.market,
            request.code,
            "--period",
            request.period,
            "--adjust",
            request.adjust,
            "--count",
            str(request.count),
            "--start",
            str(request.start),
            *_JSON_OPTION,
        ]
        if request.bar_time == "end":
            args += ["--bar-time", "end"]
        try:
            payload = json.loads(self._run(*args))
        except json.JSONDecodeError as exc:
            raise DataSourceError(f"easy-tdx returned malformed JSON for {request}: {exc}") from exc
        if not isinstance(payload, list):
            raise DataSourceError(
                f"easy-tdx kline expected a JSON list, got {type(payload).__name__}"
            )
        return [dict(item) for item in payload]

    def fetch_quote(self, request: QuoteRequest) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._run("quote", f"{request.market} {request.code}"))
        except json.JSONDecodeError as exc:
            raise DataSourceError(f"easy-tdx returned malformed JSON for {request}: {exc}") from exc
        if not isinstance(payload, list) or not payload:
            return None
        return dict(payload[0])

    def ping(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._run("ping"))
        except json.JSONDecodeError:
            return {"reachable": False, "detail": "ping output was not JSON"}
        return {"reachable": True, "servers": payload}


register_source(EasyTdxSource())
