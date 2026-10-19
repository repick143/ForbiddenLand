# Prediction Baseline（实现包）

> 状态：已实现并跑通（2026-10-19）。思路与决策点见 [`../baseline_plan.md`](../baseline_plan.md)，运行结果与链路见 [`../README.md`](../README.md)。

## 当前实现部分

- **目标**：次日 open→close 方向二分类（XGBoost）；信号 T 日收盘后产生、T+1 开盘成交（NextOpen），AKQuant 按 A 股规则严格回测。
- **数据获取**：原始取数与归一化在 `src/ecom_data`（`akshare` source 包装 `AkShareMarketProvider`，含有限重试、腾讯兜底、远程响应缓存）；本目录 `data.py` 只做日期窗口与面板映射，并保留 provider provenance。
- **特征**：14 个因果量价特征（`features.py`），只用 T 日收盘及之前信息。
- **标签**：`labels.py` — `sign(open_{T+1} -> close_{T+1})`，剔除不可成交样本并记录原因。
- **训练**：`train.py` — 时间顺序 train/valid/test 切分、XGBoost 原生 Booster 验证集早停、验证集选阈值（Youden J）、AUC/RankIC/朴素基线评估。
- **回测**：`backtest.py` — AKQuant 信号查表策略，NextOpen、T+1、佣金/印花税/过户费/滑点。
- **编排**：`run.py` — 端到端 CLI。

## 待扩展部分

- **特征**：资金流/情绪/基本面、成交额/换手率、位置状态类指标（RSI、布林带位置、距 N 日高低点），做加/不加对照实验。
- **股票池**：扩展到更大截面（当前 3 只），上截面排序/多空组合以累积弱信号。
- **目标**：形态 B——幅度点估计 + 可信区间（分位数回归 / Conformal 兜底覆盖率）。
- **评估**：多折 walk-forward、置换检验、bootstrap 置信区间。
- **工程**：滚动重训与模型版本管理；接入 easy-tdx 订单流/分钟线等多源数据。

## 直接执行命令

默认训练 + 回测（股票池 `688256/688072/600183`，训练至 2023-12-31、验证 2024-2025、测试 2026）：

```bash
./.venv/bin/python -m research.predict.baseline.run
```

指定 A 股代码做训练 + 回测（6 位代码，SH/SZ/BJ 自动识别）：

```bash
./.venv/bin/python -m research.predict.baseline.run --symbols 600519 000858 300750
```

常用参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `--symbols` | 6 位 A 股代码列表（空格分隔） | `688256 688072 600183` |
| `--start-date` / `--end-date` | 数据窗口（`YYYYMMDD`） | `20190101` / `20261016` |
| `--adjust` | 复权：`qfq` / `hfq` / `none` | `qfq` |
| `--train-end` / `--valid-end` | 训练/验证截止日（测试期 = valid-end 之后） | `20231231` / `20251231` |
| `--seed` | 随机种子 | `42` |
| `--threshold` | 覆盖验证集自动选择的分类阈值 | 自动 |
| `--report` / `--signals` | 报告与信号表输出路径 | `reports/predict_baseline.json` / `data/processed/predict_signals.csv` |

一次运行完成：取数 → 特征 → 标签 → 切分 → 训练 → 评估 → 生成信号 → AKQuant 回测 → 写 JSON 报告。结果判读：测试期 AUC 是否 >0.5、RankIC 是否显著为正、回测是否显著跑赢同窗 Buy & Hold（详见 [`../kb/no-signal-evaluation.md`](../kb/no-signal-evaluation.md)）。

示例：只看贵州茅台、2020 年以来的窗口：

```bash
./.venv/bin/python -m research.predict.baseline.run \
  --symbols 600519 \
  --start-date 20200101 --end-date 20261016 \
  --train-end 20241231 --valid-end 20251231
```
