---
name: ecom-data
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

# ecom-data

Cache-through securities data layer: reads local SQLite first, fetches from a
registered data source on a miss, and writes the result back. The script
interface is source-agnostic; only `--source` selects the backend. easy-tdx is
the only bundled source today.

## Step 1: Detect the Runtime

Run this probe; every branch has a sentinel so a missing tool is explicit.

!`root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"; if [ -x "$root/skills/ecom-data/scripts/ecom-data" ]; then "$root/skills/ecom-data/scripts/ecom-data" sources 2>/dev/null || echo "ECOM_DATA_FAILED"; else echo "ECOM_DATA_SKILL_MISSING"; fi`

**Decision tree:**

1. `sources` lists `easy-tdx` -> use the CLI below. Data fetches still require
   the easy-tdx server to be reachable (see the easy-tdx-data skill for `ping`).
2. Command missing -> the skill is not installed; run
   `python scripts/install_local_skills.py --skill ecom-data` and retry.
3. `sources` fails -> check `.venv` exists (`python scripts/bootstrap.py`), then
   inspect the error; never fall back to a global interpreter.

## Step 2: Stable Script Interface

All subcommands accept `--source` (default `easy-tdx`, env `ECOM_DATA_SOURCE`).
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
skills/ecom-data/scripts/ecom-data bars SH 688183 --adjust QFQ --count 30 --table
skills/ecom-data/scripts/ecom-data bars SH 600183 --period 5MIN --bar-time end --count 12
skills/ecom-data/scripts/ecom-data quote SH 600183 --refresh
skills/ecom-data/scripts/ecom-data stats
```

Every result carries `provenance`: `cached`, `fetched_at` (UTC), `ttl_seconds`,
plus `source` and `source_version`, so a cache hit is never confused with a live
fetch. Keep this provenance visible in answers.

## Step 3: Call It Directly (CLI) and From Code (Python API)

**Directly executable:** `skills/ecom-data/scripts/ecom-data` is a real
executable script (`#!/usr/bin/env bash` + `chmod +x`). Run it with a plain
path, no `python -m` prefix, from any working directory. It resolves the
repository root itself (including when reached through a `$CODEX_HOME`
symlink) and uses the project `.venv`:

```bash
skills/ecom-data/scripts/ecom-data bars SH 600183 --adjust QFQ --count 3
```

If `.venv` is missing it prints the bootstrap hint instead of falling back to
a global interpreter.

**Python API in your own code:** the package lives under
`skills/ecom-data/scripts`, so add that directory to `sys.path` (or set
`PYTHONPATH`) once, then import the facade:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path("<repo>") / "skills" / "ecom-data" / "scripts"))

from ecom_data import BarRequest, EcomData, QuoteRequest

with EcomData(db_path=Path("data/cache/ecom_data.db")) as svc:
    bars = svc.get_bars(BarRequest("SH", "600183", adjust="QFQ", count=30))
    quote = svc.get_quote(QuoteRequest("SH", "600183"))
    stats = svc.stats()
```

- `EcomData(source_name=..., db_path=...)` — omit both for defaults
  (`easy-tdx`, `data/cache/ecom_data.db`); `ECOM_DATA_DB_PATH`/`ECOM_DATA_SOURCE`
  are honored.
- `get_bars(request, ttl_seconds=None, refresh=False)` returns a dict with
  `bars`, `request`, and `provenance` (`cached`, `fetched_at`, `source_version`).
- `get_quote(request, ...)` returns `quote` plus the same provenance.
- `svc.stats()` / `svc.clear(source=None, older_than_seconds=None)` mirror the
  CLI subcommands; close the facade with `svc.close()` or the `with` block.
- A runnable example is at `skills/ecom-data/examples/usage.py` (call
  `python skills/ecom-data/examples/usage.py`).

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

- SQLite (stdlib, zero new dependencies) at `data/cache/ecom_data.db`
  (gitignored); override with `ECOM_DATA_DB_PATH`. SQLite is a cache, not the
  project's analytical store; DuckDB remains the analysis layer.
- Tables: `bar_series` (series JSON + row count + date range + `fetched_at`),
  `quotes` (snapshot JSON + `fetched_at`), `meta`. WAL mode is enabled.

## Adding a New Data Source

The interface and storage are fixed; only the backend changes.

1. Create `skills/ecom-data/scripts/ecom_data/sources/<name>.py`.
2. Implement `DataSource` (`fetch_bars`, `fetch_quote`; optional `ping` and
   `source_version`), then `register_source(MySource())`. See
   `references/design.md` for the template.
3. Optional out-of-tree backend: a module exposing `register()` can be loaded
   via `ECOM_DATA_PLUGIN_FILE` without touching the skill package.
4. No CLI, request-model, or storage change is required; `--source <name>`
   selects the new backend.

## Environment Variables

- `ECOM_DATA_DB_PATH` — SQLite cache path (default `data/cache/ecom_data.db`)
- `ECOM_DATA_SOURCE` — default backend (default `easy-tdx`)
- `ECOM_DATA_SOURCE_TIMEOUT_SECONDS` — per-source fetch timeout (default 30)
- `ECOM_DATA_PLUGIN_FILE` — optional plugin module path exposing `register()`

## Verification

- Offline unit tests: `tests/test_ecom_data.py` (fake source, temp SQLite) cover
  cache-through, coverage growth, TTL expiry, quote caching, source isolation,
  stats/clear, request validation, and CLI source selection.
- Run the full checks before release: `python -m pytest`, `ruff check .`,
  `ruff format --check .`, `python -m compileall -q src tests scripts research`,
  `git diff --check`.
- Live easy-tdx fetch is a smoke test only; tests must not depend on network.
- Output is research data, not investment advice; keep provenance visible.
