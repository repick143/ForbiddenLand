# easy-tdx Order-Flow Proxy

This research direction implements a long-only order-flow proxy for A-share data using the
`easy-tdx` MAC transaction endpoint and matching K-lines.  It is deliberately separate from the
AKQuant VSA demo and does not require AKQuant for acquisition, feature generation, or execution
simulation.

## What the feed can and cannot say

`transaction` returns aggregated trade prints with `time`, `price`, `vol`, `trade_count`, and
`bs_flag`.  In the installed `easy-tdx==1.30.3` route, `bs_flag` is interpreted as `0=buy
direction`, `1=sell direction`, `2=neutral`, and `5=after-hours`.  `vol` is converted from the
protocol lot unit to shares with a configurable, audited factor (100 by default).  These are
aggressor-side direction proxies, not order IDs, account identities, or a complete Level-2 order
event stream.  The strategy therefore reports pressure, response, divergence, and absorption
candidates instead of a fabricated institutional-money score.

The default target bar is a 5-minute easy-tdx K-line with `adjust=NONE`.  Continuous-session
prints are used for signals; auction and after-hours prints remain in the reconciliation report and
can be enabled explicitly.  Missing transaction coverage remains missing rather than becoming a
zero.  `transaction_alignment="auto"` inspects the returned session boundaries: the currently
validated MAC host labels minute bars by their right endpoint (`09:35`/`13:05` for a 5-minute
request), so live data resolves to `ceil`; the left-endpoint synthetic fixture resolves to `floor`.
For the right-endpoint convention, a print exactly at `09:30` or `13:00` belongs to the following
labelled bar, and the `11:30` morning terminal bar is retained while the `15:00` closing-auction
bar remains excluded from continuous-session signals.

## Pipeline

```text
easy-tdx MAC client (one host)
  -> daily/5MIN/1MIN/transaction/quote/auction snapshots
  -> page, timestamp, session, unit and volume audits
  -> transaction-to-bar aggregation
  -> causal Delta/CVD/RVOL/VWAP/impact/divergence features
  -> configurable entry/exit candidates
  -> easy-tdx BacktestEngine (next bar, fees, lot size)
```

The collector audits every transaction page and compares transaction shares with daily and 1-minute
K-line volume.  It uses an explicit MAC host (the cached best host by default), so parallel first-
time host selection cannot corrupt `~/.easy_tdx/config.tmp`.

## Run

The default live path uses 生益电子 (`SH:688183`).  It requests the latest 120 available trading
dates for transaction data, which is a practical same-time baseline horizon, not a fixed strategy
holding window.  Use `--transaction-days 0` for all dates returned by the K-line endpoint, or set
explicit dates:

```bash
.venv/bin/python -m research.order_flow.run \
  --symbol SH:688183 \
  --transaction-days 60 \
  --validation-days 3 \
  --report reports/order_flow_688183.json
```

For a deterministic offline smoke test:

```bash
.venv/bin/python -m research.order_flow.run \
  --source fixture \
  --report reports/order_flow_fixture.json
```

The command writes an ignored JSON report, a feature CSV, and a transaction Parquet (or CSV when
the Parquet extra is unavailable).  The report contains source host/version, retrieval times,
frequency, adjustment, units, page audits, quality warnings, parameters, raw engine metrics, and
metrics recomputed at one point per trading day.  The latter is necessary because the bundled
easy-tdx performance analyzer treats each intraday bar as a day for annualization.
When the latest source date is still trading, the feature frame and report mark it as provisional
and add a quality warning; it is not silently presented as a complete session.

## Parameters

All parameters are available in `OrderFlowConfig` and serialized by `as_dict()`.  They can be set
programmatically or in a UTF-8 JSON file.  Command-line values supplied explicitly override the
file; omitted options keep the file value.

```json
{
  "bar_minutes": 5,
  "transaction_alignment": "auto",
  "volume_baseline_sessions": 30,
  "min_history_sessions": 30,
  "min_transaction_coverage": 0.90,
  "entry_delta_ratio": 0.25,
  "entry_delta_zscore": 1.0,
  "entry_rvol": 1.40,
  "entry_persistence": 2,
  "use_vwap_filter": true,
  "entry_vwap_distance": 0.001,
  "stop_loss_pct": 0.03,
  "take_profit_pct": 0.08,
  "position_mode": "percent",
  "position_fraction": 0.50
}
```

Run the same experiment with a file and override one value from the shell:

```bash
.venv/bin/python -m research.order_flow.run \
  --config research/order_flow/order_flow.example.json \
  --entry-rvol 1.60 \
  --report reports/order_flow_custom.json
```

The most useful controls are:

