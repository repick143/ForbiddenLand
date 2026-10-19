---
name: line-fetcher
description: >
  Cache-through securities data layer for the ForbiddenLand repo. Use when the
  task needs repeated K-line or quote fetches, offline replay of previously
  fetched bars, or a stable data-source boundary that can switch backends
  (easy-tdx today) without changing the script interface. Commands: bars,
  quote, stats, clear, sources, ping. Also trigger when the user asks to cache
  market data, reuse fetched bars, or add a new market-data backend.
version: 1.0.0
author: chenchen
platforms: [macos, linux]
metadata:
  hermes:
    tags: [A-share, market-data, cache, easy-tdx, data-source]
    related_skills: [easy-tdx-data, tushare, technical-analysis]
    category: market-analysis
---

# line-fetcher

Cache-through securities data layer: reads local SQLite first, fetches from a
registered data source on a miss, and writes the result back. The script
interface is source-agnostic; only `--source` selects the backend. easy-tdx is
the only bundled source today. The library lives in the repository at
`src/line_fetcher`; this skill only documents and references it.

## Step 1: Detect the Runtime

Run this probe; every branch has a sentinel so a missing tool is explicit.

!`root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"; if [ -x "$root/skills/line-fetcher/scripts/line-fetcher" ]; then "$root/skills/line-fetcher/scripts/line-fetcher" sources 2>/dev/null || echo "LINE_FETCHER_FAILED"; else echo "LINE_FETCHER_SKILL_MISSING"; fi`

**Decision tree:**

1. `sources` lists `easy-tdx` -> use the CLI below. Data fetches still require
   the easy-tdx server to be reachable (see the easy-tdx-data skill for `ping`).
2. Command missing -> the skill is not installed; run
   `python scripts/install_local_skills.py --skill line-fetcher` and retry.
3. `sources` fails -> check `.venv` exists (`python scripts/bootstrap.py`), then
   inspect the error; never fall back to a global interpreter.

## Step 2: Stable Script Interface

All subcommands accept `--source` (default `easy-tdx`, env `LINE_FETCHER_SOURCE`).
Switching data sources never changes the request parameters below.

| Command | Purpose | Source-agnostic parameters |
| --- | --- | --- |
| `bars MARKET CODE` | K-line, cache-first | `--period` `--adjust` `--bar-time` `--count` `--start` |
| `quote MARKET CODE` | Quote snapshot, cache-first | (none besides `--source`) |
| `stats` | Cache statistics | `--source` |
| `clear` | Delete cached entries (dry-run without `--yes`) | `--source` `--older-than-seconds` |
| `sources` | List registered backends | — |
| `ping` | Probe a backend | `--source` |

Common options for `bars`/`quote`: `--ttl-seconds N` (0/omitted = no expiry),
`--refresh` (bypass cache), `--db PATH`, `--table`, `--output json|table|csv`.

Examples:

```bash
skills/line-fetcher/scripts/line-fetcher bars SH 688183 --adjust QFQ --count 30 --table
skills/line-fetcher/scripts/line-fetcher bars SH 600183 --period 5MIN --bar-time end --count 12
skills/line-fetcher/scripts/line-fetcher quote SH 600183 --refresh
skills/line-fetcher/scripts/line-fetcher stats
```

Every result carries `provenance`: `cached`, `fetched_at` (UTC), `ttl_seconds`,
plus `source` and `source_version`, so a cache hit is never confused with a live
fetch. Keep this provenance visible in answers.

## Step 3: Call It Directly (CLI) and From Code (Python API)

**Directly executable:** `skills/line-fetcher/scripts/line-fetcher` is a thin
launcher that references the installed library. Run it with a plain path, no
`python -m` prefix, from any working directory; it prefers the `.venv` console
script `line-fetcher` and falls back to `python -m line_fetcher`. The repository
root is resolved even when reached through a `$CODEX_HOME` symlink:

```bash
skills/line-fetcher/scripts/line-fetcher bars SH 600183 --adjust QFQ --count 3
```

If `.venv` is missing it prints the bootstrap hint instead of falling back to
a global interpreter.

