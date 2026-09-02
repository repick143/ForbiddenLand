---
name: easy-tdx-data
description: >
  Fetch, validate, and analyze Chinese A-share market data with the easy-tdx CLI/binary and
  its Python API. Use for "用 easy-tdx 查行情", "通达信数据", real-time quotes, five-level
  order books, daily or minute K-lines, intraday/tick transactions, auction data, capital flow,
  board rankings, F10/announcements, technical indicators, Chanlun, VSA inputs, TongdaXin
  formulas, factor research, screening, backtesting, or exporting data. Also trigger when the
  user asks "今天这只股票走势", "拉分钟线", "最近有没有放量", "把这几个票跑一遍",
  or wants a second data source for AkShare. Prefer the wrapped easy-tdx binary when present;
  fall back to the matching Python runtime or an explicit manual-data path. This skill is for
  research and monitoring, not live order placement or investment advice.
version: 1.0.0
author: chenchen
platforms: [macos, linux]
metadata:
  hermes:
    tags: [A-share, TDX, market-data, technical-analysis, VSA, quant]
    related_skills: [technical-analysis, tushare, china-stock-analysis]
    category: market-analysis
---

# easy-tdx Data

Use this skill as the data-source wrapper for repeated TDX research tasks. Keep the source,
observation time, frequency, adjustment mode, units, and any fallback visible in the answer.
Never present a data-source failure as a market fact.

## Step 1: Detect the Runtime and Data Path

Run the following probes before fetching data. Every probe has a sentinel so a missing tool does
not become a silent failure.

**Fast runtime probes:**

!`(command -v easy-tdx && easy-tdx --version) 2>/dev/null || echo "EASY_TDX_GLOBAL_MISSING"`

!`(root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"; test -x "$root/.venv/bin/easy-tdx" && "$root/.venv/bin/easy-tdx" --version) 2>/dev/null || echo "EASY_TDX_PROJECT_BINARY_MISSING"`

!`python3 -c 'import easy_tdx; print("EASY_TDX_IMPORT=" + easy_tdx.__version__)' 2>/dev/null || echo "EASY_TDX_IMPORT_MISSING"`

**Binary and Python runtime:**

```bash
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
bin=""
if [ -x "$root/.venv/bin/easy-tdx" ]; then bin="$root/.venv/bin/easy-tdx"
elif command -v easy-tdx >/dev/null 2>&1; then bin="$(command -v easy-tdx)"
fi
if [ -n "$bin" ] && "$bin" --version >/dev/null 2>&1; then
  echo "EASY_TDX_BIN=$bin"
else
  echo "EASY_TDX_BINARY_MISSING"
fi

py=""
if [ -x "$root/.venv/bin/python" ] && "$root/.venv/bin/python" -c 'import easy_tdx' >/dev/null 2>&1; then
  py="$root/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import easy_tdx' >/dev/null 2>&1; then
  py="$(command -v python3)"
fi
if [ -n "$py" ] && "$py" -c 'import easy_tdx; print(easy_tdx.__version__)' 2>/dev/null; then
  echo "EASY_TDX_PYTHON=$py"
else
  echo "EASY_TDX_PYTHON_MISSING"
fi
```

**Optional modules and local data:**

```bash
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
py=""
if [ -x "$root/.venv/bin/python" ] && "$root/.venv/bin/python" -c 'import easy_tdx' >/dev/null 2>&1; then
  py="$root/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import easy_tdx' >/dev/null 2>&1; then
  py="$(command -v python3)"
fi
for mod in duckdb scipy fastapi uvicorn; do
  if [ -n "$py" ] && "$py" -c "import $mod" >/dev/null 2>&1; then
    echo "${mod}_OK"
  else
    echo "${mod}_MISSING"
  fi
done
local_data="$(find "$root" -type f \( -name '*.day' -o -name '*.duckdb' \) -print -quit 2>/dev/null)"
if [ -n "$local_data" ]; then
  echo "LOCAL_TDX_DATA=$local_data"
else
  echo "LOCAL_TDX_DATA_NOT_FOUND"
fi
```

