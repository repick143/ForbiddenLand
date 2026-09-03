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

项目的支持平台是 Ubuntu Linux。推荐在仓库根目录使用初始化脚本：

```bash
python scripts/bootstrap.py
```

脚本会创建 `.venv`、安装项目及 `akquant`、`akshare`、`easy-tdx`、DuckDB、Parquet、测试和格式化依赖，
并在 `full` profile 下进入 `frontend/` 使用锁文件执行 `npm ci`。初始化还会创建数据目录并执行导入、
编译、测试和 lint 基础检查。脚本必须由 Python 3.12.x 运行；如果当前解释器版本不符，会给出
切换解释器的提示，不会删除已有虚拟环境。

可按需要选择初始化 profile：

```bash
python scripts/bootstrap.py --profile core  # 仅项目和 AKQuant
python scripts/bootstrap.py --profile data  # 加入 AKShare、DuckDB 和 Parquet
python scripts/bootstrap.py --profile web   # 后端及其数据依赖（FastAPI、Uvicorn、AkShare、DuckDB）
python scripts/bootstrap.py --profile dev   # 加入测试和 Ruff
python scripts/bootstrap.py --profile full  # 以上全部内容，并初始化前端（默认）
```

只想复用现有的 pip、setuptools 和 wheel 时，可以加 `--skip-pip-upgrade`；需要跳过导入、
编译、测试和 lint 检查时，可以加 `--skip-checks`；只初始化 Python 环境时，可以加
`--skip-frontend`。缺少的项目依赖仍会按 profile 安装。前端初始化要求 Node `22.14.x` 和 npm；
存在 `frontend/package-lock.json` 时使用 `npm ci`，否则使用 `npm install`。

也可以手动安装：

```bash
python -m venv .venv
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev,data]"
cd frontend
npm ci
cd ..
```

Ubuntu 激活命令为：

```bash
source .venv/bin/activate
```

项目不会把账号、密钥或本地环境配置提交到 Git。如果当前 Ubuntu 架构没有 AKQuant 的匹配
预编译包，从源码安装时需要 Rust 工具链。

前端使用 Node `22.14.x`，版本记录在 `.node-version` 和 `frontend/package.json`；默认 `full`
初始化会安装 `frontend/node_modules`。精简 profile 不会安装前端依赖，之后可重新运行默认脚本，
或手动在 `frontend/` 执行 `npm ci`。

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

推荐从仓库根目录用一个命令启动前后端（Ubuntu）：

```bash
bash scripts/start.sh
```

启动脚本会检查 `.venv`、Node/npm 和前端依赖，然后调用 `scripts/dev.py` 启动或复用后端、启动
Vite，并把同一个 API host/port 注入两个进程，避免后端改端口后前端仍代理到旧地址。需要临时
使用其他端口时只改一个参数即可：

```bash
bash scripts/start.sh --api-port 9093
```

默认后端端口是 `9092`，前端端口是 `5173`。按 `Ctrl+C` 会一起停止由脚本创建的子进程。
如果需要直接使用 Python 编排器，也可以运行 `python scripts/dev.py`。
`scripts/start.sh` 会先根据脚本自身位置解析仓库根目录，再从任意工作目录以 `sh` 或 `bash`
调用都能正确启动。

## 功能记录

### 开发环境初始化与服务启动（已实现）

- 入口：`scripts/bootstrap.py` 初始化 Python、数据目录和（默认 `full` profile）前端 npm
  依赖；`scripts/start.sh` 校验本地环境后统一启动 FastAPI 与 Vite。
- 边界：前端仍只通过版本化 API 访问后端；启动器不读取行情数据、不修改 DuckDB/Parquet。
- 平台：Ubuntu Linux 是支持和验证目标，要求 Python `3.12.10`、Node `22.14.x`、npm；启动器
  可通过 Bash 或 POSIX sh 调用。
