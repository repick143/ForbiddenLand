# ecom-data

Cache-through securities data layer for ForbiddenLand. Reads local SQLite first,
fetches from a registered data source on a miss, and writes back. The script
interface is source-agnostic; easy-tdx is the bundled backend today.

The library lives at `src/ecom_data` (import `ecom_data`, or use the
`ecom-data` console command from the project `.venv`). This skill only
references it; see `SKILL.md` for the contract.

The bundled `easy-tdx` source uses the easy-tdx Python library (`MacClient`,
one persistent connection) and writes every response back to the SQLite cache
(`data/cache/ecom_data.db`), so repeated reads are served locally.

## Quick Start

```bash
python scripts/install_local_skills.py --skill ecom-data   # link into ~/.codex/skills

skills/ecom-data/scripts/ecom-data sources
skills/ecom-data/scripts/ecom-data bars SH 688183 --adjust QFQ --count 30 --table
skills/ecom-data/scripts/ecom-data bars SH 688183 --adjust QFQ --count 30 --refresh
skills/ecom-data/scripts/ecom-data quote SH 600183
skills/ecom-data/scripts/ecom-data stats
skills/ecom-data/scripts/ecom-data clear --source easy-tdx --yes
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
skills/ecom-data/scripts/ecom-data bars SH 600183 --adjust QFQ --count 3 --table
```

The installed console command works the same way from any repo code:

```bash
ecom-data bars SH 600183 --adjust QFQ --count 3 --table   # from .venv
```

Inside your own Python code, import the installed package directly:

```python
from ecom_data import BarRequest, EcomData, QuoteRequest

with EcomData() as svc:  # default db: data/cache/ecom_data.db, source: easy-tdx
    result = svc.get_bars(BarRequest("SH", "600183", adjust="QFQ", count=30))
```

Requires the library installed: `python -m pip install -e ".[dev,data]"`
(registers `ecom_data` and the `ecom-data` console script). See `SKILL.md`
for the full contract and `examples/usage.py` for a runnable demo.
