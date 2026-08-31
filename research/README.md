# Research

Each subdirectory is an independent research direction. A direction should keep its assumptions,
data preparation, experiment entry points, and local results together so that it can evolve
without coupling unrelated studies.

## Directions

- [`short_term/`](short_term/): a short-term momentum/backtesting demo using remote AkShare data
  with AKQuant; identical remote daily requests use the provider's rebuildable cache by default,
  while its synthetic DuckDB fixture is reserved for offline tests.
- [`vsa/`](vsa/): a daily VSA indicator and AKQuant backtest demo for 生益电子 (`688183`). It
  computes missing-aware causal features, separates candidates from next-bar confirmations, records
  AKQuant indicators, and reports explicit costs, T+1 execution, provenance, and validation limits.
  Its fixture is synthetic and the default remote path uses `AkShareMarketProvider`.
- [`../frontend/src/content/volume_price_analysis.md`](../frontend/src/content/volume_price_analysis.md):
  a tracked methodology guide for VSA, Wyckoff, VPA, and Volume Profile. It is reference material,
  not an executable strategy or a source of trading labels.

Research outputs are local artifacts unless a result is deliberately summarized in tracked
documentation. Do not commit downloaded market data, DuckDB files, or generated reports.
