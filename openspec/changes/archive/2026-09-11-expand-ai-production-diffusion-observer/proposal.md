## Why

现有 L1 AI 应用层 Observer 只能从 Anthropic 1P API 的职业与任务流量判断“哪些使用更像生产化”，缺少企业和就业人口分母，因此无法判断企业采用广度、员工持续使用与任务生产化是否同步。Census BTOS 与 RPS/FRED 提供公开、低成本且可更新的互补分母，应将其与 Anthropic 纳入同一受治理但不强行融合的证据框架。

## What Changes

- 接入 2025-11-17 新口径开始的 Census BTOS Core AI 数据，提供美国企业总体、行业和企业规模的当前/预期采用率；旧“生产商品或服务”口径不采集、不回填、不参与趋势。
- 接入 RPS/FRED 工作专用 GenAI 序列，提供就业人口的工作采用、上周使用、每日工作使用、AI 辅助工时和自报节省工时；不以 all-purpose 使用率替代工作口径。
- ONS BICS AI 的既有受治理数据与代码仅保留历史审计，不再进入 L1 Observer、主动更新 source group、Agent context、manifest、表格或图表；其专题波次稀疏且没有提供足够增量判断价值。
- 明确不接入一次性的 BTOS AI Supplement，也不接入年度频率的 Eurostat；二者不形成遗留实施任务。
- 将 Anthropic、BTOS、RPS/FRED 纳入统一主动发现与增量更新机制，按各自 release identity 和 cadence 发现新数据，避免重复下载并报告精确状态。
- 将 L1 Observer 命题收敛为：“AI 的企业采用广度、员工持续使用和任务生产化深度是否同步扩大，从局部试验走向可重复的生产工作流？”
- 保留三条独立证据轴：企业采用广度、员工持续使用、Anthropic 任务生产化；禁止将不同统计主体、分母和技术范围硬融合为统一分数。
- 扩展 DataProducts、snapshot manifest、lineage 和可比性诊断，使 Agent 获得紧凑、带口径和证据身份的 context packet；同时交付中文方法卡、结构化表格和可复现可视化，确保人类读者能够审阅。

## Capabilities

### New Capabilities

- `data/census-btos-ai`: Census BTOS Core 新口径 AI 数据的发现、采集、规范化、质量、修订和企业采用查询契约。
- `data/rps-genai-adoption`: RPS/FRED 工作用途 GenAI 采用、持续使用与工时强度序列的受治理契约。
- `data/ons-bics-ai`: ONS BICS AI 条件模块的发现、问卷 regime、英国企业采用与嵌入深度快照契约。

### Modified Capabilities

- `data/structured-ingestion`: 增加由来源注册表驱动的主动发现、异构 cadence、条件模块和增量拉取状态契约，并覆盖现有 Anthropic 来源。
- `data/structured-query`: 增加多来源、不可硬融合的 AI adoption evidence bundle、可比性矩阵、Agent context 和可视化数据一致性契约。
- `evidence/ai-production-penetration-observer`: 将 Anthropic 单源命题扩展为企业、员工和任务三轴命题，修改趋势判断、方法卡、输出结构和人类可视化要求。

## Impact

- 数据源与配置：新增 Census BTOS API/downloads、FRED/RPS series、ONS BICS release/questionnaire watcher；更新 Anthropic Economic Index 的统一 discovery 注册。
- 数据层：新增 source/dataset/metric 注册、source-native artifacts、observations/vintages、问卷和方法 regime、派生计算、质量门及更新健康状态。
- 查询层：新增 AI adoption 多轴 DataProduct、Agent context packet、manifest/lineage、跨源可比性与缺失诊断。
- Evidence 层：更新 `ai_hardware/L1_app` Observer 的 claim、runner 输出、Markdown、表格与图表；不影响其他 sector/layer Observer，也不注入 Chain、PEAD、Chief、组合、风控或交易决策。
- 运维与验收：增加按来源的 release discovery、隔离回填、幂等、修订、schema/question drift、可视化和 context token-budget 验收。
