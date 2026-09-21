## Why

Evidence Observer 的 L1 AI 应用层缺少可持续更新、可追溯且语义边界清晰的职业与任务生产采用数据。Anthropic Economic Index 已公开 Claude.ai、1P API、SOC/O*NET taxonomy 和 Observed Exposure 数据，但当前网页 UI 不是稳定采集接口，且其 Usage Share 容易被误读为从业者采用率，因此需要将官方发布文件接入受治理的结构化数据平台。

## What Changes

- 新增 `anthropic_economic_index` 来源和 `ai_work_adoption` 数据集，通过 Hugging Face metadata API 发现并锁定官方 release commit，流式采集 Global × SOC/O*NET 白名单数据。
- 将 Claude.ai 与 1P API 建模为严格隔离、可独立成功或失败的 source-product slices；将 Observed Exposure 和 task penetration 建模为非月度研究快照。
- 引入 SOC major group、详细职业与 O*NET task 实体及版本化关系，所有映射仅使用稳定外部 ID。
- 扩展 structured ingestion，使同一批次中的 observation、entity relation 能精确绑定各自 artifact，缺失、重复或未知 slice key 不再回退到首个 artifact。
- 注册 Usage Share、Automation/Augmentation、协作模式、工作用途、自主程度、Observed Exposure 和 Task Penetration 等规范化指标，并提供可复算、带版本和输入 observation IDs 的领域派生指标。
- 新增 job-adoption snapshot、job profile、层级/as-of 查询和运维 CLI 契约，供 L1 Observer、研究查询与数据质量审计使用。
- 每周检查上游 release，支持幂等重跑、同期间修订 vintage、分产品状态、质量门、发布与回滚；隔离回填 2026-04 和 2026-05。
- 明确不采集或推断地理差异、员工/企业真实采用率、留存与席位渗透、Claude Code、2026-04 以前抽样周序列及未公开 cohort size；首版不接入 PEAD、Chain、Macro、Sector、Chief 或交易决策 workflow。

## Capabilities

### New Capabilities

- `data/anthropic-economic-index`: 定义 Anthropic Economic Index 的官方 release 发现、Global-only 采集、指标语义、taxonomy、Observed Exposure、质量门、更新状态和面向 L1 的数据产品契约。

### Modified Capabilities

- `data/structured-ingestion`: 增加多 artifact slice 精确血缘，以及 reference entities、versioned relations 的接入、校验、幂等和隔离规则。
- `data/structured-query`: 增加层级实体与 taxonomy as-of 查询，约束跨 source product 比较，并要求领域派生结果公开版本及输入 observation IDs。

## Impact

- 配置：数据目录、来源注册、指标注册和采集策略。
- 代码：structured adapter contracts、pipeline、repository/schema migration、Anthropic adapter、DataProducts、CLI 与 snapshot manifest。
- 数据：新增不可变 query-slice artifacts、实体关系、月度 observation vintages 和研究快照；现有表采用向后兼容的增量扩展。
- 运维：新增每周 release discovery、分 slice 健康状态、质量/lineage/availability 检查、shadow/platform 发布和回滚流程。
- 测试与文档：新增 2026-06-26 fixture、2026-04/05 回填验收、异常路径测试、运维文档及 source checklist 更新。
- 外部依赖：仅访问公开、无需鉴权的 Anthropic Hugging Face repository；最终血缘全部固定到 commit SHA，不持久化可变 `main` URL。
