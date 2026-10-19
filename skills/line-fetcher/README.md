# line-fetcher

Cache-through securities data layer for ForbiddenLand. Reads local SQLite first,
fetches from a registered data source on a miss, and writes back. The script
interface is source-agnostic; easy-tdx is the bundled backend today.

The library lives at `src/line_fetcher` (import `line_fetcher`, or use the
`line-fetcher` console command from the project `.venv`). This skill only
references it; see `SKILL.md` for the contract.

The bundled `easy-tdx` source uses the easy-tdx Python library (`MacClient`,
one persistent connection) and writes every response back to the SQLite cache
(`data/cache/line_fetcher.db`), so repeated reads are served locally.

## Quick Start

```bash
python scripts/install_local_skills.py --skill line-fetcher   # link into ~/.codex/skills

skills/line-fetcher/scripts/line-fetcher sources
skills/line-fetcher/scripts/line-fetcher bars SH 688183 --adjust QFQ --count 30 --table
skills/line-fetcher/scripts/line-fetcher bars SH 688183 --adjust QFQ --count 30 --refresh
skills/line-fetcher/scripts/line-fetcher quote SH 600183
skills/line-fetcher/scripts/line-fetcher stats
skills/line-fetcher/scripts/line-fetcher clear --source easy-tdx --yes
```

## Interface

- `bars MARKET CODE [--period] [--adjust] [--bar-time] [--count] [--start]`
- `quote MARKET CODE`
- `stats`, `clear`, `sources`, `ping`
- Common: `--source`, `--ttl-seconds`, `--refresh`, `--db`, `--table`,
  `--output json|table|csv`

## Direct Execution and Code Usage

The skill launcher is directly executable (no `python -m` prefix, works from
any working directory); it delegates to the installed library:

```bash
skills/line-fetcher/scripts/line-fetcher bars SH 600183 --adjust QFQ --count 3 --table
```

The installed console command works the same way from any repo code:

```bash
line-fetcher bars SH 600183 --adjust QFQ --count 3 --table   # from .venv
```

Inside your own Python code, import the installed package directly:

```python
from line_fetcher import BarRequest, LineFetcher, QuoteRequest

with LineFetcher() as svc:  # default db: data/cache/line_fetcher.db, source: easy-tdx
    result = svc.get_bars(BarRequest("SH", "600183", adjust="QFQ", count=30))
```

Requires the library installed: `python -m pip install -e ".[dev,data]"`
(registers `line_fetcher` and the `line-fetcher` console script). See `SKILL.md`
for the full contract and `examples/usage.py` for a runnable demo.
