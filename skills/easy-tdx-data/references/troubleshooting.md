# Setup and Troubleshooting

Use the smallest diagnostic that answers the failure. Keep credentials and private account data
out of logs and reports; easy-tdx's public TDX/MAC servers do not require a user trading account.

## Installation

Preferred project-local setup:

```bash
python scripts/bootstrap.py --profile data
.venv/bin/python -m pip install "easy-tdx==1.30.3"
.venv/bin/python -m pip check
.venv/bin/easy-tdx --version
```

The package name is `easy-tdx`; `import easy_tdx` is the Python spelling. It requires
`pandas>=2,<3`, so installing it can downgrade a pandas 3 environment. Check the project's tests
after installation and do not claim that a fresh bootstrap includes the package until it is added
to the project's dependency declaration.

Optional capabilities:

| Feature | Extra | Check |
|---|---|---|
| Spearman IC/factor science | `easy-tdx[science]` | `python -c 'import scipy'` |
| DuckDB warehouse | `easy-tdx[warehouse]` | `python -c 'import duckdb'` |
| REST/WebSocket/UI | `easy-tdx[web]` | `python -c 'import fastapi, uvicorn'` |
| Package tests/lint | `easy-tdx[dev]` | `pytest --version` |

Use the same Python interpreter for the install and the subsequent command. A package installed in
the shell is not guaranteed to be importable in an isolated code runtime.

## Connectivity

Start with a version check, then a single bounded ping:

```bash
easy-tdx --version
EASY_TDX_TIMEOUT=5 easy-tdx ping
```

If automatic host selection fails, set `EASY_TDX_MAC_HOST` (for MAC commands) or `EASY_TDX_HOST`
(for standard TDX commands) to a known reachable host, and report that the host was explicit.
Do not loop indefinitely or retry malformed parameters. Retry only transient connection/timeout
failures, with a finite count and visible warning.

The client stores selected-host metadata under `~/.easy_tdx/config.json` by default. Set
`EASY_TDX_CONFIG_DIR` to isolate a process or test. Multiple first-time processes can race on the
shared `config.tmp`; serialize the first `from_best_host()` call or preconfigure a host.

## Command Failures

| Symptom | Likely cause | Action |
|---|---|---|
| `command not found: easy-tdx` | Project `.venv/bin` is not on `PATH` | Call `.venv/bin/easy-tdx` or activate `.venv` |
| `ModuleNotFoundError: easy_tdx` | Different Python runtime | Probe and install with that runtime's `-m pip` |
| Empty K-line response | Bad code/market, unavailable host, or no history | Check symbol/exchange, ping, and report the empty result |
| `FileNotFoundError` for `config.tmp` | Concurrent first-time host selection | Retry serially or set an explicit host/config directory |
| Web command import error | Web extra missing | Install `easy-tdx[web]`; do not silently disable the route |
| Spearman IC import error | SciPy extra missing | Install `easy-tdx[science]` or explicitly choose Pearson |
| `fund-flow` CLI says unimplemented | CLI is a placeholder | Use the documented Python history-flow API and label it accordingly |
| Formula parse error | Formula outside the supported dialect | Report the position; simplify to whitelist functions |
| Chanlun output all zeros | Analyser exception or insufficient data | Treat as a quality warning and inspect the Python result |

An error on one symbol in a batch should be isolated and reported with the symbol, command, and
reason. Do not convert an error into an all-zero metric.

## Data-Quality Checklist

Before analysis or persistence, check:

1. Symbol and exchange match the request, including leading zeroes.
2. Dates/times are monotonic and duplicate-free; `bar_time` semantics are recorded.
3. OHLC relationships and price positivity are plausible without silently repairing rows.
4. Volume/amount units match the endpoint and the downstream formula.
5. Current-session bars are marked provisional when the session is incomplete.
6. Adjustment mode is consistent with the calculation; do not mix QFQ prices with raw spreads.
7. The source, host, retrieval time, period, and fallback are saved with the result.

Missing data is distinct from numeric zero. A local `.day` or DuckDB file is not a substitute for a
failed live request unless the user explicitly requests offline analysis and its effective date is
known.

## Documentation/API Mismatches

The installed 1.30.3 implementation should be treated as authoritative when an example conflicts:

- `FactorAnalyzer.full_report()` takes no factor-name argument; select `factor_col` in the
  constructor instead.
- Report attributes are `ic_mean` and `ir`, not `mean_ic` and `icir`.
- `factor analyze` and `pfactor backtest` print Python templates because they require caller data;
  they are not complete remote-data pipelines.
- `fund-flow` is explicitly unimplemented in the CLI even though a history method exists in the
  Python client.

Record the package version in reproducibility notes and rerun `--help` after upgrading.

## Security and Scope

The library speaks a reverse-engineered public market-data protocol. It is appropriate for research
and lightweight monitoring, but it has no exchange or broker SLA. It does not provide live trading
credentials or order routing. Review network, licensing, rate, and provider terms before deploying
it beyond personal research.