| Group | Parameters | Effect |
| --- | --- | --- |
| Data | `bar_minutes`, `transaction_alignment`, `transaction_lot_size`, `volume_baseline_sessions`, `min_history_sessions`, `large_trade_lots` | Target interval, left/right endpoint mapping, unit conversion, same-clock warm-up and large-print definition |
| Quality | `min_transaction_coverage`, `max_transaction_coverage`, `min_large_trade_share`, `max_large_trade_share`, `unknown_direction_policy` | Reject incomplete/over-counted bars or require a share of large prints; handle unknown flags |
| Session/CVD | `include_auction`, `include_after_hours`, `cvd_reset_each_session`, `persistence_same_session` | Retain non-continuous records for audit, reset CVD, and prevent persistence across breaks; signal bars remain continuous-session bars |
| Entry | `entry_delta_ratio`, `entry_delta_zscore`, `entry_rvol`, `entry_close_location`, `entry_price_return`, `entry_persistence`, `use_vwap_filter`, `entry_vwap_distance` | Demand pressure, standardized pressure, relative volume, close strength, pullback allowance and persistence |
| Exit | `exit_delta_ratio`, `exit_delta_zscore`, `exit_rvol`, `exit_close_location`, `exit_price_return`, `exit_persistence`, `use_absorption_exit`, `absorption_rvol`, `absorption_max_abs_return`, `divergence_price_threshold`, `use_vwap_exit_filter`, `exit_vwap_distance` | Supply, standardized pressure, bearish absorption, flow/price disagreement and VWAP loss |
| Risk | `min_hold_bars`, `max_hold_bars`, `stop_loss_pct`, `take_profit_pct`, `cooldown_bars`, `t_plus_one`, `flat_at_session_end` | Holding, close/high/low risk exits, cooldown and A-share sellability |
| Execution | `position_mode`, `order_size`, `position_fraction`, `initial_cash`, `commission_rate`, `min_commission`, `stamp_tax_rate`, `slippage_per_share`, `execution`, `reject_policy`, `auto_fees`, `warmup_bars`, `signal_path` | easy-tdx simulator, fee, warm-up and signal-path assumptions |

The defaults are research starting points, not fitted values.  Keep a calibration period and an
untouched out-of-sample period when tuning them.  The simulator uses 100-share lots in
`easy-tdx==1.30.3`; the configuration rejects a different lot size rather than silently claiming
unsupported behavior.  `min_transaction_coverage` and `min_large_trade_share` are disabled at
zero; `max_*` and Z-score filters are disabled at `None`.  `entry_price_return` and VWAP distances
are signed, so a negative entry threshold can deliberately test a demand response during a
pullback.  `max_hold_bars=0` is the CLI spelling for `None` (no time exit).

`transaction_alignment` accepts `auto`, `floor`, or `ceil`.  Use `floor` when the source labels
the beginning of each interval; use `ceil` for the observed MAC right-endpoint labels.  The
resolved value is written to the feature CSV and report provenance so a run never depends on an
unstated timestamp assumption.

Unless `flat_at_session_end` or an explicit exit closes the position, the final mark may remain
open.  The report exposes `open_position_shares`, includes the mark-to-market value in `end_value`,
and emits a warning so an apparently positive return is not mistaken for a fully realized result.

For practical calibration, change one parameter family at a time and retain the complete JSON
parameter snapshot in the report.  A useful three-run comparison is:

```bash
# More selective: require cleaner coverage and two-bar confirmation.
.venv/bin/python -m research.order_flow.run \
  --config research/order_flow/order_flow.example.json \
  --min-transaction-coverage 0.95 --entry-persistence 2 \
  --report reports/of_strict.json

# More responsive: lower pressure/volume thresholds and allow one-bar confirmation.
.venv/bin/python -m research.order_flow.run \
  --config research/order_flow/order_flow.example.json \
  --entry-delta-ratio 0.15 --entry-rvol 1.10 --entry-persistence 1 \
  --report reports/of_responsive.json
```

The collector controls are also adjustable from the CLI: `transaction-days`, `transaction-max-rows`,
`transaction-page-size`, `bar-count`, `daily-count`, `validation-days`, `collector-timeout`, and
`--no-fetch-quote`/`--no-fetch-auction`.  These change data coverage and network cost, not the
signal definition.

## easy-tdx custom factor

`easy-tdx==1.30.3` exposes custom factors through the `Factor`/`register_factor` protocol.  The
project registers `order_flow_delta_ratio` when `research.order_flow.easy_tdx_factor` is imported:

```python
from easy_tdx.factor import FactorEngine
from research.order_flow.easy_tdx_factor import EASY_TDX_FACTOR_NAME

# Importing the module registers the class in easy_tdx's process-local registry.
engine = FactorEngine()
factor_frame = engine.compute_single(order_flow_features, [EASY_TDX_FACTOR_NAME])
```

The factor is deliberately the direct, bounded Delta ratio:

```text
(buy_volume - sell_volume) / total_transaction_volume
```

It has no hidden scoring weights.  Missing transaction rows and rows rejected by
`of_data_valid` remain `NaN`, and the input frequency is supplied by the caller.  The definition
is also kept in [`order_flow_factor.json`](order_flow_factor.json).

The registry is process-local; easy-tdx 1.30.3 has no factor database or automatic project-module
discovery.  The runner therefore writes both a factor table and a manifest:

```bash
.venv/bin/python -m research.order_flow.run \
  --source live \
  --factor-frequency daily \
  --factor-output reports/order_flow_688183_factor.parquet \
  --factor-manifest reports/order_flow_688183_factor.manifest.json
```

Daily output has one row per `date`/`code` and columns `date`, `code`, `symbol`, `datetime`, and
`order_flow_delta_ratio`, matching the long-format inputs expected by `FactorAnalyzer`.  Use
`--factor-frequency bar` to retain every 5-minute timestamp for intraday inspection; that output
must be explicitly aggregated by session before daily cross-sectional IC or quantile analysis.
The manifest records the actual file format/path, factor contract, source host, retrieval time,
period, timestamp convention, volume units, and missing-value count.

## Interpretation and limitations

Positive Delta with a positive bar response is consistent with active demand.  Large positive Delta
with a weak/down response is a bearish absorption candidate; large negative Delta with a stable/up
response is a bullish absorption candidate.  Neither pattern proves an institution was present.
The transaction endpoint can aggregate multiple executions, has limited timestamp precision, and is
request/response polling rather than exchange push.  A successful backtest is therefore a
reproducible hypothesis test, not an execution guarantee or investment advice.
