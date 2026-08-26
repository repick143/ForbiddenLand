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
