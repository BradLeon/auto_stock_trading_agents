## Why

结构化数据与非结构化数据都属于系统的数据层，但当前实现分散在 `ats.data`、`ats.structured`、`ats.data_platform` 和 `ats.memory`，导致同一职责有多个入口、依赖方向不清晰、配置无法统一校验，新增来源或迁移 Workflow 的成本持续上升。现在结构化数据基础能力已经完成，适合在继续扩展数据源前先统一数据层的代码、配置和存储责任。

## What Changes

- 在 `ats.data` 下建立统一的数据层命名空间，按共享核心、数据源适配器、采集管道、存储、数据产品和运行时数据划分职责。
- 将结构化和非结构化实现作为同一数据层下的两个领域子树；两者共享实体、来源、版本、质量、血缘和采集运行契约，但保留各自的标准化和存储模型。
- 将当前 `ats.data_platform` 的数据产品接口收敛到 `ats.data.products`，将 `ats.structured` 收敛到 `ats.data` 的结构化子树；迁移期间保留旧导入路径作为兼容转发层。
- 明确适配器、管道、存储和产品之间的单向依赖，禁止适配器直接写库、产品直接访问 Provider，以及结构化和非结构化实现相互反向依赖。
- 建立 `config/data/` 配置层级和统一 catalog loader，区分来源注册、结构化数据集、非结构化文档源、Provider 配置、调度策略和 Workflow 配置。
- 逐步从 `ats.memory` 拆出数据层 schema 与 repository；过渡期保持旧数据库、旧 Workflow 和旧 CLI 可运行，按来源和消费者独立切换。
- 交付开发者、运维者和使用者可阅读的统一数据层架构文档，包含目标目录、依赖规则、配置字段、迁移步骤、测试门槛和兼容周期。

## Capabilities

### New Capabilities

- `data/data-layer-architecture`: 定义统一数据层的代码边界、配置入口、依赖方向、兼容接口和分阶段迁移契约。

### Modified Capabilities

无。现有结构化数据查询和采集行为保持不变，本变更先统一实现边界和开发/运维契约。

## Impact

- 代码：`src/ats/data`、`src/ats/structured`、`src/ats/data_platform`、`src/ats/memory` 及其导入方将分阶段调整。
- 配置：新增 `config/data/`，现有 `config/structured_data.yaml`、`config/sources.yaml` 和 `config/news_sources.yaml` 在兼容期内继续有效。
- 接口：新增统一的 `ats.data` 数据层入口；旧模块路径暂不删除，只转发到新实现。
- 数据库：不立即迁移或删除旧表；先拆分代码所有权和 repository，再进行数据迁移与旧逻辑退役。
- 测试：每个迁移阶段必须通过导入兼容、配置校验、数据产品回归、来源采集 smoke test 和受影响 Workflow 回归后才能进入下一阶段。
