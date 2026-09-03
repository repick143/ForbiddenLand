# easy-tdx Order-Flow Proxy

This research direction implements a long-only order-flow proxy for A-share data using the
`easy-tdx` MAC transaction endpoint and matching K-lines.  It is deliberately separate from the
AKQuant VSA demo and does not require AKQuant for acquisition, feature generation, or execution
simulation.  The original strategy is V1 and remains the default; the advanced multi-scale path
described below is explicitly selected with `strategy_version="v2"` or `--strategy-version v2`.

## What the feed can and cannot say

`transaction` returns aggregated trade prints with `time`, `price`, `vol`, `trade_count`, and
`bs_flag`.  In the installed `easy-tdx==1.30.3` route, `bs_flag` is interpreted as `0=buy
direction`, `1=sell direction`, `2=neutral`, and `5=after-hours`.  `vol` is converted from the
protocol lot unit to shares with a configurable, audited factor (100 by default).  These are
aggressor-side direction proxies, not order IDs, account identities, or a complete Level-2 order
event stream.  The strategy therefore reports pressure, response, divergence, absorption, and a
bounded participation-evidence score without claiming to identify an institution.

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
  -> causal participation strength, direction, state and confidence
  -> V1 or V2 versioned entry/exit candidates
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
  "strategy_version": "v1",
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
| Participation | `participation_strong_threshold`, `participation_direction_threshold`, `participation_confirmation_bars` | Classify strong evidence, assign a direction, and require consecutive same-session confirmation |
| V2 | `v2_fast_span`, `v2_medium_span`, `v2_slow_span`, `v2_flow_window`, `v2_percentile_window`, `v2_regime_window`, `v2_min_observations`, `v2_min_component_count` | Multi-scale pressure, rolling context, regime and component-availability controls |
| V2 signals | `v2_score_entry_threshold`, `v2_score_exit_threshold`, `v2_exhaustion_threshold`, `v2_min_confidence`, `v2_use_regime_filter`, `v2_reset_each_session`, `v2_require_confirmation` | Score thresholds, exhaustion exit, confidence gate, regime filter, session carry and confirmation |
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

## V2 strategy

V1 consumes the original `of_entry_signal`/`of_exit_signal` rules based on Delta, relative volume,
close location, absorption, divergence, and VWAP filters.  V2 keeps every V1 column for comparison,
but the strategy class consumes only `of_v2_entry_signal`/`of_v2_exit_signal` when
`strategy_version="v2"`.  The report and feature frame then carry `strategy_version: "v2"` and
`order_flow_version: "order-flow-proxy-2"`; omitting the option preserves V1 behavior.

The V2 signed score is the equal-weight mean of six bounded, causal components:

| Component | Interpretation from available easy-tdx fields |
| --- | --- |
| `v2_flow_pressure` | Fast/medium/slow EWM of transaction Delta |
| `v2_execution_quality` | Whether signed price response is efficient relative to local range and flow consistency |
| `v2_absorption_score` | Hidden-demand/supply proxy when high activity produces a small price result |
| `v2_regime_alignment` | Flow agreement with trend efficiency and volatility regime |
| `v2_divergence_score` | Signed flow/price disagreement; positive means resilient price against sell flow |
| `v2_large_flow_score` | Directional large-print Delta attenuated by large-print share |

```text
order_flow_v2_score = mean(the six available components)
```

Rows with fewer than `v2_min_component_count` score components, insufficient percentile history, or
invalid/missing transactions remain `NaN` and cannot generate a V2 signal.  The parameter accepts
`2` through `6`; the separate strength stage has five percentile components and therefore caps its
own readiness requirement at five.  `v2_score_confidence` is component completeness, not a
probability.  The optional `v2_quote_imbalance` and `v2_microprice_edge` columns are used only when
five-level quote fields are present; their absence does not manufacture a book imbalance.

By default V2 resets rolling state at morning/afternoon and calendar-session boundaries.  Set
`v2_reset_each_session=false` to carry state across those known breaks; irregular gaps within a
session, missing/invalid transaction bars, and out-of-session rows still reset the state.  This
choice is recorded in `parameters` and should be held fixed when comparing experiments.

Run the V2 strategy and export its primary factor with the dedicated example configuration:

```bash
.venv/bin/python -m research.order_flow.run \
  --config research/order_flow/order_flow.v2.example.json \
  --source live \
  --symbol SH:688183 \
  --transaction-days 60 \
  --factor-output reports/order_flow_688183_v2_factor.parquet \
  --factor-manifest reports/order_flow_688183_v2_factor.manifest.json \
  --report reports/order_flow_688183_v2.json
```