**Reachability (only when a live request is needed):** run `"$bin" ping` with a finite command
timeout or use an explicit host (`EASY_TDX_MAC_HOST` for MAC routes,
`EASY_TDX_HOST` for standard TDX routes). Do not repeatedly rescan servers inside a batch.

**Decision tree:**

1. `EASY_TDX_BIN` plus a successful ping: use the binary, JSON output, and the route in Step 2.
2. Binary exists but ping fails: retry once with the configured `EASY_TDX_MAC_HOST` or a known
   host; report the unavailable source if it still fails. Never substitute stale local data.
3. Binary is missing but `EASY_TDX_PYTHON` imports: invoke the interpreter selected by the probe
   with `-m easy_tdx` (after checking `--help`) or use the Python API; terminal and any isolated
   code runtime must be checked separately.
4. Neither runtime is available: stop and give the project-local install command. Do not install
   into a global interpreter without an explicit request.
5. The user explicitly asks for offline data: use `.day`/DuckDB only after confirming the path,
   source date, and whether current-day provisional bars are allowed.

**Step 1 exit gate:** select exactly one usable path (project binary, global binary, same-runtime
module/API, or an explicit offline path). If none is usable, stop with the installation or data-path
message instead of attempting a request.

## Step 2: Parse the Request and Choose Defaults

Resolve the user intent before constructing a command. Do not invent a stock universe; use only
symbols the user supplied unless they explicitly request a dynamic market-wide scan.

| Parameter | Default | Rule |
|---|---|---|
| Market | A-share (`SH`/`SZ`/`BJ`) | Infer exchange from a 6-digit code; preserve the code as a string |
| Symbols | User-provided symbols | If absent, report that a symbol is required and stop |
| Operation | Quote/trend lookup | Route to the narrowest command that answers the request |
| K-line period | `DAILY` | Use `5MIN` for intraday/VSA unless the user specifies another period |
| Date range | Latest available bars | Honor an explicit range; `count` is a bar count, not a date range |
| Bar count | 200 | Use at least 200 for indicators; use the requested count for display |
| Warmup | 120-200 bars for EMA-derived indicators | Keep warmup rows out of displayed conclusions |
| Adjustment | `QFQ` for price/trend; `NONE` for raw VSA/transaction work | Always print the choice |
| Minute timestamp | `start` | Use `end` only when aligning with Tushare/other right-endpoint bars |
| Output | JSON internally, concise tables in the answer | Never parse human-formatted table output |
| Factor horizon | 5 bars | State that this is bars, not calendar days |
| Factor quantiles | 5 | Do not claim statistical significance from a small cross-section |
| Backtest execution | `next_open` | Disclose fees, lot rules, slippage, and date range |
| Cache/fallback | No stale fallback | A live failure remains an explicit failure |

**Step 2 deliverable:** a resolved request containing symbols, exchange, operation, period, date
window/count, adjustment, and any user-supplied execution assumptions.

## Step 3: Route and Fetch Data

Use the CLI first because it is the installed wrapper and returns machine-readable JSON by default.
Read `references/market-data.md` for the command map, Python equivalents, pagination, and field
units.

| Intent | Preferred binary route | Python/API fallback |
|---|---|---|
| Quote or five levels | `quote`, `symbol-info`, `quote-list` | `MacClient.get_stock_quotes` |
| Daily/intraday K-line | `kline` | `MacClient.get_stock_kline` |
| VSA inputs | `kline`, `tick`, `transaction`, `auction` | `MacClient.get_stock_kline`, `get_tick_chart`, `get_transactions` |
| Board/market breadth | `board-*`, `market-stat`, `unusual` | matching `MacClient` board/stat methods |
| Financial/news | `finance-info`, `company-info`, `f10`, `announcement` | TDX, Sina, or Cninfo clients as documented |
| Offline/repeated queries | `offline`, `warehouse` | `easy_tdx.offline` / `easy_tdx.warehouse` |

For a missing binary, do not assume a different Python runtime has the package. Re-run the import
probe in that runtime, then use the API fallback or provide the exact installation instruction.
For multiple live symbols, reuse one client and keep requests bounded; serialize first-time server
selection to avoid the shared `config.tmp` initialization race.

