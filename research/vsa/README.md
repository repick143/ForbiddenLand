# Daily VSA Demo

This direction contains a runnable, research-only Volume Spread Analysis (VSA) example for
生益电子 (`688183`).  The company code is intentional: the existing project fixture
生益科技 (`600183`) is a different security.

## Pipeline

```text
AkShareMarketProvider
  -> normalized daily OHLCV
  -> causal pandas features
  -> VSA candidate events
  -> next-bar confirmation
  -> AKQuant strategy (NextOpen + T+1)
  -> indicator export and JSON report
```

The default path in [`run.py`](run.py) uses the configured remote provider and records its source,
backend, storage, adjustment mode, retrieval time, and cache-hit state.  It does not read the
unreviewed local market snapshots.  Use the synthetic fixture only for deterministic offline
checks:

```bash
.venv/bin/python -m research.vsa.run --source fixture \
  --report reports/vsa_688183.json \
  --indicators reports/vsa_688183_indicators.json \
  --features reports/vsa_688183_features.csv
```

For a remote run without date arguments, the CLI uses the current Shanghai date and the preceding
three calendar months (same day-of-month when possible).  The resolved dates are written to the
report.  Use explicit dates when reproducing an older run:

```bash
.venv/bin/python -m research.vsa.run \
  --adjust qfq

.venv/bin/python -m research.vsa.run \
  --start-date 20240101 --end-date 20241231 --adjust qfq
```

`reports/` is ignored by Git.  The optional CSV keeps undefined ratios as empty values for
inspection; the private frame passed to AKQuant uses an explicit zero sentinel only where the
engine requires numeric extra fields.  The strategy reconstructs validity flags before recording
indicators, so warm-up and undefined observations are omitted as sparse points rather than shown
as zeroes.

## Features and rules

`features.py` computes spread, body and wick geometry, close location, CLV, volume/spread ratios,
prior range levels, trend context, and data-quality flags.  Every rolling baseline shifts one bar
before calculating its window, so a later observation cannot change an earlier feature.

`rules.py` currently labels five first-pass candidates:

- No Supply
- Stopping Volume
- Test
- No Demand
- Upthrust

A candidate is not a trade.  Only the following bar can mark it `confirmed`, `invalidated`, or
`expired`; an executable `vsa_confirmed_signal` is written on that following bar.  Bullish
confirmations carry a candidate-low stop and a configured risk/reward target.  Thresholds are
conventional defaults in `VSAConfig`, versioned in the report, and deliberately not optimized for
this one symbol.

## Execution assumptions and limits

The strategy is long-only, submits confirmed entries with AKQuant `NextOpen()`, applies commission,
stamp tax, transfer fee, slippage, 100-share lots, and `t_plus_one=True`.  Because a newly bought
A-share position is not sellable on the entry day, the real stop order is installed on the first
later bar with available shares.  Intrabar target attainment is observed from the bar high and
exited at the next open; the stop is a `stopmarket` order.  Overnight gaps and tick-size rounding
remain visible in the trades and orders tables.

The fixture demonstrates plumbing, not performance.  The report marks the single-symbol sample as
below the 30-trade reference threshold and without out-of-sample validation.  Daily OHLCV cannot
support minute/Tick VSA or reliable intrabar volume distribution.  Missing, zero-volume, invalid
OHLC, adjustment, suspension, limit-state, and corporate-action issues need separate data audits
before any broader study.
