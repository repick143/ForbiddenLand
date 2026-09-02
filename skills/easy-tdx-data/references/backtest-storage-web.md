# Backtest, Storage, and Web Capabilities

These modules are useful for a self-contained prototype. They are separate from ForbiddenLand's
AKQuant pipeline and must not be treated as a drop-in replacement for it.

## Single-Strategy Backtest

The binary accepts a Python strategy file with `init()` and `next()` methods:

```bash
easy-tdx backtest SH 600183 --strategy-file strategies/macd_cross.py --count 800 --adjust QFQ
```

In 1.28.1, `--strategy-file` loads a Python `Strategy` subclass; `--strategy` is a reserved DSL
option and currently reports that the DSL path is not implemented. Inspect
`easy-tdx backtest --help` again after upgrading. The Python API is more stable for controlled
research:

```python
from easy_tdx.backtest import BacktestEngine

engine = BacktestEngine(
    MyStrategy,
    cash=100_000,
    execution="next_open",
    commission=0.0003,
    min_commission=5.0,
    stamp_tax=0.001,
    slippage=0.0,
    warmup_bars=120,
    symbol="SH:600183",
    auto_fees=True,
)
result = engine.run(bars)
```

Available controls include:

- long/full/short position modes and reject policies;
- fixed or model-based slippage, including square-root impact;
- TWAP, VWAP, and limit-style execution models;
- fixed stop-loss, take-profit, and trailing stop (OCO behavior);
- benchmark comparison, attribution, risk metrics, grading, and 25-item reports;
- parameter grids, multi-strategy portfolios, rotation, Walk-Forward, and multi-seed checks.

The simulator models a configured set of assumptions. It does not route orders to a broker. A
backtest report must include the data range, adjustment, fees, lot size, execution timing, and
whether suspended/limit bars were handled.

## Factor Portfolio and Screening

`RebalanceEngine` can rank symbols by a factor and rebalance equal-weight, factor-weighted,
risk-parity, or mean-variance portfolios. `screen scan` turns a strategy into a market-wide signal
scanner and `screen rank` backtests/ranks the resulting signals.

The documented `factor analyze` and `pfactor backtest` commands currently return a Python API
template because they need caller-supplied market data. Treat them as guidance, not a complete
remote data job. Full-market `screen scan` is documented as reading local TDX `.day` files; verify
the file dates before using it.

## DuckDB Warehouse

Install the optional warehouse dependency in a fresh environment:

```bash
python -m pip install "easy-tdx[warehouse]"
```

Typical commands:

```bash
easy-tdx warehouse sync --symbols SH:600183,SZ:000001
easy-tdx warehouse query SH 600183 --count 30
easy-tdx warehouse stats
easy-tdx warehouse check
```

The warehouse supports incremental tail synchronization, provisional current-day bars, and
freshness/gap/jump checks. Its database is a local cache, not a canonical project snapshot. Keep
the file under an ignored data/cache path and record the source and synchronization time.

## Offline Files

`offline` can read and write TDX daily/minute files, board files, equity-change files, and historical
financial files. This path requires a real TDX data directory; a normal Ubuntu or macOS checkout
usually has none. Do not infer “no data” from an absent directory without saying that the local
source is unavailable.

## Web API and UI

The optional Web extra provides FastAPI/Uvicorn:

```bash
python -m pip install "easy-tdx[web]"
easy-tdx serve --host 127.0.0.1 --port 8000 --no-open-browser
```

It exposes REST routes for quotes, bars, indicators, formulas, backtests, watchlists, and a
WebSocket route for one-symbol realtime data. The bundled UI includes a market dashboard,
watchlist, board drill-down, and backtest views. The UI's watchlist/strategy persistence is local
to the easy-tdx service and is separate from ForbiddenLand's frontend/API contracts.

The realtime WebSocket/SSE layers are poll-and-fan-out wrappers. The TDX protocol has no server
push, so the service is suitable for light monitoring and alerts, not exchange-grade streaming or
high-frequency execution. Default intervals are configurable and should be displayed in a report.

## ForbiddenLand Boundary

For this repository, keep the following flow explicit:

`easy-tdx (optional provider) -> adapter/normalizer -> DuckDB/Parquet -> research/API -> AKQuant`

Do not let frontend code invoke the binary, read the warehouse, or call TDX directly. A future
adapter must add `frequency`, `source`, `retrieved_at`, `adjustment`, unit conversions, and quality
flags to the project's normalized market contract. Do not silently write TDX data into the existing
AkShare snapshot or silently select the easy-tdx backtest engine.

## Research Disclaimer

Historical performance, ratings, and model signals are research artifacts. They contain provider,
survivorship, look-ahead, and execution-model risks and are not investment advice.
