# ForbiddenLand

一个面向个人研究的 A 股股票分析工具，使用 Python 实现。

项目以 AKQuant 作为核心量化研究与回测引擎，以 AKShare 获取公开数据，并默认使用 DuckDB
作为存储和查询层、Parquet 作为可复现的数据快照格式。项目会优先保证数据来源、计算过程
和结论可以复核，再逐步增加自动化能力。

## 目标

- 统一获取并整理 A 股行情、财务和交易日数据。
- 将基本面、技术面和估值指标拆分为可测试的分析模块。
- 使用 AKQuant 执行策略回测、组合管理和风险检查。
- 支持按时间保存数据快照，避免用最新数据悄悄改写历史结论。
- 生成适合个人复盘的结构化报告，并为后续回测保留接口。

## 计划中的分析维度

1. 基本面：收入、利润、现金流、盈利能力和资产负债情况。
2. 行情与技术面：趋势、成交量、波动率、均线和相对强弱。
3. 估值与风险：估值区间、行业对比、回撤、流动性和数据质量。

指标和评分标准会在实现对应模块时单独记录，避免把尚未验证的规则写成既定结论。

## 环境准备

仓库通过 `.python-version` 固定本地开发版本为 Python `3.12.10`。该版本由 pyenv 管理，
不会修改全局 pyenv 设置；项目要求 Python `3.12.x`。

进入仓库后可以确认实际版本：

```bash
pyenv version
python --version
```

推荐使用初始化脚本（macOS 和 Windows 均可）：

```bash
python scripts/bootstrap.py
```

脚本会创建 `.venv`、安装项目及 `akquant`、`akshare`、DuckDB、Parquet、测试和格式化依赖，
并执行导入和基础检查。脚本必须由 Python 3.12.x 运行；如果当前解释器版本不符，会给出
切换解释器的提示，不会删除已有虚拟环境。

可按需要选择初始化 profile：

```bash
python scripts/bootstrap.py --profile core  # 仅项目和 AKQuant
python scripts/bootstrap.py --profile data  # 加入 AKShare、DuckDB 和 Parquet
python scripts/bootstrap.py --profile dev   # 加入测试和 Ruff
python scripts/bootstrap.py --profile full  # 以上全部内容（默认）
```

只想复用现有的 pip、setuptools 和 wheel 时，可以加 `--skip-pip-upgrade`；需要跳过导入、
编译、测试和 lint 检查时，可以加 `--skip-checks`。缺少的项目依赖仍会按 profile 安装。
脚本使用 Python 标准库实现，不依赖 Bash、Zsh 或 Windows 专有命令。

也可以手动安装：

```bash
python -m venv .venv
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev,data]"
```

Windows PowerShell 的激活命令为：

```powershell
.venv\Scripts\Activate.ps1
```

macOS 激活命令为：

```bash
source .venv/bin/activate
```

Windows 命令提示符也可以使用：

```bat
.venv\Scripts\activate.bat
```

项目不会把账号、密钥或本地环境配置提交到 Git。AKQuant 从 PyPI 提供 macOS 和 Windows 的
预编译包；如果运行在没有匹配 wheel 的架构上，从源码安装时需要 Rust 工具链。

## 组件边界

```text
AKShare -> Parquet 原始快照 -> DuckDB 存储/查询 -> 标准化数据
                                      |
                                      v
                         AKQuant 策略与回测
                                      |
                                      v
                              复盘报告与指标
```

回测不得在运行过程中临时请求实时数据。应先保存数据源、抓取时间、复权方式和数据版本，
再使用固定快照执行回测。

## 常用检查

```bash
python -m pytest
python -m ruff format --check .
python -m ruff check .
```

## 目录约定

```text
.
├── src/forbiddenland/   # Python 源码
├── tests/                # 自动化测试
├── data/raw/             # 原始数据，仅保存在本地
├── data/processed/       # 清洗后的本地数据
└── reports/              # 生成的分析报告
```

`data/` 和 `reports/` 下的运行产物默认被忽略。需要复现分析时，应记录数据来源、抓取时间、参数和代码版本；适合长期维护的小型样例可以单独放入测试夹具并明确标注来源。

## 开发约定

- 每个数据源和计算步骤都保留清晰的输入、输出与异常信息。
- 网络数据可能缺失、延迟或发生修订，报告中应区分“无数据”和“计算结果为零”。
- 先补测试，再调整指标或评分规则；涉及历史数据的变更应说明影响范围。
- `main` 用于可运行的主线版本，功能开发使用短分支并提交小而明确的变更。

## 风险声明

本项目仅用于个人学习、研究和复盘，不构成投资建议。数据的完整性、及时性和授权范围需要由使用者自行确认；任何分析结果都不应替代独立判断或风险控制。
