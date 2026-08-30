## Why

结构化数据与非结构化数据都属于系统的数据层，但当前实现分散在 `ats.data`、`ats.structured`、`ats.data_platform` 和 `ats.memory`，导致同一职责有多个入口、依赖方向不清晰、配置无法统一校验，新增来源或迁移 Workflow 的成本持续上升。现在结构化数据基础能力已经完成，适合在继续扩展数据源前先统一数据层的代码、配置和存储责任。

## What Changes

- 在 `ats.data` 下建立统一的数据层命名空间，按共享核心、数据源适配器、采集管道、存储、数据产品和运行时数据划分职责。
- 将结构化和非结构化实现作为同一数据层下的两个领域子树；两者共享实体、来源、版本、质量、血缘和采集运行契约，但保留各自的标准化和存储模型。
- 将当前 `ats.data_platform` 的数据产品接口收敛到 `ats.data.products`，将 `ats.structured` 收敛到 `ats.data` 的结构化子树；迁移期间保留旧导入路径作为兼容转发层。
- 明确适配器、管道、存储和产品之间的单向依赖，禁止适配器直接写库、产品直接访问 Provider，以及结构化和非结构化实现相互反向依赖。
- 建立 `config/data/` 配置层级和统一 catalog loader，区分来源注册、结构化数据集、非结构化文档源、Provider 配置、调度策略和 Workflow 配置。
- 将现有 defeatbeta、yfinance、SEC 与发行人披露统一为受管的 `company_financials` 来源链：按可达性依次尝试 defeatbeta、yfinance、SEC Facts、发行人 IR；一旦前序来源提供同一报告期的完整财务报表合同，即停止后续抓取。仅持久化低频、可审计的收入、利润、现金流、CapEx、资产负债表和 EPS，不持久化行情或期权数据。
- 逐步从 `ats.memory` 拆出数据层 schema 与 repository；过渡期保持旧数据库、旧 Workflow 和旧 CLI 可运行，按来源和消费者独立切换。
- 在 facade 稳定后，迁移旧实现持有的 `measurement_*` 与既有 `structured_*` observations/artifacts/运行记录，以及文档、版本、分块和证据；迁移必须可恢复、可续跑、可对账，且不得丢失版本、血缘或审计信息。
- 将每个直接消费可切换数据产品的 Agent 与 Workflow 显式切换到统一数据产品路径；调用方自身改动时以新旧输出对账、回滚演练和端到端验收作为**消费者切换门**，但不把它们作为数据源或数据集发布门；区分等价结果、经独立验证的受管升级、平台回归和仅编排边界，避免将旧路径故障或 Workflow memory 误判为平台数据错误。
- 在 task 10.5 的数据层发布范围内，逐个完成 TrendForce 文章、SemiAnalysis（IMAP/RSS）和 IBKR News 的新路径资产沉淀、覆盖账本、隔离验收与来源级发布。本轮只判断数据本身的实体/来源、正文、时间、覆盖、去重、时效、质量和血缘；不切换任何 Agent/Workflow 消费者，也不删除 legacy 路径。SemiAnalysis 未订阅时可将可验证的邮件预览正文发布为明确标记 `partial` 的资产，绝不得伪装为全文；TrendForce 的文章与 DRAM 合约价是两个独立数据集，禁止混作同一验收对象。
- 确立 IBKR News 为新闻新路径的第一优先级：其可达、动态 provider 枚举与历史请求健康时只采集 IBKR；`yfinance_live_news` 只在 TWS 不可达、权限/订阅不足、provider 不可用、请求受限/拒绝或指定切片失败时，作为对应失败范围的 fallback，而不是与 IBKR 正常双采集或等价替代。IBKR 候选必须保留精确原始新闻时间、查询实体、provider/article ID、标准化标题和正文 hash，并以标题实体校验与分层去重防止无关或重复头条耗尽正文预算。Yahoo 仍保存原生 ID、publisher、发布时间和 canonical URL；只接纳标题中明确命中 PEAD 实体 ticker/公司名/已登记别名的候选，取得正文后还须验证标题锚点。未命中的 Yahoo 推荐项必须以 `association_rejected` 记录在验收清单中，不得进入可发布资产；正文不完整或页面正文与标题不一致同样不得发布。
- 只有全部数据迁移、消费者切换和稳定性验收完成后，才删除旧模块、旧配置 alias、重复 repository 实现和不再需要的旧 schema；在此之前不得归档本变更。
- 交付开发者、运维者和使用者可阅读的统一数据层架构文档，包含目标目录、依赖规则、配置字段、迁移步骤、测试门槛和兼容周期。

## Capabilities

### New Capabilities

- `data/data-layer-architecture`: 定义统一数据层的代码边界、配置入口、依赖方向、兼容接口和分阶段迁移契约。

### Modified Capabilities

`company_financials` 的受管来源链覆盖既有 defeatbeta、yfinance、SEC Facts 与发行人 IR。每个实体/报告期必须选定一个完整报表包来源，不得把不同 Provider 的字段静默拼接成同一“官方口径”；仅当 SEC Facts 与同实体、同报告期的发行人 IR 单独均不完整时，才允许形成保留字段级血缘的官方披露包。Provider 数据必须保留其 Provider 身份，不得标注为发行人原始披露。TSM 的每普通股 EPS、每 ADR EPS 与币种必须作为独立、显式单位的指标；Provider 的拆股调整 EPS 与报告口径 total debt 也不得冒充发行人原始值。SEC 的长期债务与总债务必须分开映射。行情读取仍属于 runtime 边界，P/E 由受管 EPS 与 runtime 价格按需计算。

## Impact

- 代码：`src/ats/data`、`src/ats/structured`、`src/ats/data_platform`、`src/ats/memory` 及其导入方将分阶段调整。
- 配置：新增 `config/data/`，现有 `config/structured_data.yaml`、`config/sources.yaml` 和 `config/news_sources.yaml` 在兼容期内继续有效；新增受管 yfinance 财务报表来源的质量、限流与回退声明。
- 接口：新增统一的 `ats.data` 数据层入口；旧模块路径暂不删除，只转发到新实现。
- 数据库：第一阶段不立即迁移或删除旧表；后续必须先完成按数据域的可恢复迁移、对账和消费者切换，再退役旧逻辑。
- 测试：每个迁移阶段必须通过导入兼容、配置校验、数据产品回归、来源采集 smoke test 和受影响 Workflow 回归后才能进入下一阶段。
- 外部来源约束：SemiAnalysis 必须复用既有研究邮件/RSS 管道并以邮件标识与 canonical URL 去重；IBKR News 只允许只读 TWS 查询，连接、权限、限流和“无新闻”必须可区分；TrendForce 的付费墙或过期 RSS 只能形成明确缺口，不得伪造覆盖成功。