- 可选项：`--skip-frontend` 适用于只需要 Python 的初始化；`--api-port` 可为前后端同步指定
  非默认端口（默认后端 `9092`）。
- 验证：覆盖 Python 单测、Ruff、compileall、shell 语法检查、前端生产构建，以及临时端口的
  前后端健康检查；市场数据仍遵循远程默认和缓存策略。
- 限制：启动器不会自动下载依赖或启动生产构建；缺少环境时会给出初始化提示。

也可以分别启动服务（例如调试独立进程）：

启动后端（仓库根目录）：

```text
python -m forbiddenland.api.app
```

当前开发阶段后端默认开启 Uvicorn 自动重载，Python/API 修改会自动生效；前端使用 Vite HMR，
`frontend/src/` 下的修改会自动更新浏览器。只有修改 Vite 配置、依赖或环境变量时才需要重启
前端开发服务器。若需关闭后端重载，可设置 `FORBIDDENLAND_API_RELOAD=0`。

启动前端（另一个终端）：

```bash
cd frontend
npm ci
npm run dev
```

开发时前端运行在 `http://127.0.0.1:5173`，所有请求使用版本化的 `/api/v1` 前缀。Vite 会
从 `FORBIDDENLAND_API_PROXY_TARGET`（优先）或 `FORBIDDENLAND_API_PORT` 解析后端代理目标，
默认是 `http://127.0.0.1:9092`。分别启动时，后端和前端必须使用同一端口；后端监听非本机
地址时，前端应显式设置完整的 `FORBIDDENLAND_API_PROXY_TARGET`。
自选研究台默认使用本地日期的前一个月到今天，支持创建分组并添加个股、指数或同花顺概念，
每页可展示 4、6 或 9 张日线蜡烛图。分组和标的保存在当前浏览器的 `localStorage`，不会写入
DuckDB，也不会进入 Git。图表使用 `lightweight-charts`，页面保留 TradingView attribution。
顶栏的“量价方法” Tab 展示 [`frontend/src/content/volume_price_analysis.md`](frontend/src/content/volume_price_analysis.md)，
集中介绍 VSA、Wyckoff、VPA 和 Volume Profile，并明确当前本地数据只有日线的边界。

### 个股分析历史（已实现）

- 入口：`.venv/bin/python -m research.technical_analysis.run`；默认分析生益电子 `688183`
  和生益科技 `600183`。甬矽电子 `688362`、云南锗业 `002428`、晓程科技 `300139`、景旺电子
  `603228`、行云科技 `300209` 和超纯应材 `301717` 也已纳入目录，可用重复的 `--symbol`
  指定一只或多只。
- 存储：每个自然日一份轻量 JSON，路径为
  `analysis_history/<六位股票代码>/<YYYY-MM-DD>.json`。同一股票再次分析时，生成器会读取
  最近一份更早记录，比较期间最高/最低/收盘与上一份触发价、止损和目标，并把结果写入
  `review`；没有新交易日或首份记录也会明确标记。
- API：`GET /api/v1/analysis/history` 提供按股票、关键词、日期范围和数量筛选的列表；
  `GET /api/v1/analysis/history/{symbol}/{analysis_date}` 提供详情。前端“分析历史” Tab
  按个股分组、日期倒序展示，并可打开指标、形态、条件观察位、复盘、来源和验证警告。
- 边界：行情仍由 `AkShareMarketProvider` 的远程默认路径获取；记录保留实际来源、复权、
  抓取时间、缓存命中和数据窗口。技术分析是研究输出，不代表含成本回测或投资建议；损坏
  的历史文件会在列表响应中作为 warning 暴露，而不是静默忽略。

### easy-tdx 数据源 skill（已实现）

