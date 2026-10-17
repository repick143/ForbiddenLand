# Stock Trend Prediction Research

This directory is a placeholder for stock price and securities-trend prediction research. It is
created as an independent research direction following the layout used by `research/short_term/`,
`research/vsa/`, and `research/order_flow/`.

No implementation exists yet. Before adding a runnable experiment, decide the prediction target,
feature source, evaluation window, and backtest boundary, then record them here. Keep the project
constraints from `AGENTS.md`:

For the frontier-research overview that this direction starts from, see
[`frontier_overview.md`](frontier_overview.md). It covers the 2024-2026 method landscape
(time-series foundation models, graph neural networks, LLM agents, multimodal and generative
approaches) and the evaluation pitfalls that constrain any runnable experiment here.

For the first planned experiment (a next-day direction baseline with strict A-share execution
rules), see [`baseline_plan.md`](baseline_plan.md). It currently contains the approach and open
decision points only; implementation details come after the approach is confirmed.

For a from-scratch introduction to the baseline model family (what XGBoost is, which APIs matter,
the end-to-end training workflow, and the problems that must be solved before it can be used on
stock data), see [`xgboost-intro.md`](xgboost-intro.md).

For paper reading notes, see [`kit_2609.34507_notes.md`](kit_2609.34507_notes.md) (KiT, a
diffusion-Transformer financial time-series foundation model) and
[`frontier_overview.md`](frontier_overview.md) for the wider 2024-2026 landscape.

The distilled, source-cited knowledge base for stock-model prediction lives under
[`kb/`](kb/README.md). Add the most condensed, traceable conclusions there when researching.

- Data acquisition goes through the configured provider path (remote AkShare by default); do not
  read local Parquet/DuckDB snapshots until they are revalidated and explicitly approved.
- Keep provider adapters separate from strategy, feature, and evaluation logic.
- Preserve raw source values and report provenance (source, observation date, adjustment mode,
  retrieval time) instead of silently correcting or filling data.
- Outputs are research results, not investment advice. A prediction is an estimate with explicit
  data-quality and validation boundaries, not a calibrated probability.

The intended run pattern follows the other research directions:

```bash
python -m research.predict.<module>
```

Run the project checks before treating any experiment as verified:

```bash
python -m pytest
ruff format --check .
ruff check .
python -m compileall -q src tests scripts research
git diff --check
```
