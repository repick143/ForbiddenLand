# Prediction Baseline（实现包）

> 状态：已实现并跑通（2026-10-19）。思路与决策点见 [`../baseline_plan.md`](../baseline_plan.md)，运行结果与链路见 [`../README.md`](../README.md)。

## 当前实现部分

- **目标**：次日 open→close 方向二分类（XGBoost）；信号 T 日收盘后产生、T+1 开盘成交（NextOpen），AKQuant 按 A 股规则严格回测。
- **数据获取**：原始取数与归一化在 `src/ecom_data`；默认 `easy-tdx` source（easy-tdx Python 库
  MacClient + SQLite 缓存 `data/cache/ecom_data.db`），可选 `akshare` source（包装
  `AkShareMarketProvider`，含有限重试、腾讯兜底、`data/cache/akshare` 远程响应缓存）；本目录
  `data.py` 只做日期窗口与面板映射，并保留 source 溯源。
- **特征**：14 个因果量价特征（`features.py`），只用 T 日收盘及之前信息。
- **标签**：`labels.py` — `sign(open_{T+1} -> close_{T+1})`，剔除不可成交样本并记录原因。
- **训练**：`train.py` — 共享数据准备 `build_dataset`（取数→特征→标签→时间顺序切分）、XGBoost
  原生 Booster 验证集早停、验证集选阈值（Youden J）、AUC/RankIC/朴素基线评估；CLI 持久化模型
  并写训练报告；支持 `--symbols-file` 从文件读取训练股票池，报告 `timing` 段记录取数/因子/标签/
  切分/训练/总计耗时（秒）。
- **预测**：`predict.py` — CLI 加载训练产物，对 `--valid-end` 之后的窗口评分、写信号表、AKQuant
  回测，报告评分窗指标与逐符号最新一 bar 的方向信号。
- **回测**：`backtest.py` — AKQuant 信号查表策略，NextOpen、T+1、佣金/印花税/过户费/滑点。
- **编排**：`run.py` — 一次跑通（训练 + 回测合并报告）。
- **模型文件**：`model_cache.py` — 模型文件（`.ubj`/`.json`）内嵌训练配置指纹与验证阈值；
  训练 CLI 用 `--model-cache` 持久化（配置一致时可复用，否则重训覆盖），预测 CLI 用
  `load_model_file` 加载（指纹写入报告溯源，不强制一致以便跨股票池推理）。

## 待扩展部分

- **特征**：资金流/情绪/基本面、成交额/换手率、位置状态类指标（RSI、布林带位置、距 N 日高低点），做加/不加对照实验。
- **股票池**：扩展到更大截面（当前 3 只），上截面排序/多空组合以累积弱信号。
- **目标**：形态 B——幅度点估计 + 可信区间（分位数回归 / Conformal 兜底覆盖率）。
- **评估**：多折 walk-forward、置换检验、bootstrap 置信区间。
- **工程**：滚动重训调度与模型版本注册表（文件级缓存 + 训练/预测拆分已实现）；接入 easy-tdx
  订单流/分钟线等多源数据。

## 直接执行命令

### 1. 训练（生成模型 + 训练报告）

默认股票池 `688256/688072/600183`，训练至 2023-12-31、验证 2024-2025、测试 2026：

```bash
./.venv/bin/python -m research.predict.baseline.train
```

指定 A 股代码训练（6 位代码，SH/SZ/BJ 自动识别）：

```bash
./.venv/bin/python -m research.predict.baseline.train --symbols 600519 000858 300750
```

从文件读取训练股票池（每行一个 6 位代码，`#` 开头为注释；与 `--symbols` 二选一）：

```bash
./.venv/bin/python -m research.predict.baseline.train \
  --symbols-file research/predict/universe/symbols_hs300_star50.txt
```

`research/predict/universe/symbols_hs300_star50.txt` 为沪深300（`000300`）+ 科创50（`000688`）
成分股并集快照，共 330 只（as-of 2026-10-16，来源 csindex 官网经 AkShare），文件头含来源与
快照日期；清单的维护与重新生成见 [`../universe/README.md`](../universe/README.md)。