- 入口：[`skills/easy-tdx-data/SKILL.md`](skills/easy-tdx-data/SKILL.md)。skill 优先解析仓库
  `.venv/bin/easy-tdx` binary，按请求路由行情、分钟线、分时/逐笔、盘口、板块、公告、
  技术指标、因子、缠论、通达信公式、筛选、回测、DuckDB 仓库和 Web 能力；缺少 binary 时
  检查同一 Python runtime 并回退到该 runtime 的 `python -m easy_tdx`/Python API。
- 安装：在仓库根目录执行 `python scripts/bootstrap.py --profile data`，然后执行
  `.venv/bin/python -m pip install "easy-tdx==1.30.3"` 和 `.venv/bin/python -m pip check`。
  `easy-tdx` 是发行包名，`easy_tdx` 是导入名；包要求 `pandas>=2,<3`。Web、DuckDB 仓库、
  Spearman IC 分别按需安装 `easy-tdx[web]`、`easy-tdx[warehouse]`、`easy-tdx[science]`。
- 加载 skill：在仓库根目录执行 `python scripts/install_local_skills.py`，脚本会把
  `skills/easy-tdx-data` 链接到 `${CODEX_HOME:-$HOME/.codex}/skills/easy-tdx-data`，并拒绝覆盖
  已存在的同名路径。链接指向仓库源目录，因此后续修改 `SKILL.md` 或 `references/` 会自动
  反映到本地安装，不需要重复复制；切换仓库位置或修复链接时可重复执行脚本。某些 agent
  宿主使用 `$HOME/.agents/skills`，可显式指定 `--skill-home "$HOME/.agents/skills"`。
  创建链接后需重启 Codex（或新开 session）才能进入 skill 清单；完整命令见该目录的
  `README.md`。
- 数据边界：TDX/MAC 是公开行情协议，实时层是轮询而非交易所推送；skill 必须记录来源、
  主机、抓取时间、频率、复权、时间戳和成交量单位，不得静默把 TDX 数据写入 AkShare 快照，
  也不得把 easy-tdx 自带回测引擎当作 AKQuant 的替代品。
- VSA/研究：easy-tdx 提供分钟和逐笔输入、50 个指标、19 个因子及公式解析，但没有经验证
  的 VSA 策略；价值因子当前为占位实现，因子窗口按输入行数计算。完整限制和故障处理见
  `skills/easy-tdx-data/references/`。
- 订单流研究：`research/order_flow/` 使用 easy-tdx MAC `transaction` 的成交方向代理，
  配合 5 分钟/1 分钟/日线交叉对账、分页审计和可调参数运行独立回测；它不接入 AKQuant，
  也不把聚合成交记录当作完整 Level-2 委托事件。`transaction_alignment=auto` 会记录并保留
  实际采用的左/右端点映射；当前已验证 MAC 主机的分钟 K 线按右端点标记，合成 fixture 按左
  端点标记。报告同时保留 easy-tdx 原始指标和按交易日修正的分钟回测统计。模块还按
  easy-tdx `Factor`/`register_factor` 协议注册 `order_flow_delta_ratio`，并将每日
  `date`/`code` 长格式因子表及 provenance manifest 保存到 `reports/`；easy-tdx 的注册表本身
  是进程内的，不会跨进程自动发现项目模块。
- 验证：当前 macOS/Python `3.12.10` 环境已通过项目测试；Ubuntu 仍是项目发布验证目标。

后端提供 `/api/v1/health`、`/api/v1/market/securities`、`/api/v1/market/assets` 和
`/api/v1/market/bars`。统一资产接口使用 `asset_type=stock|index|concept`；本地模式支持个股和
已核验的同花顺概念行情，常见宽基指数目录可以搜索，但指数历史行情目前需要远端 AkShare。
OpenAPI 契约可用 `python scripts/export_openapi.py` 更新到
`contracts/openapi.json`。当前本地验证统一使用开发服务器和 HMR；`npm run build` 仅作为后续
发布流程使用，仍通过同一 API 契约访问后端。

## 研究方向

