"""Provider integrations and compatibility adapters."""

from .akshare_compat import (
    CONCEPT_INDEX_COLUMNS,
    CONCEPT_INFO_COLUMNS,
    CONCEPT_NAME_COLUMNS,
    CONCEPT_SUMMARY_COLUMNS,
    AkShareCompat,
    CompatibilityConfig,
    CompatibilityError,
    ConfigurationError,
    InvalidRequestError,
    LocalDataError,
    LocalDataUnavailableError,
    UnsupportedEndpointError,
    ak,
    get_akshare,
    install_local_backend,
    uninstall_backend,
)

__all__ = [
    "CONCEPT_INDEX_COLUMNS",
    "CONCEPT_INFO_COLUMNS",
    "CONCEPT_NAME_COLUMNS",
    "CONCEPT_SUMMARY_COLUMNS",
    "AkShareCompat",
    "CompatibilityConfig",
    "CompatibilityError",
    "ConfigurationError",
    "InvalidRequestError",
    "LocalDataError",
    "LocalDataUnavailableError",
    "UnsupportedEndpointError",
    "ak",
    "get_akshare",
    "install_local_backend",
    "uninstall_backend",
]
