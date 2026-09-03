# Market Data Routes

`easy-tdx` exposes two related protocol clients. The standard `TdxClient` is the lower-level
TDX command set; `MacClient` adds the richer MAC service used by the CLI for adjusted K-lines,
boards, statistics, and several auxiliary datasets. `UnifiedTdxClient` routes A-share and
extended-market calls for Python users.

## Resolve the Executable

Resolve the binary from the current repository rather than assuming a global installation:

```bash
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if [ -x "$root/.venv/bin/easy-tdx" ]; then
  TDX_BIN="$root/.venv/bin/easy-tdx"
elif command -v easy-tdx >/dev/null 2>&1; then
  TDX_BIN="$(command -v easy-tdx)"
else
  TDX_BIN=""
fi
```

The project-local installation is reproducible with:

```bash
python scripts/bootstrap.py --profile data
.venv/bin/python -m pip install "easy-tdx==1.30.3"
```

Use the repository's Python interpreter explicitly. The distribution is named `easy-tdx`, while
the import is `easy_tdx`; it requires `pandas>=2,<3`.

## Command Map

| Need | Command | Notes |
|---|---|---|
| One or more snapshots | `quote "SZ 000001,SH 600183"` | Five bid/ask levels and quote fields |
| Sorted market list | `quote-list A --count 50` | Use `--sort` and `--order` explicitly |
| K-lines | `kline SH 600183 --period DAILY --count 200` | JSON by default; `--table` is display only |
| Intraday bars | `kline SH 600183 --period 5MIN --count 240` | `1MIN`, `5MIN`, `15MIN`, `30MIN`, `60MIN` |
| Adjusted bars | `kline SH 600183 --adjust QFQ` | `NONE`, `QFQ`, or `HFQ` |
| Right-end timestamps | `kline ... --bar-time end` | Only minute bars; default is `start` |
| Today's/history chart | `tick SH 600183 --days 5` | Aggregated intraday chart data; not individual transactions |
| Transactions | `transaction SH 600183 --count 2000` | Protocol time precision and aggregation are limited |
| Auction | `auction SH 600183` | Collection-auction fields may be absent outside session |
| Capital flow | `capital-flow SH 600183` | Read the documented classification caveat |
| Breadth/alerts | `market-stat`, `unusual SH` | Current-market snapshots |
| Boards | `board-list`, `board-members`, `board-ranking`, `board-summary` | Industry/concept types are explicit options |
| Company/F10 | `company-info`, `finance-info`, `f10` | `f10` is the independent Sina source |
| Announcements | `announcement 600183 --download 5 --download-dir ./pdfs` | CNInfo; no TDX connection required |

For extended markets use the `ex` command group and its own market enum. Do not treat an extended
market's units or trading calendar as A-share units without checking the response.

## Python Fallback

When the binary is unavailable but the same interpreter imports the package, use the API directly:

```python
from easy_tdx import Adjust, MacClient, Market, Period

with MacClient.from_best_host() as client:
    quotes = client.get_stock_quotes([(Market.SH, "600183")])
    bars = client.get_stock_kline(
        Market.SH,
        "600183",
        period=Period.MIN_5,
        count=200,
        adjust=Adjust.NONE,
        bar_time="start",
    )
```

For a lower-level unadjusted request:

```python
from easy_tdx import KlineCategory, Market, TdxClient

with TdxClient.from_best_host() as client:
    bars = client.get_security_bars(Market.SH, "600183", KlineCategory.DAY, 0, 200)
```

The synchronous clients have matching `Async*` variants. Reuse one connected client for a batch;
do not call `from_best_host()` once per symbol.

## Period and Timestamp Semantics

| Value | Meaning |
|---|---|
| `MIN_1`, `MIN_5`, `MIN_15`, `MIN_30`, `MIN_60` | Intraday bars |
| `DAY`, `WEEK`, `MONTH`, `SEASON`, `YEAR` | Daily and higher bars |
| `bar-time start` | TDX bar start (for example, the last morning 5-minute bar is 11:25) |
| `bar-time end` | Right endpoint aligned to other providers (11:30 in the example) |

The package returns the newest page first at the protocol layer but its DataFrame helpers normally
return chronological rows. Verify ordering before joining with another provider. `count` is a
number of bars, not a date range; request warmup rows beyond the number shown to the user.

## Units and Provenance

Do not compare raw fields across endpoint families without a conversion table:

| Field | Typical unit/meaning |
|---|---|
| K-line `vol` | Shares |
| Quote `vol` | Lots/hands |
| K-line `amount` | Yuan |
| Transaction `vol` | Protocol transaction unit; verify before aggregating |
| Quote `price`/OHLC | Yuan per share or instrument-specific tick |

Attach a provenance object to every saved result, for example:

```json
{
  "source": "easy_tdx",
  "protocol": "MAC",
  "host": "configured-or-selected-host",
  "retrieved_at_utc": "2026-09-02T08:00:00Z",
  "market": "SH",
  "symbol": "600183",
  "period": "5MIN",
  "adjustment": "NONE",
  "bar_time": "start",
  "volume_unit": "shares",
  "provisional": false
}
```

The library's DataFrames do not provide the project's complete provenance contract automatically;
the caller must add it. Keep the actual Sina or CNInfo source when using `f10` or `announcement`.

## Request Hygiene

- Keep six-digit codes as strings and retain `SH:`/`SZ:`/`BJ:` when joining datasets.
- Validate `open <= high`, `low <= close`, and positive prices; do not silently repair rows.
- Detect duplicate timestamps and gaps. A missing bar is not a zero-volume bar.
- Mark a current-session bar provisional until the session is complete. The warehouse's default
  query excludes provisional rows; a direct CLI request does not.
- For QFQ/HFQ comparisons, retain the adjustment mode and cross-check a known corporate-action
  sample. Different providers can use different factor conventions.
- A timeout, empty response, or malformed page is reported with the symbol and endpoint. Do not
  fall back to an old cache or another provider unless the user explicitly asks for a comparison.

## Live Server Behavior

TDX/MAC servers are public protocol endpoints with variable availability and no project SLA. The
client can rank hosts and reconnect, but a successful TCP response is not a guarantee of complete or
fresh market coverage. During batch work, serialize first-time config initialization because
multiple processes can race while replacing `~/.easy_tdx/config.tmp`.

The protocol has request/response semantics rather than exchange push. Treat quote polling as
lightweight monitoring, not a low-latency execution feed.
