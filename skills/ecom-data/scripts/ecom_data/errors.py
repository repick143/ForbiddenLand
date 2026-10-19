"""Exception hierarchy for the ecom-data cache layer."""


class EcomDataError(RuntimeError):
    """Base class for all ecom-data failures."""


class DataSourceError(EcomDataError):
    """A data source could not fulfill a request (unreachable, bad payload)."""


class SourceNotFoundError(EcomDataError):
    """The requested data-source name is not registered."""