**Python API in your own code:** the library is a regular repository package
under `src/line_fetcher` and is installed by `python -m pip install -e ".[dev,data]"`.
Import it directly, no `sys.path` tricks:

```python
from line_fetcher import BarRequest, LineFetcher, QuoteRequest

with LineFetcher(db_path=Path("data/cache/line_fetcher.db")) as svc:
    bars = svc.get_bars(BarRequest("SH", "600183", adjust="QFQ", count=30))
    quote = svc.get_quote(QuoteRequest("SH", "600183"))
    stats = svc.stats()
```

- `LineFetcher(source_name=..., db_path=...)` — omit both for defaults
  (`easy-tdx`, `data/cache/line_fetcher.db`); `LINE_FETCHER_DB_PATH`/`LINE_FETCHER_SOURCE`
  are honored.
- `get_bars(request, ttl_seconds=None, refresh=False)` returns a dict with
  `bars`, `request`, and `provenance` (`cached`, `fetched_at`, `source_version`).
- `get_quote(request, ...)` returns `quote` plus the same provenance.
- `svc.stats()` / `svc.clear(source=None, older_than_seconds=None)` mirror the
  CLI subcommands; close the facade with `svc.close()` or the `with` block.
- A runnable example is at `skills/line-fetcher/examples/usage.py` (call
  `python skills/line-fetcher/examples/usage.py`).

## Cache Semantics

- **Cache-through**: a request reads the local SQLite cache first; on a miss it
  fetches from the source and writes back. The stored bar series is keyed by
  `(source, market, code, period, adjust, bar_time)` and grows to the largest
  requested window, so a smaller later request is served from cache.
- **TTL**: by default entries never expire. For time-sensitive data pass
  `--ttl-seconds` (daily bars: 21600 for a 6-hour window) or `--refresh`.
  `--ttl-seconds 0` means no expiry, same as omitting it.
- **Quotes** are keyed by `(source, market, code)`; refresh them explicitly or
  with a short TTL because they are real-time snapshots.

## Storage

- SQLite (stdlib, zero new dependencies) at `data/cache/line_fetcher.db`
  (gitignored); override with `LINE_FETCHER_DB_PATH`. SQLite is a cache, not the
  project's analytical store; DuckDB remains the analysis layer.
- Tables: `bar_series` (series JSON + row count + date range + `fetched_at`),
  `quotes` (snapshot JSON + `fetched_at`), `meta`. WAL mode is enabled.

## Adding a New Data Source

The interface and storage are fixed; only the backend changes.

1. Create `src/line_fetcher/sources/<name>.py` in the library package.
2. Implement `DataSource` (`fetch_bars`, `fetch_quote`; optional `ping` and
   `source_version`), then `register_source(MySource())`. See
   `references/design.md` for the template.
3. Optional out-of-tree backend: a module exposing `register()` can be loaded
   via `LINE_FETCHER_PLUGIN_FILE` without touching the skill package.
4. No CLI, request-model, or storage change is required; `--source <name>`
   selects the new backend. New console entry points, if ever needed, are
   declared in `pyproject.toml` `[project.scripts]`.

## Environment Variables

- `LINE_FETCHER_DB_PATH` — SQLite cache path (default `data/cache/line_fetcher.db`)
- `LINE_FETCHER_SOURCE` — default backend (default `easy-tdx`)
- `LINE_FETCHER_SOURCE_TIMEOUT_SECONDS` — per-source fetch timeout (default 30)
- `LINE_FETCHER_PLUGIN_FILE` — optional plugin module path exposing `register()`

## Verification

- Offline unit tests: `tests/test_line_fetcher.py` (fake source, temp SQLite) cover
  cache-through, coverage growth, TTL expiry, quote caching, source isolation,
  stats/clear, request validation, and CLI source selection.
- Run the full checks before release: `python -m pytest`, `ruff check .`,
  `ruff format --check .`, `python -m compileall -q src tests scripts research`,
  `git diff --check`.
- Live easy-tdx fetch is a smoke test only; tests must not depend on network.
- Output is research data, not investment advice; keep provenance visible.