研究代码按独立方向放在 `research/` 下。当前的 [`short_term/`](research/short_term/) demo
默认通过项目的 `AkShareMarketProvider` 获取远程 AkShare 数据，再交给 AKQuant 回测；该路径
包含有限重试和明确的 Tencent 历史端点备选，并把实际来源写入报告。现有本地 Parquet/DuckDB
快照尚未作为回测输入，需要完成复核并明确批准后再接入。离线测试可显式使用 demo 自带的合成
fixture。另有 [`vsa/`](research/vsa/) 日线 VSA demo，默认分析生益电子 `688183`（不是项目
标准夹具中的生益科技 `600183`），按“特征 -> 候选 -> 下一根确认 -> AKQuant”生成指标和回测
报告；其远程数据同样经 provider 获取，合成 fixture 仅用于离线验证。

### 日线 VSA 指标与回测（已实现）

- 入口：`.venv/bin/python -m research.vsa.run`；`--source fixture` 提供不依赖网络的合成
  演示，默认 remote 路径请求生益电子 `688183` 的日线数据。
- 模块：`research/vsa/features.py` 负责前置滚动基线、Spread/CLV/量比和数据质量；
  `research/vsa/rules.py` 负责五类候选及后一根确认/失效；`research/vsa/strategy.py` 通过
  `Bar.extra` 接入 AKQuant，并记录指标点；`research/vsa/run.py` 负责 provider、回测和 JSON
  报告。
- 执行边界：`NextOpen()`、100 股整手、佣金/印花税/过户费/滑点和 `t_plus_one=True` 均显式
  配置；新买入仓位在次日可卖时才挂真实止损，目标价按日 K 线高点观察后次开盘退出。
- 数据与验证：报告保留来源、后端、存储、复权和抓取时间，保存参数版本及候选证据；单标的
  合成样本低于 30 笔参考交易且没有样本外验证，不代表策略存在统计优势。详细限制和命令见
  [`research/vsa/README.md`](research/vsa/README.md)。

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
FORBIDDENLAND_REMOTE_CACHE_ENABLED=1      # 默认开启远程历史日线缓存
FORBIDDENLAND_REMOTE_CACHE_TTL_SECONDS=86400
FORBIDDENLAND_REMOTE_CACHE_DIR=data/cache/akshare
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

远程历史日线默认使用 provider 层缓存。AkShare 的 `stock_zh_a_hist` 并不会替项目提供可控的
持久化缓存，因此项目把已经校验并标准化的 OHLCV 响应以 UTF-8 JSON 原子写入
`data/cache/akshare/`；缓存不是本地数据后端，也不改变 `backend=remote`。缓存键包含资产类型、
标准化代码或概念、日期区间、复权方式、周期和实际 endpoint，避免不同请求互相污染。有效命中
不会访问网络，响应的 `provenance.source` 和原始 `retrieved_at_utc` 保持不变，并将
`provenance.cache_hit` 设为 `true`，`storage` 会标明 `cache hit`。默认有效期为 24 小时；过期、
损坏或时间戳在未来的文件会被忽略并重新请求远端。远端请求失败时不会使用过期缓存。可通过
`FORBIDDENLAND_REMOTE_CACHE_ENABLED=0` 关闭，或调整 TTL/目录；`FORBIDDENLAND_AKSHARE_CACHE_*`
是对应的兼容变量名。默认缓存目录和行情 payload 均被 Git 忽略；自定义缓存目录也不应纳入 Git。
缓存只用于减少重复读取，不是版本化回测快照；需要可复现的正式回测时仍应保存并审核固定的
数据版本。

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
├── scripts/              # 环境初始化和 Ubuntu 启动入口
├── frontend/             # React/Vite/TypeScript 前端
├── contracts/            # OpenAPI 等轻量接口契约
├── research/             # 独立研究方向
├── analysis_history/     # 按个股/分析日保存的轻量复盘记录
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
