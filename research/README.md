# Research

Each subdirectory is an independent research direction. A direction should keep its assumptions,
data preparation, experiment entry points, and local results together so that it can evolve
without coupling unrelated studies.

## Directions

- [`short_term/`](short_term/): a short-term momentum/backtesting demo using remote AkShare data
  with AKQuant; its synthetic DuckDB fixture is reserved for offline tests.

Research outputs are local artifacts unless a result is deliberately summarized in tracked
documentation. Do not commit downloaded market data, DuckDB files, or generated reports.