**Step 3 exit gate:** retain only responses whose status, symbol, JSON shape, and row availability
have been checked; isolate failed symbols and record their endpoint and reason.

## Step 4: Validate and Normalize the Response

Treat a command as successful only when its exit status is zero, JSON parses, the symbol matches,
and the result is non-empty (unless an empty result is itself meaningful). Preserve missing fields.

- Keep exchange-qualified identifiers and leading zeroes (`SH:600183`, `SZ:000001`).
- Record `source=easy_tdx/TDX` or the actual Sina/Cninfo source, `retrieved_at`, period,
  adjustment, host when available, and whether data came from local files.
- Normalize volume units before comparing sources: K-line `vol` is shares, quote volume is lots,
  and transaction fields have their own protocol units.
- Keep TDX bar timestamps explicit (`start` versus `end`) and mark incomplete current-day bars.
- Check monotonic dates, duplicate bars, non-positive prices, and impossible OHLC relationships.
- A remote alternate endpoint, empty page, timeout, or malformed row is a data-quality finding;
  include symbol and field in the report.

If validation fails, return the valid subset with a prominent quality note, or stop when the subset
could change the conclusion. Do not silently switch to AkShare, an old cache, or a local snapshot.

**Step 4 deliverable:** normalized rows plus provenance and quality flags, or an explicit blocked
result when the data cannot support the requested conclusion.

## Step 5: Compute the Requested Research Output

Use the binary subcommand where it performs the complete operation; use Python for custom factors,
cross-sectional work, or calculations that need explicit control. Read `references/research.md`
for the indicator/factor catalog, formula syntax, Chanlun/VSA workflow, and known implementation
gaps.

- Indicators: request enough warmup bars, then show the final values and the input period.
- Factors: distinguish single-stock values from cross-sectional IC/quantile results; never call
  `pe_ratio` or `pb_ratio` valid until financial inputs are supplied and non-missing.
- Formula: use the whitelist parser only; named boolean outputs are signals, numeric outputs are
  values. Do not use Python `eval` or execute untrusted formula files.
- Chanlun: report the analyzed frequency and treat all-zero output as possibly an exception,
  not automatically as “no signal”.
- VSA: combine spread, relative volume, close-location/value, effort-result, and next-bar
  follow-through. `easy_tdx` supplies data; it does not provide a validated VSA strategy.
- Backtest: disclose that the engine is easy-tdx's independent pandas simulator, not AKQuant.

**Step 5 deliverable:** reproducible calculations with the input frequency, warmup, parameters, and
any follow-through or sample-size requirement stated alongside the values.

## Step 6: Reproduce, Explain, and Respond

Return exactly these sections (omit a section only when the user requested raw data only):

1. **Request and scope** — symbols, market, operation, period, date/count, adjustment.
2. **Data provenance** — source/endpoint or offline path, retrieval time, host, timestamp and
   volume conventions, plus any fallback.
3. **Observed data** — compact table or key rows; distinguish missing from zero.
4. **Calculations** — indicator/factor/formula/backtest parameters and the relevant result values.
5. **Interpretation** — evidence first, then a qualified research conclusion; do not turn a
   snapshot into a prediction.
6. **Quality and limitations** — unavailable fields, stale/provisional bars, unit caveats,
   server failures, and any documented API mismatch.
7. **Reproduction command** — the exact binary command or Python entry point used.

End with: “以上为基于 TDX 公开行情数据的研究结果，不构成投资建议；实时数据、复权、资金
流和回测结果需要结合独立来源与样本外验证。”

## Reference Files

- `references/market-data.md` — CLI/API routes, periods, adjustments, fields, units, and provenance
- `references/research.md` — indicators, factors, formulas, Chanlun, VSA, and analysis caveats
- `references/backtest-storage-web.md` — backtest, portfolio, screening, DuckDB warehouse, and Web
- `references/troubleshooting.md` — setup, connectivity, concurrency, optional extras, and data-quality failures
