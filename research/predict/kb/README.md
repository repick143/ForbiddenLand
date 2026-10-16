# 股票模型预测知识库

> 用途：在做股票模型预测的查阅与理解时，把**最精炼、可溯源**的结论沉淀到这里，方便快速查阅和回溯原始出处。
> 原则：只收录"能说明来源"的内容；宁可缺一条，也不收无法溯源的条目。

## 使用方式

- **查阅**：先看下方索引，再进对应条目文件。
- **收录**：新建条目时复制 `entry-template.md`，按"最精炼要点 + 引用溯源"组织，并在索引表登记一行。
- **溯源底线**：每条必须有原始来源（标题 / arXiv / URL）、收录或访问日期、关键原句或数据点、验证状态。
- **验证状态**：明确标注"作者自报 / 综述转述 / 本仓库验证"，未独立验证的结论不得写成事实。

## 索引

| 条目 | 主题 | 收录日期 | 关键来源 |
|---|---|---|---|
| `prediction-targets-and-evaluation.md` | 预测目标与评价（方向 vs 幅度+区间、RankIC 量级） | 2026-10-15 | arXiv:2609.34507 等 |
| `method-landscape.md` | 方法谱系快查（树模型→时序基础模型→GNN→LLM→扩散） | 2026-10-15 | frontier_overview 文献清单 |
| `backtest-and-leakage-redlines.md` | 回测与泄漏红线（T+1/涨跌停/成本/前视/复现） | 2026-10-15 | AGENTS.md、frontier_overview |
| `kit-diffusion-transformer.md` | KiT：扩散 Transformer 金融时序基础模型（精炼） | 2026-10-15 | arXiv:2609.34507 |
| `xgboost-ceiling.md` | XGBoost 在次日预测上的客观上限（表达力/信噪比/成本口径） | 2026-10-15 | arXiv:2609.34507、实践共识 |

## 关联文档

- `../frontier_overview.md`：方法全景综述（知识库条目的方法学来源）
- `../baseline_plan.md`：本项目基线方案思路
- `../kit_2609.34507_notes.md`：KiT 论文完整笔记
