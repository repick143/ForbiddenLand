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
- [`order_flow/`](order_flow/): an independent easy-tdx transaction-direction order-flow proxy
  for 生益电子 (`SH:688183` by default). It audits paginated MAC transactions, verifies lot/share
  volume against daily and minute K-lines, computes causal Delta/CVD/RVOL/VWAP/absorption features,
  exposes configurable signal, timestamp-alignment, and execution parameters, and runs the easy-tdx
  backtest simulator. It also registers and saves the `order_flow_delta_ratio` custom factor using
  easy-tdx's `Factor` protocol; daily exports use the `date`/`code` long format expected by
  `FactorAnalyzer`. Its `auto` alignment records whether the observed endpoint is left- or
  right-labelled. It does not claim complete Level-2 order events or institutional identity.
- [`technical_analysis/`](technical_analysis/): a reproducible multi-timeframe technical-analysis
  generator for 生益电子 (`688183`), 生益科技 (`600183`), 甬矽电子 (`688362`), 云南锗业 (`002428`),
  晓程科技 (`300139`), 行云科技 (`300209`), 景旺电子 (`603228`) and 超纯应材 (`301717`). It
  writes the latest observation
  and conditional risk levels to the date-partitioned `../analysis_history/` journal, and compares
  each new record with the latest earlier record for the same stock.
- [`../frontend/src/content/volume_price_analysis.md`](../frontend/src/content/volume_price_analysis.md):
  a tracked methodology guide for VSA, Wyckoff, VPA, and Volume Profile. It is reference material,
  not an executable strategy or a source of trading labels.

Research outputs are local artifacts unless a result is deliberately summarized in tracked
documentation. Do not commit downloaded market data, DuckDB files, or generated reports. The
lightweight JSON files under `../analysis_history/` are the deliberate exception for the analysis
journal feature: they are user-facing historical records, not source-data payloads or backtest
reports.
