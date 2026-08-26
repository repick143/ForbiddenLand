"""Configuration for provider and local data backends."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
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

    def __post_init__(self) -> None:
        backend = str(self.backend).strip().lower()
        if backend not in {"local", "remote", "hybrid"}:
            raise ConfigurationError(
                f"Unsupported backend {self.backend!r}; expected local, remote, or hybrid."
            )
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "data_root", Path(self.data_root).expanduser())
        if self.daily_file is not None:
            object.__setattr__(self, "daily_file", Path(self.daily_file).expanduser())
        if self.basic_file is not None:
            object.__setattr__(self, "basic_file", Path(self.basic_file).expanduser())

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
        return cls(
            backend=backend_value,
            data_root=data_root,
            allow_remote_fallback=_parse_bool(
                values.get("FORBIDDENLAND_ALLOW_REMOTE_FALLBACK"), default=False
            ),
            daily_file=daily_file,
            basic_file=basic_file,
        )

    def resolved_daily_file(self) -> Path:
        """Return the configured daily snapshot path."""

        return self.daily_file or self.data_root / "raw" / "stock_daily.parquet"

    def resolved_basic_file(self) -> Path:
        """Return the configured security-master snapshot path."""

        return self.basic_file or self.data_root / "raw" / "stock_basic_data.parquet"


# A descriptive alias is useful to callers that think in terms of a backend rather than an
# AkShare compatibility layer.
BackendConfig = CompatibilityConfig
