"""Filesystem persistence for date-partitioned stock analysis records."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from ..domain.analysis import AnalysisHistoryRead, AnalysisRecord

_STOCK_CODE = re.compile(r"^\d{6}$")


class AnalysisHistoryError(RuntimeError):
    """Base error for analysis-history persistence failures."""


class AnalysisHistoryNotFound(AnalysisHistoryError):
    """Raised when a requested stock/date record does not exist."""


class AnalysisHistoryDataError(AnalysisHistoryError):
    """Raised when one requested history file is malformed or unsafe."""


def normalize_stock_code(value: str) -> str:
    """Normalize an optional exchange suffix while preserving six-digit codes."""

    code = str(value).strip().upper()
    if "." in code:
        code = code.rsplit(".", 1)[0]
    if not code.isdigit() or len(code) > 6:
        raise ValueError("stock symbol must be a string with at most six digits")
    code = code.zfill(6)
    if not _STOCK_CODE.fullmatch(code):
        raise ValueError("stock symbol must contain six digits")
    return code


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (date,)):
        return value.isoformat()
    return value


class AnalysisHistoryRepository:
    """Read and write ``<root>/<stock-code>/<YYYY-MM-DD>.json`` records."""

    def __init__(self, root: Path | str):
        configured = Path(root).expanduser()
        if not configured.is_absolute():
            configured = Path(__file__).resolve().parents[3] / configured
        self.root = configured.resolve()

    def _record_path(self, code: str, analysis_date: date) -> Path:
        normalized = normalize_stock_code(code)
        return self.root / normalized / f"{analysis_date.isoformat()}.json"

    def _safe_path(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise AnalysisHistoryDataError("analysis history path escapes configured root") from exc
        return resolved

    def _read_path(self, path: Path) -> AnalysisRecord:
        safe_path = self._safe_path(path)
        try:
            raw = json.loads(safe_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AnalysisHistoryNotFound(f"analysis history file not found: {safe_path}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AnalysisHistoryDataError(
                f"cannot read analysis history {safe_path}: {exc}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise AnalysisHistoryDataError(
                f"analysis history {safe_path} must contain a JSON object"
            )
        try:
            record = AnalysisRecord.from_mapping(raw, source_path=safe_path)
        except (TypeError, ValueError) as exc:
            raise AnalysisHistoryDataError(f"invalid analysis history {safe_path}: {exc}") from exc
        if safe_path.stem != record.analysis_date.isoformat():
            raise AnalysisHistoryDataError(
                f"analysis history {safe_path} filename does not match analysis_date "
                f"{record.analysis_date.isoformat()}"
            )
        if safe_path.parent.name != record.asset.code:
            raise AnalysisHistoryDataError(
                f"analysis history {safe_path} directory does not match asset code "
                f"{record.asset.code}"
            )
        return record

    def get(self, code: str, analysis_date: date) -> AnalysisRecord:
        """Return one record or raise an explicit not-found/data error."""

        return self._read_path(self._record_path(code, analysis_date))

    def list(self) -> AnalysisHistoryRead:
        """Read all valid records and retain per-file failures as warnings."""

        if not self.root.exists():
            return AnalysisHistoryRead(records=(), warnings=())
        if not self.root.is_dir():
            raise AnalysisHistoryDataError(f"analysis history root is not a directory: {self.root}")

        records: list[AnalysisRecord] = []
        warnings: list[str] = []
        for code_dir in sorted(self.root.iterdir(), key=lambda item: item.name):
            if not code_dir.is_dir() or not _STOCK_CODE.fullmatch(code_dir.name):
                continue
            for path in sorted(code_dir.glob("*.json"), key=lambda item: item.name):
                try:
                    records.append(self._read_path(path))
                except AnalysisHistoryError as exc:
                    warnings.append(str(exc))
        records.sort(key=lambda item: (item.analysis_date, item.asset.code), reverse=True)
        return AnalysisHistoryRead(records=tuple(records), warnings=tuple(warnings))

    def latest_before(self, code: str, analysis_date: date) -> AnalysisRecord | None:
        """Find the latest valid prior record for review generation."""

        normalized = normalize_stock_code(code)
        result = self.list()
        candidates = [
            record
            for record in result.records
            if record.asset.code == normalized and record.analysis_date < analysis_date
        ]
        return max(candidates, key=lambda item: item.analysis_date, default=None)

    def save(self, record: AnalysisRecord) -> Path:
        """Atomically persist one canonical record and return its final path."""

        path = self._record_path(record.asset.code, record.analysis_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    _json_ready(record.to_mapping()),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise AnalysisHistoryError(f"cannot write analysis history {path}: {exc}") from exc
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
        return path


__all__ = [
    "AnalysisHistoryDataError",
    "AnalysisHistoryError",
    "AnalysisHistoryNotFound",
    "AnalysisHistoryRead",
    "AnalysisHistoryRepository",
    "normalize_stock_code",
]
