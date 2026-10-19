"""Exception hierarchy for the line-fetcher cache layer."""


class LineFetcherError(RuntimeError):
    """Base class for all line-fetcher failures."""


class DataSourceError(LineFetcherError):
    """A data source could not fulfill a request (unreachable, bad payload)."""


class SourceNotFoundError(LineFetcherError):
    """The requested data-source name is not registered."""
