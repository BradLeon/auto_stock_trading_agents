## Why

结构化数据与非结构化数据都属于系统的数据层，但当前实现分散在 `ats.data`、`ats.structured`、`ats.data_platform` 和 `ats.memory`，导致同一职责有多个入口、依赖方向不清晰、配置无法统一校验，新增来源或迁移 Workflow 的成本持续上升。现在结构化数据基础能力已经完成，适合在继续扩展数据源前先统一数据层的代码、配置和存储责任。

## What Changes

- 在 `ats.data` 下建立统一的数据层命名空间，按共享核心、数据源适配器、采集管道、存储、数据产品和运行时数据划分职责。
- 将结构化和非结构化实现作为同一数据层下的两个领域子树；两者共享实体、来源、版本、质量、血缘和采集运行契约，但保留各自的标准化和存储模型。
- 将当前 `ats.data_platform` 的数据产品接口收敛到 `ats.data.products`，将 `ats.structured` 收敛到 `ats.data` 的结构化子树；迁移期间保留旧导入路径作为兼容转发层。
- 明确适配器、管道、存储和产品之间的单向依赖，禁止适配器直接写库、产品直接访问 Provider，以及结构化和非结构化实现相互反向依赖。
- 建立 `config/data/` 配置层级和统一 catalog loader，区分来源注册、结构化数据集、非结构化文档源、Provider 配置、调度策略和 Workflow 配置。
- 将现有 legacy yfinance 财务报表读取提升为受管的 `company_financials` fallback：仅持久化低频、可审计的收入、利润、现金流、CapEx、资产负债表和 EPS，不持久化行情或期权数据；SEC/发行人披露仍优先。
- 逐步从 `ats.memory` 拆出数据层 schema 与 repository；过渡期保持旧数据库、旧 Workflow 和旧 CLI 可运行，按来源和消费者独立切换。
- 在 facade 稳定后，迁移旧实现持有的 `measurement_*` 与既有 `structured_*` observations/artifacts/运行记录，以及文档、版本、分块和证据；迁移必须可恢复、可续跑、可对账，且不得丢失版本、血缘或审计信息。
- 将每个 Agent 与 Workflow 显式切换到统一数据产品路径，并以新旧输出对账、回滚演练和端到端验收作为发布门。
- 只有全部数据迁移、消费者切换和稳定性验收完成后，才删除旧模块、旧配置 alias、重复 repository 实现和不再需要的旧 schema；在此之前不得归档本变更。
- 交付开发者、运维者和使用者可阅读的统一数据层架构文档，包含目标目录、依赖规则、配置字段、迁移步骤、测试门槛和兼容周期。

## Capabilities

### New Capabilities

- `data/data-layer-architecture`: 定义统一数据层的代码边界、配置入口、依赖方向、兼容接口和分阶段迁移契约。

### Modified Capabilities

`company_financials` 的持久化 fallback 覆盖范围扩展至既有 yfinance 报表读取，以补足 SEC Facts 或 defeatbeta 镜像缺失的当季离散季度及概念。TSM 的每普通股 EPS、每 ADR EPS 与币种必须作为独立、显式单位的指标；Provider 的拆股调整 EPS 与报告口径 total debt 也不得冒充发行人原始值。SEC 的长期债务与总债务必须分开映射。行情读取仍属于 runtime 边界。

## Impact

- 代码：`src/ats/data`、`src/ats/structured`、`src/ats/data_platform`、`src/ats/memory` 及其导入方将分阶段调整。
- 配置：新增 `config/data/`，现有 `config/structured_data.yaml`、`config/sources.yaml` 和 `config/news_sources.yaml` 在兼容期内继续有效；新增受管 yfinance 财务报表来源的质量、限流与回退声明。
- 接口：新增统一的 `ats.data` 数据层入口；旧模块路径暂不删除，只转发到新实现。
- 数据库：第一阶段不立即迁移或删除旧表；后续必须先完成按数据域的可恢复迁移、对账和消费者切换，再退役旧逻辑。
- 测试：每个迁移阶段必须通过导入兼容、配置校验、数据产品回归、来源采集 smoke test 和受影响 Workflow 回归后才能进入下一阶段。
