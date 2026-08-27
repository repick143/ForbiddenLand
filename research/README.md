# Research

Each subdirectory is an independent research direction. A direction should keep its assumptions,
data preparation, experiment entry points, and local results together so that it can evolve
without coupling unrelated studies.

## Directions

- [`short_term/`](short_term/): a short-term momentum/backtesting demo using remote AkShare data
  with AKQuant; identical remote daily requests use the provider's rebuildable cache by default,
  while its synthetic DuckDB fixture is reserved for offline tests.
- [`../frontend/src/content/volume_price_analysis.md`](../frontend/src/content/volume_price_analysis.md):
  a tracked methodology guide for VSA, Wyckoff, VPA, and Volume Profile. It is reference material,
  not an executable strategy or a source of trading labels.

Research outputs are local artifacts unless a result is deliberately summarized in tracked
documentation. Do not commit downloaded market data, DuckDB files, or generated reports.
