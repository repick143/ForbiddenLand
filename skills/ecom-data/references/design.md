# ecom-data Design Notes

## Goals

1. A cache layer for securities data with a stable script interface.
2. Multiple backends, one interface; switching backends must not change the CLI.
3. Local-first reads; fetch-and-write-back on a miss.

## Layout

The library is a normal repository package at `src/ecom_data` and is installed
with the project (`python -m pip install -e ".[dev,data]"`), which registers
the `ecom-data` console script (`pyproject.toml` `[project.scripts]`). The
`skills/ecom-data` directory is documentation plus a thin launcher
(`scripts/ecom-data`) that delegates to the installed library; it contains no
implementation code.

## Decisions

- **SQLite over DuckDB**: the user asked for the lightest store. SQLite is
  stdlib (`sqlite3`), zero new dependencies, and single-file. DuckDB remains
  the project's analytical layer (`data/processed/`, warehouse queries); the
  cache is deliberately a key-value store, not an analytical store. Swapping
  the storage engine only requires reimplementing `Cache`.
- **easy-tdx via its CLI binary**: the adapter shells out to the wrapped
  `easy-tdx` binary (project `.venv/bin/easy-tdx`) and parses JSON. This keeps
  the adapter stable across easy-tdx upgrades and mirrors the easy-tdx-data
  skill convention. Direct imports of easy-tdx internals are avoided.
- **Library in `src/`, skill as a reference**: keeping implementation code in
  `src/ecom_data` makes it importable from any repo code and console-ready via
  `[project.scripts]`; the skill stays a thin, installable instruction set.
- **Series-level bar keys**: a bar series is keyed by
  `(source, market, code, period, adjust, bar_time)` and stores the full
  fetched window. Requests with `count <= stored rows` are served from cache,
  so repeated queries with different counts share one fetch. `start` is an
  offset from the latest bar and is applied when slicing.
- **TTL semantics**: default no expiry; `--ttl-seconds` enables freshness;
  `0` means no expiry. Expiry is checked against `fetched_at` (UTC ISO).
  `--refresh` forces a fetch regardless.
- **Plugins**: `ECOM_DATA_PLUGIN_FILE` loads an external module exposing
  `register()`, letting custom backends live outside the skill package.

## Source Adapter Contract

```python
from ecom_data.model import BarRequest, QuoteRequest
from ecom_data.sources import DataSource, register_source


class MySource(DataSource):
    name = "my-source"

    @property
    def source_version(self) -> str | None:
        return "1.0"

    def fetch_bars(self, request: BarRequest) -> list[dict]:
        # normalized bars: datetime, open, high, low, close, vol, amount, ...
        return [...]

    def fetch_quote(self, request: QuoteRequest) -> dict | None:
        return {"market": request.market, "code": request.code, ...}

    def ping(self) -> dict | None:
        return {"reachable": True}


register_source(MySource())
```

## Storage Schema

- `bar_series`: `source, market, code, period, adjust, bar_time` (PK) +
  `payload` (JSON array), `row_count`, `first_dt`, `last_dt`, `fetched_at`,
  `source_version`.
- `quotes`: `source, market, code` (PK) + `payload` (JSON), `fetched_at`,
  `source_version`.
- `meta`: `key` (PK), `value`.

`fetched_at` is stored as UTC ISO (`+00:00`, seconds precision); comparisons
are lexicographic on the ISO string.

## Extending

- New backend: add `sources/<name>.py` implementing `DataSource` and register
  it; no CLI/storage change. Out-of-tree: plugin file + `ECOM_DATA_PLUGIN_FILE`.
- New data kind (e.g. transactions): add a method to `DataSource`, a cache
  table, and a CLI subcommand; keep the source-agnostic request shape.
- Always run `tests/test_ecom_data.py` and the full project checks before
  release; live fetches are smoke tests only.
