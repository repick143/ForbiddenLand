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

## 实现状态与链路（2026-10-19）

首个可运行版本已实现，入口：

```bash
./.venv/bin/python -m research.predict.run
```

链路：远程 AkShare（项目 provider）→ 因果特征 → 次日 open→close 标签与可成交性过滤 →
XGBoost walk-forward 训练 → 预计算信号 → AKQuant 回测。

模块与职责：

- `data.py`：经 `AkShareMarketProvider` 拉取远程日线，保留 source/storage/retrieved_at 溯源；
  默认股票池 `688256`/`688072`/`600183`。
- `features.py`：14 个因果量价特征（gap、ret_1/5/10/20、均线乖离、vol_20、量比、涨跌停标志等），
  只用 T 日收盘及之前信息；涨跌停比例按板块近似（10%/20%）。
- `labels.py`：标签 `sign(open_{T+1} -> close_{T+1})`；剔除不可成交样本并记录原因
  （`t_close_limit_up`/`next_open_limit_up`/`no_next_bar`）。
- `train.py`：时间顺序 train/valid/test 切分；XGBoost 原生 Booster + DMatrix，验证集早停；
  验证集选阈值（Youden J）；AUC/RankIC/朴素基线评估。
- `backtest.py`：AKQuant 信号查表策略，NextOpen 成交、T+1、佣金/印花税/过户费/滑点与
  `research/short_term/demo.py` 一致。
- `run.py`：CLI 编排，写 `reports/predict_baseline.json` 与 `data/processed/predict_signals.csv`
  （均不提交 Git）。

约束与红线：默认远程数据（本次运行实际走了 Tencent historical fallback，provenance 已记录）；
不读本地未验证快照；新增依赖 `xgboost`（`pyproject.toml` 的 `research` 组）。

验证：新增 16 个确定性测试覆盖特征因果/涨跌停、标签口径与排除、切分/训练/评估、信号回测、
数据归一化；全量 `pytest` 224 passed；ruff/compileall/diff 检查通过。

首次真实运行（2026-10-19，训练至 2023-12-31、验证 2024-2025、测试 2026-01~10）结果诚实且符合
预期：测试 AUC≈0.476、RankIC≈-0.006、准确率≈50.4%，**未发现可交易信号**；回测 +45.7% 接近
同窗等权 Buy & Hold（约 +48%），属行情漂移而非 alpha。这印证知识库"别指望高准确率、弱信号 +
严格回测"的定位；后续提升方向是特征/截面/多源数据，而不是换更强的模型。

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
