# Short-Term Research

This direction contains the first runnable AKQuant example. Its default path uses the project's
configured `AkShareMarketProvider`, including bounded retries and the explicit Tencent historical
fallback, before passing normalized bars to AKQuant. Existing local Parquet/DuckDB snapshots are
not read until they have been revalidated and explicitly approved:

```text
remote AkShare provider -> normalized pandas frame -> AKQuant backtest -> JSON report
```

The default run uses a fixed historical window (`20240101` through `20240331`) and qfq prices.
The report records the actual provider source and storage (including a Tencent fallback), window,
adjustment mode, and retrieval time. Results are for research only, not investment advice.

Run it from the repository root with the project's Python environment:

```bash
python -m research.short_term.demo
```

The default run writes only the ignored report `reports/short_term_demo.json`; it does not read
local market snapshots. Paths and the remote window can be changed on both macOS and Windows:

```text
python -m research.short_term.demo --start-date 20240101 --end-date 20240331 --adjust qfq --report reports/short_term_demo.json
```

For offline pipeline tests only, use the explicit synthetic fixture. This path writes an ignored
DuckDB file and must not be treated as validated market data:

```text
python -m research.short_term.demo --source fixture --database data/cache/short_term_demo.duckdb --report reports/short_term_demo.json
```

The strategy is deliberately small: after a three-bar warm-up, it buys 100 shares when the close
is above its three-bar mean and exits when the close falls below that mean. Signals are evaluated
at the bar close and use AKQuant's `NextOpen` fill policy. Commission, stamp tax, transfer fee,
lot size, and T+1 behavior are explicit demo settings so the experiment can be extended without
hidden execution assumptions.
