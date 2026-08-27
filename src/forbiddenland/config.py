"""Configuration for provider and local data backends."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Literal

BackendMode = Literal["local", "remote", "hybrid"]


class ConfigurationError(ValueError):
    """Raised when backend configuration is invalid."""


def _parse_bool(value: str | bool | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigurationError(
        f"Invalid boolean value {value!r}; use one of true/false, 1/0, yes/no, or on/off."
    )


def _parse_int(value: str | int | None, *, default: int, name: str, minimum: int) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid integer value for {name}: {value!r}") from exc
    if parsed < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}; got {parsed}")
    return parsed


def _parse_float(
    value: str | float | None,
    *,
    default: float | None,
    name: str,
    minimum: float,
) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid numeric value for {name}: {value!r}") from exc
    if not isfinite(parsed) or parsed < minimum:
        raise ConfigurationError(f"{name} must be finite and at least {minimum}; got {parsed}")
    return parsed


def _path_from_env(value: str | None) -> Path | None:
    if value is None or not value.strip():
        return None
    return Path(value).expanduser()


@dataclass(frozen=True, slots=True)
class CompatibilityConfig:
    """Runtime settings for the AkShare-compatible data facade.

    Relative paths are resolved against the process working directory. Keeping paths relative by
    default makes the same configuration usable from both macOS and Windows checkouts.
    """

    # Remote data remains the safe default until local market snapshots are reviewed and approved.
    backend: BackendMode = "remote"
    data_root: Path = Path("data")
    allow_remote_fallback: bool = False
    daily_file: Path | None = None
    basic_file: Path | None = None
    ths_concept_catalog_file: Path | None = None
    ths_concept_members_file: Path | None = None
    ths_sector_quotes_file: Path | None = None
    remote_retry_attempts: int = 3
    remote_retry_backoff_seconds: float = 0.5
    remote_request_timeout_seconds: float | None = 15.0
    remote_alternate_source: bool = True
    # Remote historical responses are cached locally by default to avoid repeating identical
    # AkShare requests. The cache is rebuildable and never becomes a local market-data backend.
    remote_cache_enabled: bool = True
    remote_cache_ttl_seconds: float = 86_400.0
    remote_cache_dir: Path | None = None

    def __post_init__(self) -> None:
        backend = str(self.backend).strip().lower()
        if backend not in {"local", "remote", "hybrid"}:
            raise ConfigurationError(
                f"Unsupported backend {self.backend!r}; expected local, remote, or hybrid."
            )
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "data_root", Path(self.data_root).expanduser())
        retry_attempts = _parse_int(
            self.remote_retry_attempts,
            default=3,
            name="remote_retry_attempts",
            minimum=1,
        )
        retry_backoff = _parse_float(
            self.remote_retry_backoff_seconds,
            default=0.5,
            name="remote_retry_backoff_seconds",
            minimum=0.0,
        )
        request_timeout = (
            None
            if self.remote_request_timeout_seconds is None
            else _parse_float(
                self.remote_request_timeout_seconds,
                default=15.0,
                name="remote_request_timeout_seconds",
                minimum=0.001,
            )
        )
        if not isinstance(self.remote_alternate_source, bool):
            raise ConfigurationError("remote_alternate_source must be a boolean")
        if not isinstance(self.remote_cache_enabled, bool):
            raise ConfigurationError("remote_cache_enabled must be a boolean")
        cache_ttl = _parse_float(
            self.remote_cache_ttl_seconds,
            default=86_400.0,
            name="remote_cache_ttl_seconds",
            minimum=0.0,
        )
        object.__setattr__(self, "remote_retry_attempts", retry_attempts)
        object.__setattr__(self, "remote_retry_backoff_seconds", retry_backoff)
        object.__setattr__(self, "remote_request_timeout_seconds", request_timeout)
        object.__setattr__(self, "remote_cache_ttl_seconds", cache_ttl)
        if self.daily_file is not None:
            object.__setattr__(self, "daily_file", Path(self.daily_file).expanduser())
        if self.basic_file is not None:
            object.__setattr__(self, "basic_file", Path(self.basic_file).expanduser())
        if self.ths_concept_catalog_file is not None:
            object.__setattr__(
                self,
                "ths_concept_catalog_file",
                Path(self.ths_concept_catalog_file).expanduser(),
            )
        if self.ths_concept_members_file is not None:
            object.__setattr__(
                self,
                "ths_concept_members_file",
                Path(self.ths_concept_members_file).expanduser(),
            )
        if self.ths_sector_quotes_file is not None:
            object.__setattr__(
                self,
                "ths_sector_quotes_file",
                Path(self.ths_sector_quotes_file).expanduser(),
            )
        if self.remote_cache_dir is not None:
            object.__setattr__(self, "remote_cache_dir", Path(self.remote_cache_dir).expanduser())

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        default_data_root: Path | str = Path("data"),
    ) -> CompatibilityConfig:
        """Build configuration from environment variables.

        ``FORBIDDENLAND_MARKET_BACKEND`` is the canonical backend variable. The shorter
        ``FORBIDDENLAND_BACKEND`` and ``FORBIDDENLAND_DATA_BACKEND`` names are accepted as
        compatibility aliases so existing local scripts do not need to be rewritten.
        """

        values = os.environ if environ is None else environ
        backend_value = (
            values.get("FORBIDDENLAND_MARKET_BACKEND")
            or values.get("FORBIDDENLAND_BACKEND")
            or values.get("FORBIDDENLAND_DATA_BACKEND")
            or "remote"
        )
        data_root = Path(values.get("FORBIDDENLAND_DATA_ROOT", str(default_data_root))).expanduser()
        daily_file = _path_from_env(
            values.get("FORBIDDENLAND_DAILY_FILE") or values.get("FORBIDDENLAND_LOCAL_DAILY_FILE")
        )
        basic_file = _path_from_env(
            values.get("FORBIDDENLAND_BASIC_FILE") or values.get("FORBIDDENLAND_LOCAL_BASIC_FILE")
        )
        ths_concept_catalog_file = _path_from_env(
            values.get("FORBIDDENLAND_THS_CONCEPT_CATALOG_FILE")
        )
        ths_concept_members_file = _path_from_env(
            values.get("FORBIDDENLAND_THS_CONCEPT_MEMBERS_FILE")
        )
        ths_sector_quotes_file = _path_from_env(values.get("FORBIDDENLAND_THS_SECTOR_QUOTES_FILE"))
        return cls(
            backend=backend_value,
            data_root=data_root,
            allow_remote_fallback=_parse_bool(
                values.get("FORBIDDENLAND_ALLOW_REMOTE_FALLBACK"), default=False
            ),
            remote_retry_attempts=_parse_int(
                values.get("FORBIDDENLAND_REMOTE_RETRY_ATTEMPTS"),
                default=3,
                name="FORBIDDENLAND_REMOTE_RETRY_ATTEMPTS",
                minimum=1,
            ),
            remote_retry_backoff_seconds=_parse_float(
                values.get("FORBIDDENLAND_REMOTE_RETRY_BACKOFF_SECONDS"),
                default=0.5,
                name="FORBIDDENLAND_REMOTE_RETRY_BACKOFF_SECONDS",
                minimum=0.0,
            ),
            remote_request_timeout_seconds=_parse_float(
                values.get("FORBIDDENLAND_REMOTE_REQUEST_TIMEOUT_SECONDS"),
                default=15.0,
                name="FORBIDDENLAND_REMOTE_REQUEST_TIMEOUT_SECONDS",
                minimum=0.001,
            ),
            remote_alternate_source=_parse_bool(
                values.get("FORBIDDENLAND_REMOTE_ALTERNATE_SOURCE"), default=True
            ),
            remote_cache_enabled=_parse_bool(
                values.get("FORBIDDENLAND_REMOTE_CACHE_ENABLED")
                or values.get("FORBIDDENLAND_AKSHARE_CACHE_ENABLED"),
                default=True,
            ),
            remote_cache_ttl_seconds=_parse_float(
                values.get("FORBIDDENLAND_REMOTE_CACHE_TTL_SECONDS")
                or values.get("FORBIDDENLAND_AKSHARE_CACHE_TTL_SECONDS"),
                default=86_400.0,
                name="FORBIDDENLAND_REMOTE_CACHE_TTL_SECONDS",
                minimum=0.0,
            ),
            remote_cache_dir=_path_from_env(
                values.get("FORBIDDENLAND_REMOTE_CACHE_DIR")
                or values.get("FORBIDDENLAND_AKSHARE_CACHE_DIR")
            ),
            daily_file=daily_file,
            basic_file=basic_file,
            ths_concept_catalog_file=ths_concept_catalog_file,
            ths_concept_members_file=ths_concept_members_file,
            ths_sector_quotes_file=ths_sector_quotes_file,
        )

    def resolved_daily_file(self) -> Path:
        """Return the configured daily snapshot path."""

        return self.daily_file or self.data_root / "raw" / "stock_daily.parquet"

    def resolved_basic_file(self) -> Path:
        """Return the configured security-master snapshot path."""

        return self.basic_file or self.data_root / "raw" / "stock_basic_data.parquet"

    def resolved_ths_concept_catalog_file(self) -> Path:
        """Return the configured Tonghuashun sector catalog path."""

        return (
            self.ths_concept_catalog_file
            or self.data_root / "raw" / "行业概念板块" / "行业概念板块_同花顺.parquet"
        )

    def resolved_ths_concept_members_file(self) -> Path:
        """Return the configured Tonghuashun concept-member snapshot path."""

        return (
            self.ths_concept_members_file
            or self.data_root / "raw" / "行业概念板块" / "概念板块成分汇总_同花顺.parquet"
        )

    def resolved_ths_sector_quotes_file(self) -> Path:
        """Return the configured Tonghuashun per-sector quote archive path."""

        return (
            self.ths_sector_quotes_file
            or self.data_root / "raw" / "行业概念板块" / "板块指数行情_同花顺_parquet.zip"
        )

    def resolved_remote_cache_dir(self) -> Path:
        """Return the rebuildable directory for remote AkShare response caches."""

        return self.remote_cache_dir or self.data_root / "cache" / "akshare"


# A descriptive alias is useful to callers that think in terms of a backend rather than an
# AkShare compatibility layer.
BackendConfig = CompatibilityConfig
