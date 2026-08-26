# 数据目录

这里管理本地和外部数据的约定。大型数据文件不进入 Git；仓库只保存获取/转换代码、
Schema、清单、校验和以及其他可复现分析所需的轻量元数据。

## 目录职责

- `raw/`：从数据源下载的原始快照，尽量保持来源字节和原始字段不变。
- `processed/`：清洗、标准化或分析过程中生成的本地数据集；默认通过 DuckDB 存储和查询，
  Parquet 用于快照、交换或导出。
- `cache/`：可以从原始数据重新构建的临时缓存，不作为唯一数据来源。

这些目录中的 Parquet、DuckDB 和其他大型二进制文件均被 `.gitignore` 忽略。需要共享或长期保存时，
把文件放到版本化的对象存储、数据集平台或发布附件中，并在仓库中记录地址、版本、抓取时间、
Schema 和 SHA-256；不要把文件直接塞进 Git 历史。

## 当前快照

`raw/stock_basic_data.parquet` 是一份 A 股证券基础资料快照（2026-08-26 检查）：5,892 行、16 列。
它包含 `ts_code`、`symbol`、`name`、`area`、`industry`、`market`、`exchange`、上市日期以及实际控制人等字段。
`fullname`、`enname`、`curr_type`、`list_status`、`delist_date` 和 `is_hs` 在该快照中全部为空。
股票代码必须按字符串处理，以保留前导零。

该快照的质量提示：`exchange` 全部为空，`market` 有 2 行为空；`301688.SZ` 和 `301697.SZ` 的
`list_date` 为 `1970-01-01`，应视为待核查的占位值，不能直接用于上市年限等计算。
`ts_code` 和 `symbol` 均非空且各自唯一；空值不得被默认为零或有效分类。

`raw/stock_daily.parquet` 是日线行情快照（2026-08-26 检查）：约 1,457 万行、33 列，覆盖
2009-01-05 至 2026-08-21，包含 5,826 个 `ts_code` 和 4,284 个交易日。每个
`(ts_code, trade_date)` 组合当前无重复。它包含未复权 OHLCV、估值/市值字段和累计
`adj_factor`；
没有分钟行情，也不能当作实时行情。兼容层将后复权计算为 `价格 * adj_factor`，前复权计算为
`价格 * adj_factor / 最新因子`；这套公式依赖当前快照的因子约定，变更前必须做契约测试。
查询时应使用 DuckDB 的股票代码和日期条件，避免一次性
将整个文件读入 pandas。

DuckDB 是本项目默认的本地存储层。检查或构建分析副本时，可以直接读取 Parquet 原始文件：

```sql
SELECT *
FROM read_parquet('data/raw/stock_basic_data.parquet')
LIMIT 20;

CREATE OR REPLACE TABLE stock_basic AS
SELECT *
FROM read_parquet('data/raw/stock_basic_data.parquet');
```

后续快照建议使用 `<source>_<dataset>_<as-of-date>.parquet` 命名；如果源数据没有明确日期，
应在清单中记录抓取时间和数据有效期，而不是凭文件名推断。

## AkShare 兼容读取

`forbiddenland.integrations.akshare_compat` 提供 AkShare 风格的本地读取入口。由于本地快照仍待
用户复核，兼容层默认使用远程 AkShare；只有显式设置
`FORBIDDENLAND_MARKET_BACKEND=local` 后才会读取这些文件。首批支持：

- `stock_zh_a_hist`：从 `raw/stock_daily.parquet` 查询日线，并可由日线聚合周线/月线。
- `stock_info_a_code_name`：从 `raw/stock_basic_data.parquet` 返回代码和名称。

后端由 `FORBIDDENLAND_MARKET_BACKEND` 控制（`remote`、`local` 或 `hybrid`，默认 `remote`）。本地模式不会因
数据缺失而偷偷请求网络；`hybrid` 只有同时设置 `FORBIDDENLAND_ALLOW_REMOTE_FALLBACK=1` 时才
允许对未覆盖接口回源。切换时只改环境变量，业务调用保持不变。
