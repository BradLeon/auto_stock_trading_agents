## Why

FactSet Earnings Insight 已被 `macro.yaml` 和 Macro Agent 以本地 PDF 下载加正则解析的 legacy 路径使用，但它没有进入统一文档资产、结构化 vintage、质量门、血缘和 DataProducts 体系。最新报告证明该来源具有稳定周度价值，同时也存在栅格图表、措辞漂移、GAAP/Non-GAAP 混合和正文内部冲突；继续由 Agent 现场下载和解析会让错误、缺周和历史不可回放被隐藏。

## What Changes

- 新增 FactSet Earnings Insight 周度采集能力：解析固定入口重定向，幂等保存原始 PDF、HTTP provenance、提取文本和图表区域，作为统一的 `research_article` 文档资产。
- 新增 `sp500_earnings_insight` 持久数据集，把指数级和 11 个 GICS 行业的核心盈利、营收、surprise、margin、guidance、盈利修正广度、估值、地域收入和评级读数保存为不可变 vintage。
- 对正文和图表产生的数值使用候选、证据锚点、确定性校验、冲突隔离和 shadow 发布门；模型或视觉结果不能单独获得正式准入。
- 对 `082826` 的 Sector 图表新增本地半自动表格解码和可审阅标注包：系统预填候选值、单元格区域与证据，人工按图表确认或修正；发布前必须以该 golden dataset 逐格比较，不接受仅有非空标注的形式性验收。
- `082826` 的 Sector 验收分为可审计的连续阶段：人工 golden cells、独立 PDF decoder、ChartTable 候选、逐格 comparator 与真实 PDF 验收报告。任何阶段性产物均不得被误报为 `sector_core` 已通过。
- 新增稳定的 Earnings Insight DataProducts 快照接口，供 Macro 和 Sector Workflow 读取；Chief、Risk 与 PEAD 继续只消费 Macro/Sector 结论，避免同一来源被重复加权。
- 把周度调度改为先采集/发布 FactSet，再执行 Macro、Sector；采集失败时显式返回 stale/unavailable，不允许 Agent 隐式在线回退。
- 以最新 2026-08-28（`082826`）报告建立上线验收与人工标注集；指数指标先切换，行业图表指标仅在该期逐单元格通过发布门后切换。历史 PDF 可保留作受控重处理输入，但不构成当前发布标准或历史消费结论依据。
- 迁移完成并经过观察期后，移除 Macro Agent 内部的直接下载、本地文件夹和 PDF 解析路径。

## Capabilities

### New Capabilities

- `data/factset-earnings-insight`: 覆盖 FactSet 周报的统一文档采集、结构化指标、证据与质量治理、DataProducts 消费、调度和渐进迁移。

### Modified Capabilities

<!-- None. Existing generic data-layer requirements remain in force; this change adds a source-specific capability. -->

## Impact

- 数据配置：统一 catalog 的 unstructured/structured source、dataset、31 个 V1 metric（包括行业盈利修正广度）、entity、feature flag、release 与 schedule 意图。
- 数据实现：FactSet 获取与 PDF 文档适配、文本/图表候选抽取、structured ingestion、evidence link 与质量报告。
- 公共消费面：新增 typed Earnings Insight DataProducts product；现有 `EarningsBackdrop` 变为兼容 DTO。
- Workflow：Macro 和 Sector 改为 platform-only 数据读取，周度作业增加显式数据准备阶段；下游 Chief/Risk/PEAD 的既有注入边界保持不变。
- 运维与测试：增加当前报告的 source acceptance、release-check、as-of/vintage/lineage、consumer smoke 和 rollback drill；历史 backfill 是可选运维能力，不是上线门槛。
- 依赖：正文继续使用现有 PDF 文本栈；图表数值需要受控的图像结构化抽取能力，但不得把 OCR/LLM 置信度当作唯一准入依据。
