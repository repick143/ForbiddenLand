# 实验股票池（Universe）

存放股票模型预测实验使用的成分股/股票池快照。文件均为轻量控制面清单（6 位代码 + 名称，
`#` 行为注释），由 `research.predict.baseline.train` 的 `--symbols-file` 读取。

## 当前清单

- `symbols_hs300_star50.txt` — 沪深300（`000300`）+ 科创50（`000688`）成分股并集，330 只，
  快照日期 2026-10-16。文件头记录来源与 as-of。

## 使用

```bash
./.venv/bin/python -m research.predict.baseline.train \
  --symbols-file research/predict/universe/symbols_hs300_star50.txt
```

## 重新生成

指数成分会定期调整；需要新快照时用以下命令抓取并覆盖对应文件（更新文件头 as-of 日期），
来源为 csindex 官网（经 AkShare `index_stock_cons_csindex`）：

```python
import akshare as ak

for code in ("000300", "000688"):
    df = ak.index_stock_cons_csindex(symbol=code)
    print(code, df["日期"].iloc[0], len(df))  # 核对 as-of 与数量
    # 取 df["成分券代码"]/["成分券名称"] 与既有清单并集、排序后写回文件
```
