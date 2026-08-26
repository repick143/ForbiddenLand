"""Provider integrations and compatibility adapters."""

from .akshare_compat import (
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
