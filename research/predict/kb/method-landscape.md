# 方法谱系快查

- 标签：`方法` `模型` `综述`
- 收录日期：2026-10-15

## 最精炼要点

1. 基线层：统计（ARIMA/GARCH）+ 树模型（XGBoost/LightGBM）：可解释、调参简单、适合先验证管线。
2. 时序基础模型：Chronos（数值 token 化语言模型式）、TimesFM、Moirai、FinCast（金融 MoE）：零样本强，但金融数据通常需微调。
3. 图神经网络：MDGNN（多关系动态图 + Transformer）、DiffSTOCK（扩散 + 图）：显式建模股票联动，但关系边构造易引入泄漏。
4. LLM 与智能体：FinGPT（新闻传播广度 + 指令微调）、FinMem（分层记忆自主交易）、AlphaAgent（LLM 因子挖掘）：热但需防 lookahead（预训练语料含未来信息）。
5. 多模态：K 线图图像 + ViT/TFT 融合（IEEE Access 2025），以及订单簿级 ViT-LOB。
6. 扩散/生成：DiffSTOCK、KiT：直接生成整条未来路径分布，天然带区间，是"预测 + 不确定性"的上限形态。

## 引用溯源

全部条目出自 `../frontier_overview.md` 第 3 节的参考文献清单（2026-10-15 网络检索整理）：

- FinCast: arXiv:2508.19609（CIKM 2025）
- FinGPT: arXiv:2412.10823
- MDGNN: arXiv:2402.06633
- DiffSTOCK: arXiv:2403.14063
- FinMem: IEEE TBD 2025
- AlphaAgent: KDD 2025
- ViT-LOB: 2024
- 多模态混合模型: IEEE Access 2025
- Chronos: TMLR；Moirai: ICML

## 验证状态

- 上述 arXiv ID 来自 `frontier_overview.md` 检索整理，未逐一打开原文核对；使用前建议回链原文确认标题与结论。
