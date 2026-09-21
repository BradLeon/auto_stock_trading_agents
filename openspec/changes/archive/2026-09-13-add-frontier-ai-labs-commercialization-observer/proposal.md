## Why

L1 AI 应用层目前已经能够观察技术能力与生产化扩散，但仍缺少独立回答“模型公司能否把持续使用转化为高质量、可留存且具有合理单位经济的收入，并形成可持续商业模式”的受治理证据。OpenAI、Anthropic 尚未通过定期财报披露完整经营数据，因此需要先建立 Frontier AI Labs 收入水平与趋势这一商业化证据子域，并明确区分公司披露、年化运行率、第三方估算和预测，避免把私营公司零散 ARR 信息误写为可比财务事实。

## What Changes

- 新增 Frontier AI Labs 收入结构化数据集，首版覆盖 OpenAI 与 Anthropic，并保留可扩展的实验室实体注册机制。
- 以 Sacra 公开公司页面为最新数据主信源，定期自动发现页面更新，提取带来源引用的收入观察值；同一期间修订保留 vintage，不静默覆盖历史。
- 以 TickerTrends 的公开文章 `anthropic-vs-openai-arr-tracking` 补充 2026 年 1–6 月历史观察值；只接纳文章明确披露的日期和值，不插值、不把 tracked estimate 改写为公司披露。
- 在统一结构化数据层持久化收入指标、计量口径、事实/估算/预测身份、as-of/known-at 时间、来源、引用、质量、血缘与方法版本，支持离线复用、聚合计算和可视化。
- 新增 L1“商业化能力”独立 Evidence Observer。首版只判断其中的“Frontier AI Labs 收入水平与趋势”，不改写既有“生产化与应用扩散”Observer，也不把 BTOS、RPS、Ramp、Anthropic Economic Index 重新归类为商业化证据。
- 正式报告展示 OpenAI、Anthropic 的可比收入曲线、最新水平、变化、口径差异、来源可信度和证据缺口；报告明确说明收入增长只能证明收入兑现，尚不能单独证明留存、毛利、客户集中度或完整单位经济。
- 采集、解析、质量门、查询、Observer、图表、manifest replay 和回归测试全部通过后，直接将该数据源与 Observer 发布到 `platform`/正式 L1 报告，不设置 `shadow` 或 `experimental` 过渡状态。

## Capabilities

### New Capabilities

- `data/frontier-ai-labs-revenue`: 定义 Sacra/TickerTrends 收入证据的发现、提取、结构化持久化、口径身份、修订 vintage、质量门和可复现查询契约。
- `evidence/ai-commercialization-observer`: 定义 L1 商业化能力 Observer 的固定命题、首版 Frontier AI Labs 收入判断、Agent context、中文正式报告与可视化契约。

### Modified Capabilities

- `data/structured-ingestion`: 增加事件驱动网页收入证据的定期发现、幂等增量入库、同期间修订和直接平台发布要求。
- `data/structured-query`: 增加按实验室、收入计量口径、观察身份、期间和 as-of 查询可比收入序列及完整 lineage 的要求。

## Impact

- 数据配置：新增 Sacra 与 TickerTrends source/dataset/metric/schedule 注册，以及 OpenAI、Anthropic 的实体映射与来源优先级。
- 数据层：新增公开网页发现与解析适配器、原始 artifact 保存、SQLite observation/vintage 入库、质量检查和收入序列 DataProduct。
- Evidence：`ai_hardware/L1_app` 新增独立商业化 Observer 声明及 runner，不改变现有生产化 Observer 的 claim、三轴状态或 Ramp 补充命题。
- 输出：新增机器 packet、compact Agent context、中文 Markdown、收入趋势表、PNG、CSV/JSON、sidecar 和 snapshot manifest；所有输出共享 observation IDs 与 rows hash。
- 运维：新增定期探测任务。无新披露时记录 `no_change`；页面结构变化、口径冲突或来源不可达时保留上次正式数据并明确告警，不把缺失解释为收入为零。
- 测试：覆盖网页 fixture、历史回填、指标分类、去重、revision/as-of、跨来源冲突、离线重放、中文图表字体、按层独立运行及对既有 L1 生产化报告的隔离回归。
