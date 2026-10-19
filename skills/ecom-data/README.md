# ecom-data

Cache-through securities data layer for ForbiddenLand. Reads local SQLite first,
fetches from a registered data source on a miss, and writes back. The script
interface is source-agnostic; easy-tdx is the bundled backend today.

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

The CLI script is directly executable (no `python -m` prefix, works from any
working directory):

```bash
skills/ecom-data/scripts/ecom-data bars SH 600183 --adjust QFQ --count 3 --table
```

To use the cache layer inside your own Python code:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path("skills/ecom-data/scripts").resolve()))

from ecom_data import BarRequest, EcomData, QuoteRequest

with EcomData() as svc:  # default db: data/cache/ecom_data.db, source: easy-tdx
    result = svc.get_bars(BarRequest("SH", "600183", adjust="QFQ", count=30))
```

See `SKILL.md` for the full contract, cache semantics, provenance rules, and
`examples/usage.py` for a runnable demo of both styles.
