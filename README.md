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
python scripts/bootstrap.py --profile web   # 后端及其数据依赖（FastAPI、Uvicorn、AkShare、DuckDB）
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

前端使用 Node `22.14.x`，版本记录在 `.node-version` 和 `frontend/package.json`。初始化 Python
环境不会自动安装 Node 依赖；进入 `frontend/` 后执行 `npm install`。

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

## 前后端服务边界

项目采用独立的 Python 后端服务和 React 前端项目：

```text
浏览器 frontend/ -> FastAPI /api/v1 -> application 用例 -> provider/storage/AKQuant
                                                     -> DuckDB、Parquet 或远程 AkShare
```

前端只负责页面、交互、格式化和图表渲染，不直接打开 DuckDB/Parquet，也不直接调用 AkShare
或 AKQuant。后端 API 是数据访问、计算、数据来源和回测状态的唯一入口。

启动后端（仓库根目录）：

```text
python -m forbiddenland.api.app
```

启动前端（另一个终端）：

```text
cd frontend
npm install
npm run dev
```

开发时前端运行在 `http://127.0.0.1:5173`，`/api` 请求由 Vite 转发到
`http://127.0.0.1:9092`。后端默认监听 `9092` 端口（可用 `FORBIDDENLAND_API_PORT` 覆盖）。
自选研究台默认使用本地日期的前一个月到今天，支持创建分组并添加个股、指数或同花顺概念，
每页可展示 4、6 或 9 张走势图。分组和标的保存在当前浏览器的 `localStorage`，不会写入
DuckDB，也不会进入 Git。图表使用 `lightweight-charts`，页面保留 TradingView attribution。

后端提供 `/api/v1/health`、`/api/v1/market/securities`、`/api/v1/market/assets` 和
`/api/v1/market/bars`。统一资产接口使用 `asset_type=stock|index|concept`；本地模式支持个股和
已核验的同花顺概念行情，常见宽基指数目录可以搜索，但指数历史行情目前需要远端 AkShare。
OpenAPI 契约可用 `python scripts/export_openapi.py` 更新到
`contracts/openapi.json`。生产前端使用 `npm run build` 生成静态资源，仍通过同一 API 契约访问
后端。

## 研究方向

研究代码按独立方向放在 `research/` 下。当前的 [`short_term/`](research/short_term/) demo
默认通过 AKQuant 使用远程 AkShare 数据；现有本地 Parquet/DuckDB 快照尚未作为回测输入，
需要完成复核并明确批准后再接入。离线测试可显式使用 demo 自带的合成 fixture。

## AkShare 本地/远程切换

项目提供了 AkShare 兼容入口。业务代码首次接入时使用：

```python
from forbiddenland.integrations.akshare_compat import ak

daily = ak.stock_zh_a_hist(
    symbol="000001",
    period="daily",
    start_date="20240101",
    end_date="20241231",
    adjust="",
)
```

数据后端通过环境变量选择，调用代码不需要随之修改：

```text
FORBIDDENLAND_MARKET_BACKEND=remote      # 默认：调用真实 AkShare
FORBIDDENLAND_MARKET_BACKEND=local       # 用户复核后显式读取 data/raw/
FORBIDDENLAND_MARKET_BACKEND=hybrid      # 本地实现优先，回源需另行显式允许
FORBIDDENLAND_ALLOW_REMOTE_FALLBACK=0    # hybrid 默认不隐式回源
FORBIDDENLAND_REMOTE_RETRY_ATTEMPTS=3    # 瞬时连接错误的总尝试次数
FORBIDDENLAND_REMOTE_RETRY_BACKOFF_SECONDS=0.5
FORBIDDENLAND_REMOTE_REQUEST_TIMEOUT_SECONDS=15
FORBIDDENLAND_REMOTE_ALTERNATE_SOURCE=1  # 主端点不可用时允许 Tencent AkShare 端点
FORBIDDENLAND_DATA_ROOT=data
FORBIDDENLAND_THS_CONCEPT_CATALOG_FILE=...  # 可选：覆盖同花顺概念目录
FORBIDDENLAND_THS_CONCEPT_MEMBERS_FILE=...  # 可选：覆盖同花顺概念成分汇总
FORBIDDENLAND_THS_SECTOR_QUOTES_FILE=...    # 可选：覆盖同花顺板块行情 ZIP
```

本地支持 `stock_zh_a_hist`（日线、周线、月线）、`stock_info_a_code_name`，以及以下四个
AKShare 同花顺概念接口：

- `stock_board_concept_name_ths`
- `stock_board_concept_info_ths`
- `stock_board_concept_index_ths`
- `stock_board_concept_summary_ths`

`stock_zh_a_hist_tx` 是仅远程可用的 Tencent 历史端点，主要作为主历史端点连接失败时的备选，
不会读取本地快照。

同花顺本地概念范围限定为已核验的 A 股 `885/886` 概念指数。调用详情和行情时既可传概念名称，
也可传本地 `六位代码.TI`；本地 `.TI` 指数代码与远程网页接口返回的页面代码不是同一命名空间，
不能直接互换。行情快照没有 `成交额`，简介快照也不能提供成交量单位换算、涨幅排名、涨跌家数、
资金净流入和成交额，这些字段返回缺失值而不是零。概念时间表是用目录 `上市日期`、概念名称和
成分明细实际计数构造的快照近似；`驱动事件`、`龙头股` 返回缺失值，不代表源端不存在。

在本地快照完成复核并获明确批准前，默认 backend 是 `remote`；需要读取本地文件时必须显式设置
`FORBIDDENLAND_MARKET_BACKEND=local`。
`adjust=""` 使用快照中的原始价格；`qfq`/`hfq` 使用 `adj_factor` 计算。没有本地对应数据的
接口在 `local` 模式会明确报错；只有在 `hybrid` 且显式设置
`FORBIDDENLAND_ALLOW_REMOTE_FALLBACK=1` 时才允许回源。日线快照不是实时行情，不能用来冒充
`stock_zh_a_spot_em`。

远程个股历史请求对连接中断和超时执行有限次数的指数退避。若 AkShare 当前主端点持续不可用，
且 `FORBIDDENLAND_REMOTE_ALTERNATE_SOURCE` 未关闭，provider 会改用 AkShare 的
`stock_zh_a_hist_tx`（腾讯）端点；返回结果的 `provenance.source` 和 `storage` 会明确标记
`Tencent historical fallback`。参数错误、响应格式错误和数据质量错误不会重试，也不会使用旧缓存
或未经复核的本地快照替代。若两个远程端点都失败，API 会返回 `502` 及两端点的错误上下文。

已有必须保留 `import akshare as ak` 的脚本，可以在启动阶段调用一次。默认仍是远程 backend；
只有在本地快照完成复核并获批准后，才设置 `FORBIDDENLAND_MARKET_BACKEND=local` 再调用：

```python
from forbiddenland.integrations.akshare_compat import install_local_backend

install_local_backend()
import akshare as ak
```

之后仍通过上述环境变量切换后端。数据文件本身继续放在 `data/raw/`，并被 Git 忽略。

## 常用检查

```bash
python -m pytest
python -m ruff format --check .
python -m ruff check .
```

只运行兼容层单测：

```bash
python -m pytest tests/test_akshare_compat.py -q
```

## 目录约定

```text
.
├── src/forbiddenland/   # Python 源码
├── frontend/             # React/Vite/TypeScript 前端
├── contracts/            # OpenAPI 等轻量接口契约
├── research/             # 独立研究方向
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
