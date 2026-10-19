"""Data-source registry.

A data source adapts one backend (easy-tdx today) to the stable
``BarRequest``/``QuoteRequest`` interface. Adding a source means implementing
``DataSource`` and calling ``register_source``; no CLI or storage change is
required.
"""

from __future__ import annotations

import importlib.util
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ecom_data.errors import SourceNotFoundError
from ecom_data.model import BarRequest, QuoteRequest


class DataSource(ABC):
    """Contract every backend adapter must implement."""

    name = ""

    @property
    def source_version(self) -> str | None:
        return None

    @abstractmethod
    def fetch_bars(self, request: BarRequest) -> list[dict[str, Any]]:
        """Return normalized bars; an empty list is a valid result."""

    @abstractmethod
    def fetch_quote(self, request: QuoteRequest) -> dict[str, Any] | None:
        """Return a normalized quote snapshot, or None when unavailable."""

    def ping(self) -> dict[str, Any] | None:
        """Optional reachability probe; None means the source has no probe."""

        return None


_REGISTRY: dict[str, DataSource] = {}


def register_source(source: DataSource) -> DataSource:
    """Register a source instance under ``source.name`` (idempotent replace)."""

    if not source.name:
        raise ValueError("DataSource.name must be set")
    _REGISTRY[source.name] = source
    return source


def get_source(name: str) -> DataSource:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY))
        raise SourceNotFoundError(f"Unknown data source {name!r}; available: {available}") from exc


def list_sources() -> list[str]:
    return sorted(_REGISTRY)


# Importing the adapter registers it in the registry.
from ecom_data.sources import easy_tdx  # noqa: F401


def _load_plugins() -> None:
    """Load user data-source plugins from ``ECOM_DATA_PLUGIN_FILE``.

    The module must expose a ``register()`` callable that calls
    ``register_source`` for each custom source. This lets new backends live
    outside the skill package without changing the CLI interface.
    """

    raw = os.environ.get("ECOM_DATA_PLUGIN_FILE")
    if not raw:
        return
    path = Path(raw).expanduser()
    spec = importlib.util.spec_from_file_location("ecom_data_plugin", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load ecom-data plugin: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    register = getattr(module, "register", None)
    if callable(register):
        register()


_load_plugins()