The V2 factor contract is stored in
[`order_flow_factor_v2.json`](order_flow_factor_v2.json), and its easy-tdx name is
`order_flow_v2_score`.  The daily export is the mean of valid V2 bar scores for each session; use
`--factor-frequency bar` when the downstream study needs every completed 5-minute observation.
V2 remains a transaction-direction proxy: `bs_flag` is not a complete Level-2 event stream and no
component identifies an account or institution.

## Participation evidence factor

The runner also produces `order_flow_participation_score`, a `0-100` measure of whether the
current 5-minute bar contains unusually strong participation evidence.  It is not a probability,
an account classification, or a direct buy/sell signal.  Four `0-1` components have fixed equal
weights:

| Component | Causal inputs |
| --- | --- |
| Activity | Prior same-clock percentiles of transaction volume and trade count |
| Size | Prior same-clock percentiles of average trade amount and large-print volume share |
| Imbalance | Prior same-clock percentiles of absolute Delta and large-print Delta ratios |
| Control | Prior same-clock percentile of flow/price alignment or absorption strength |

```text
order_flow_participation_score = 25 * (activity + size + imbalance + control)
```

Only earlier valid observations at the same clock slot enter each rolling baseline.  The default
thresholds classify a score of at least `75` as strong evidence and a signed direction magnitude
of at least `30` as directional; two consecutive directional bars in the same morning or afternoon
segment set `participation_confirmed=true`.  State values distinguish active buying/selling,
passive buy absorption, passive sell distribution, conflicting evidence, no clear evidence, and
unavailable data.

Daily factor output is the P90 of valid 5-minute scores, which captures a session's strong-participant
peak without letting one maximum print determine the entire day.  The report separately retains
the latest score/state/direction/confirmation/confidence and the whole-session mean, peak,
score-weighted direction, dominant strong-bar state, strong/confirmed bar shares, valid-bar count,
confirmed buy/sell direction, and provisional marker.  Use enough earlier sessions for warm-up;
the following command uses 30 prior sessions and displays the latest five sessions in the JSON
report:

```bash
.venv/bin/python -m research.order_flow.run \
  --source live \
  --symbol SH:688183 \
  --transaction-days 40 \
  --volume-baseline-sessions 30 \
  --min-history-sessions 30 \
  --participation-factor-output reports/order_flow_688183_participation_factor.parquet \
  --participation-factor-manifest reports/order_flow_688183_participation_factor.manifest.json \
  --report reports/order_flow_688183_participation.json
```

The versioned definition is in
[`order_flow_participation_factor.json`](order_flow_participation_factor.json).  Large-print
classification still depends on the configured row-volume threshold, and `bs_flag` remains only an
aggressor-side proxy.  Reported confidence measures data coverage, causal-history availability,
component completeness, and score stability; it is not a predictive probability.  Compare the
factor across more symbols and out-of-sample periods before using it in a strategy.

## easy-tdx custom factors

`easy-tdx==1.30.3` exposes custom factors through the `Factor`/`register_factor` protocol.  The
project registers `order_flow_delta_ratio` (V1-compatible), `order_flow_v2_score` (V2), and
`order_flow_participation_score` (auxiliary evidence) when `research.order_flow.easy_tdx_factor`
is imported:

```python
from easy_tdx.factor import FactorEngine
from research.order_flow.easy_tdx_factor import (
    EASY_TDX_FACTOR_NAME,
    ORDER_FLOW_V2_FACTOR_NAME,
    PARTICIPATION_FACTOR_NAME,
)

# Importing the module registers the class in easy_tdx's process-local registry.
engine = FactorEngine()
factor_frame = engine.compute_single(
    order_flow_features,
    [EASY_TDX_FACTOR_NAME, ORDER_FLOW_V2_FACTOR_NAME, PARTICIPATION_FACTOR_NAME],
)
```

The factor is deliberately the direct, bounded Delta ratio:

```text
(buy_volume - sell_volume) / total_transaction_volume
```

It has no hidden scoring weights.  The participation factor's four equal weights are separately
versioned and documented above.  Missing transaction rows and rows rejected by
`of_data_valid` remain `NaN`, and the input frequency is supplied by the caller.  The definition
is also kept in [`order_flow_factor.json`](order_flow_factor.json).

The registry is process-local; easy-tdx 1.30.3 has no factor database or automatic project-module
discovery.  The runner therefore writes both a factor table and a manifest:

