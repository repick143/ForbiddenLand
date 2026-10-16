# KiT：扩散 Transformer 金融时序基础模型（精炼版）

- 标签：`扩散模型` `Transformer` `基础模型` `区间预测`
- 收录日期：2026-10-15

## 最精炼要点

1. 思路：把"预测未来 K 线"重定义为**条件路径生成**（flow matching），一次性生成整组未来 OHLCV 轨迹，规避自回归"一步错步步错"的误差累积。
2. 编码：5 维 log-ratio 状态（跳空/实体/上影/下影/量比），可逆、尺度无关、K 线几何合法——一个模型可跨工具与市场。
3. 推理：双分类器自由引导（身份轴 + 历史轴）+ Euler 求解，采样 16 条轨迹 → 任意分布统计量（均值/分位数/区间）。
4. 规模：3 市场 × 7 时间尺度、约 30 亿根 K 线、KiT-L 284M 参数、128×H200 训练。
5. 结果：收益平均 RankIC 0.057、波动率 RankIC 0.66，每个时间尺度领先专用金融预测器与通用时序基础模型（Chronos-2、TimesFM 等）。
6. 启示：收益可预测上限很低、波动率可预测得多 → 支持"点估计 + 区间"目标；多路径采样是区间预测的上限形态。

## 引用溯源

- 原文：*KiT: A Foundation Model for Financial Time-Series Forecasting using Diffusion Transformers*，Boyu Zhang, Haorui Li，arXiv:2609.34507v1 [cs.LG]，2026-09-28。
- 链接：https://arxiv.org/abs/2609.34507（摘要页，访问 2026-10-15）；HTML 全文同页可读。
- 完整笔记（方法公式、数据切分、基线清单、逐节引用）：`../kit_2609.34507_notes.md`。

## 验证状态

- 基于 arXiv 摘要页 + HTML 全文整理（2026-10-15）；数字为作者自报。
- 代码仓库 https://github.com/Luciferbobo/KiT 论文称"将提供"，尚未验证。