训练参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `--symbols` | 6 位 A 股代码列表（空格分隔） | `688256 688072 600183` |
| `--symbols-file` | 股票池文件路径（与 `--symbols` 二选一） | 关闭 |
| `--start-date` / `--end-date` | 数据窗口（`YYYYMMDD`） | `20190101` / `20261016` |
| `--adjust` | 复权：`qfq` / `hfq` / `none` | `qfq` |
| `--source` | ecom-data 数据源：`easy-tdx`（默认，快）或 `akshare`（远程 AkShare，带磁盘缓存） | `easy-tdx` |
| `--bar-count` | 每只股票请求的最大 bar 数（easy-tdx QFQ 建议 2500，跳过深层历史重算；akshare 忽略） | `2500` |
| `--train-end` / `--valid-end` | 训练/验证截止日（测试期 = valid-end 之后） | `20231231` / `20251231` |
| `--seed` | 随机种子 | `42` |
| `--threshold` | 覆盖验证集自动选择的分类阈值 | 自动 |
| `--model-cache` | **输出**模型文件路径（`PATH.ubj`/`.json`）；不传则不落盘 | 关闭 |
| `--report` | 训练报告输出路径 | `reports/predict_baseline_train.json` |

训练报告包含数据溯源、切分行数、train/valid/test 指标、模型指纹与分阶段耗时（`timing`）；
`--model-cache` 已存在且指纹一致时直接复用（`mode=loaded`），否则重训并覆盖（`mode=saved`）。
数据源与 `--bar-count` 已纳入指纹，换源或换 bar 数会触发重训。

单只标的无数据或拉取失败时**跳过并保留其余部分**：`data.py` 将原因记入 `batch.skipped`，训练/
预测报告输出 `skipped` 段（例如 `601059` 在 TDX 无日线）；仅当全部标的都失败时才报错中断。

**数据源**：默认 `easy-tdx`（easy-tdx Python 库，约 2s/只，330 只约 11 分钟；响应写入
`data/cache/ecom_data.db` SQLite 缓存，复跑直接命中，不重复连 TDX）。QFQ 全量历史会触发
easy-tdx 深层历史本地重算（老票 2005 年前后负价），默认 `--bar-count 2500` 只取近 10 年，覆盖
2019 起的训练窗口并跳过大部分重算。需要远程 AkShare 时显式 `--source akshare`：

```bash
./.venv/bin/python -m research.predict.baseline.train \
  --symbols-file research/predict/universe/symbols_hs300_star50.txt \
  --model-cache data/cache/predict_model_hs300_star50.ubj
```

### 2. 预测（加载模型 + 评分 + 回测）

```bash
./.venv/bin/python -m research.predict.baseline.predict \
  --model-cache data/cache/predict_model.ubj
```

指定 A 股代码做回测：

```bash
./.venv/bin/python -m research.predict.baseline.predict \
  --symbols 600519 000858 300750 \
  --model-cache data/cache/predict_model.ubj
```

预测参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `--symbols` | 6 位 A 股代码列表；可不同于训练股票池（推理不强制指纹一致） | `688256 688072 600183` |
| `--start-date` / `--end-date` | 数据窗口（`YYYYMMDD`） | `20190101` / `20261016` |
| `--adjust` | 复权：`qfq` / `hfq` / `none` | `qfq` |
| `--valid-end` | 评分/回测窗口起点（该日之后） | `20251231` |
| `--model-cache` | **必填**：训练 CLI 生成的模型文件 | - |
| `--threshold` | 覆盖模型内嵌阈值；无内嵌时默认 `0.50` | 模型内嵌 |
| `--signals` / `--report` | 信号表与预测报告输出路径 | `data/processed/predict_signals.csv` / `reports/predict_baseline_predict.json` |

预测报告包含评分窗指标、逐符号最新一 bar 的 `prob`/方向信号（`latest`）、AKQuant 回测与同窗
Buy & Hold 对比；`latest` 的 `signal` 是“T+1 开盘”的方向提示，实际可成交性（涨跌停、缺 bar）需
当日复核。

### 3. 一次跑通（训练 + 回测合并报告）

```bash
./.venv/bin/python -m research.predict.baseline.run
./.venv/bin/python -m research.predict.baseline.run --symbols 600519 000858 300750
```

`run.py` 参数与训练 CLI 相同，另支持可选 `--model-cache`（复用缓存模型）与 `--signals`，输出单一
合并报告 `reports/predict_baseline.json`。

结果判读：测试期 AUC 是否 >0.5、RankIC 是否显著为正、回测是否显著跑赢同窗 Buy & Hold（详见
[`../kb/no-signal-evaluation.md`](../kb/no-signal-evaluation.md)）。

示例：只看贵州茅台、2020 年以来的窗口：

```bash
./.venv/bin/python -m research.predict.baseline.run \
  --symbols 600519 \
  --start-date 20200101 --end-date 20261016 \
  --train-end 20241231 --valid-end 20251231
```

示例：先训练并保存模型，再单独做预测/回测（模型建议放在被 gitignore 的 `data/cache/` 下）：

```bash
./.venv/bin/python -m research.predict.baseline.train \
  --model-cache data/cache/predict_model.ubj
./.venv/bin/python -m research.predict.baseline.predict \
  --model-cache data/cache/predict_model.ubj
```