```bash
.venv/bin/python -m research.order_flow.run \
  --source live \
  --factor-frequency daily \
  --factor-output reports/order_flow_688183_factor.parquet \
  --factor-manifest reports/order_flow_688183_factor.manifest.json \
  --participation-factor-output reports/order_flow_688183_participation_factor.parquet \
  --participation-factor-manifest reports/order_flow_688183_participation_factor.manifest.json
```

Daily output has one row per `date`/`code` and columns `date`, `code`, `symbol`, `datetime`, and
the selected factor column, matching the long-format inputs expected by `FactorAnalyzer`.  Use
`--factor-frequency bar` to retain every 5-minute timestamp for intraday inspection; that output
must be explicitly aggregated by session before daily cross-sectional IC or quantile analysis.
The manifest records the actual file format/path, factor contract, source host, retrieval time,
period, timestamp convention, volume units, and missing-value count.

## Future-return prediction experiment

The factor is an input to a prediction experiment, not a prediction by itself.  The first version
is implemented in [`predict.py`](predict.py) and consumes the feature CSV produced by the main
runner.  It has three explicit stages:

```text
causal feature at bar close
  -> next-open/future-open label within one continuous session
  -> factor quantile event study and rolling Ridge expected-return estimate
```

Generate a bar-level feature file first, then run a chronological walk-forward experiment:

```bash
.venv/bin/python -m research.order_flow.run \
  --source live \
  --symbol SH:688183 \
  --bar-minutes 5 \
  --factor-frequency bar \
  --features reports/order_flow_688183_features.csv \
  --factor-output reports/order_flow_688183_factor_bar.parquet \
  --report reports/order_flow_688183.json

.venv/bin/python -m research.order_flow.predict \
  --features reports/order_flow_688183_features.csv \
  --source-report reports/order_flow_688183.json \
  --config research/order_flow/order_flow_prediction.example.json \
  --output reports/order_flow_688183_predictions.csv \
  --report reports/order_flow_688183_predictions.json
```

Use `--latest` on the second command to fit on completed prior sessions and score the latest
session, whose future label is naturally unavailable.  Historical backtests must use the default
walk-forward mode so the test labels never influence a fitted model.

For a signal at bar `t` observed at the close, with `next_open` execution, the default target is:

```text
r(t, h) = open(t+h+1) / open(t+1) - 1
```

`horizon_bars` counts bars, not calendar days.  The implementation rejects missing intermediate
timestamps and never crosses the lunch break or overnight boundary.  Rows failing
`of_data_valid`, `of_history_ready`, or the future-window check remain in the output with an
explicit eligibility flag and reason rather than being filled with zero.

The model uses the available subset of these causal columns:
`order_flow_delta_ratio`, `delta_ratio_zscore`, `relative_transaction_volume`, `clv`,
`bar_return`, `vwap_distance`, and `flow_price_divergence`, plus configurable lags of the order-flow
factor.  Imputation and standardization are fitted inside each training window.  Training,
validation, and test windows are ordered by trading date; labels that end at or after the next
window are purged.  `threshold_grid` is selected on validation data only, while
`round_trip_cost` and `edge_buffer` must be set from the intended fee/slippage assumptions.

The prediction output adds `future_return`, `predicted_return`, `prediction_signal`,
`prediction_threshold`, `prediction_fold`, and `prediction_train_rows`.  `prediction_signal` is a
research entry candidate based on expected return.  To run it through the existing fee- and
T+1-aware simulator in Python:

```python
from research.order_flow.predict import run_prediction_backtest, walk_forward_predict

predictions = walk_forward_predict(features, config=prediction_config)
backtest = run_prediction_backtest(predictions.frame, config=order_flow_config)
```

`run_prediction_backtest` masks incomplete historical labels and maps the model output to the
existing order-flow strategy.  It does not make a short intraday label executable under A-share
T+1; a short horizon can still be useful as a signal diagnostic, while the actual strategy must
respect sellability, fees, lot size, stops, and holding limits.  Single-symbol three-month runs
are pipeline checks, not evidence of a stable edge.  For cross-sectional IC, export daily factors
for multiple symbols and define the universe and date-level ranking before fitting.

## Interpretation and limitations

Positive Delta with a positive bar response is consistent with active demand.  Large positive Delta
with a weak/down response is a bearish absorption candidate; large negative Delta with a stable/up
response is a bullish absorption candidate.  Neither pattern proves an institution was present.
The transaction endpoint can aggregate multiple executions, has limited timestamp precision, and is
request/response polling rather than exchange push.  A successful backtest is therefore a
reproducible hypothesis test, not an execution guarantee or investment advice.
