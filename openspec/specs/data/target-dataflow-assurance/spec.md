# data/target-dataflow-assurance Specification

## Purpose

为目标 Dataflow 的来源、准入、共享事实、产品及消费者路径提供逐项可复现的核验和切流资格，防止未完成或不可回滚的数据路径随 Phase F 一并切换。

## Requirements

### Requirement: 目标节点与关键连线必须逐项登记和核验

系统 SHALL 为目标 Dataflow 的每个节点和关键连线维护覆盖记录，包含实现/配置位置、数据或查询 owner、直接消费者、旧路径、验证命令或证据、最近验证时间和 `verified | needs_refactor | missing | blocked` 状态。状态 SHALL 来自实际运行、契约或对账证据，不得仅由已归档 change 或文档声明推断。无法验证的节点或连线 MUST 保留缺口原因和受影响消费者。

#### Scenario: 存在代码但无端到端读取证据

- **WHEN** 数据产品有实现文件，但无法追溯到已准入资产及消费者读取
- **THEN** 对应路径 SHALL NOT 标记为 `verified`，记录 SHALL 指明缺少的证据和消费者影响

#### Scenario: 某节点已有可靠实现

- **WHEN** 节点及其相关连线通过可复现核验
- **THEN** 覆盖记录 SHALL 指向现有实现和证据，SHALL NOT 要求为验收而重建第二套实现

### Requirement: 数据域验证必须覆盖写入到读取及异常语义

对每个候选持久化数据域，系统 SHALL 验证来源与原始资产、准入或 quarantine、不可变版本、共享观测/文档/中性事实、数据产品及消费者读取之间的血缘；还 SHALL 验证正常、来源失败、陈旧、拒绝、修订与历史 as-of 查询。Agent 观点 SHALL 只能进入 Workflow Memory，不能回写共享事实。runtime 数据和内部账本数据 SHALL 分别验证其查询 owner、as-of 和完整性边界，不得伪装为持久化研究观测。

#### Scenario: 来源修订已发布的数据

- **WHEN** 来源发布同一实体/期间的修订材料
- **THEN** 消费者 SHALL 能识别新旧版本及其 known-at/as-of，历史查询 SHALL 不被新版本静默覆盖

#### Scenario: Agent 试图写入研究观点

- **WHEN** Agent 产出叙事判断或投资建议
- **THEN** 该产出 SHALL 保留在 Workflow Memory，数据层共享事实 SHALL NOT 接收其为中性事实

### Requirement: Data 与目标角色的每条对接关系必须有独立契约

系统 SHALL 按 `docs/ARCHITECTURE.md` §4 为 Layer、Information、Sector、Fundamental、Macro、Technical、Chief、Risk、Trader 与 Clerk 的每条数据输入维护独立 consumer contract，包含产品/API owner、schema/version、来源与 as-of/vintage、完整性/缺口语义、允许消费者、fallback、旧入口和验证证据。持久化研究输入 SHALL 只通过 Data Products；实时市场输入 SHALL 只通过 Runtime Data Gateway；组合、交易、绩效、决策与审批状态 SHALL 只通过 Internal State API 或受管决策/执行契约。外部研究文档 SHALL NOT 以 Workflow Memory 或底层表作为正式读取 API。

#### Scenario: Layer 只登记了部分目标输入

- **WHEN** Layer consumer contract 只覆盖策展知识或结构化产业数据，而没有覆盖目标要求的已准入文档/命题证据数据包
- **THEN** Layer 数据对接 SHALL 标记为不完整，直到 `HIER_DATA` 与 `DOC_DATA` 两条输入均通过验证

#### Scenario: Information 从 Workflow Memory 读取外部材料

- **WHEN** Information 的正式研究输入 API 指向 Workflow Memory、底层文档表或 Provider，而不是已准入的 Data Product
- **THEN** 该路径 SHALL 被标记为边界违规并被架构守卫拒绝，且不得将历史兼容读取视为目标契约

#### Scenario: 两条允许的跨分析师依赖

- **WHEN** consumer contract 扫描分析师间 Task Projection 读取
- **THEN** 系统 SHALL 只允许 Layer→Sector 与 Information→Fundamental，并 SHALL 将任何其他分析师观点读取标记为阻断项

#### Scenario: Chief、Risk 与 Clerk 使用不同内部数据边界

- **WHEN** Chief、Risk 或 Clerk 请求组合、市场、规则、交易历史、审批或券商状态
- **THEN** 每个输入 SHALL 按目标关系分别解析到 Internal State API、Runtime Data Gateway、规则产品、审批契约或 broker/ledger 接口，不得以泛化 `WORKFLOW` consumer 或直接数据库读取代替

### Requirement: Evidence Observer 必须作为旧角色退役而非新增目标角色

系统 SHALL 将旧 Evidence Observer 标记为由 Layer Analyst 取代的历史角色，并 SHALL 对其已登记来源、产品、运行入口和消费者逐项作迁移决定：与产业层级、实体关系、截面对比或命题证据相关者迁入 Layer 的 `HIER_DATA`/`DOC_DATA`；其他项保留为一般数据产品或按零消费者规则退役。目标 consumer manifest SHALL NOT 同时包含可运行的 Evidence Observer 与 Layer Analyst。

#### Scenario: 旧 Observer 来源仍有 Layer 价值

- **WHEN** 旧 Evidence Observer 来源能够支持 Layer 的目标职责且通过来源准入
- **THEN** 该来源 SHALL 保留原 lineage 并迁入 Layer 数据包，不得通过创建第二个分析角色继续消费

#### Scenario: Observer 与 Layer 同时登记为活跃消费者

- **WHEN** consumer manifest 或调度配置同时将 Evidence Observer 和 Layer Analyst 标为活跃分析角色
- **THEN** 架构校验 SHALL 失败并指出重复职责及待执行的迁移/退役项

### Requirement: 五角色实际读取必须保留治理来源

系统 SHALL 在 fundamental/chief/risk/trader/clerk 实际消费点执行各自声明的产品权限与当次业务 scope 检查。研究输入 SHALL 经治理产品或投影，内部状态 SHALL 经 state API，Trader SHALL 经受限 runtime 行情与完整批准授权执行；Clerk SHALL 读取真实执行回报及审批上下文，并在步骤间与发布前重查读取范围与隔离写入检查。实际调用 SHALL 保留 API、来源 refs 与状态，SHALL NOT 用静态导入、预置 refs 或扩大产品权限代替接线。

#### Scenario: 实际五角色留存消费引用

- **WHEN** 实际运行 Fundamental、Chief、Risk、Trader 与 Clerk 入口
- **THEN** 系统 SHALL 留存各入口真实 API/投影/状态/授权/执行回报引用及结果状态
- **AND** SHALL 保持报价、授权与发布检查

#### Scenario: Clerk 步骤间资格失效

- **WHEN** Clerk 已完成一个子步骤，但后续读取越出当次 scope 或隔离环境不再完整
- **THEN** 后续子步骤与最终发布 SHALL 停止
- **AND** SHALL 保留已完成步骤的审计，SHALL NOT 为继续运行建立替代 broker

### Requirement: 消费者必须按声明的数据产品与业务 scope 读取

交易路径切换并完成 R5 退役后，研究分析师、主理人、风控、Trader 与 Clerk SHALL 只读取各自声明的数据产品（含投影、内部状态与受限行情），并使用当次明确的业务 scope。读取前 SHALL 检查该数据产品；不合格时 SHALL 显式失败并阻断受影响任务。读取 SHALL NOT 依赖路由表、逐 scope 读取资格账本或以代码指纹绑定的资格证据。消费者 SHALL NOT 读取未声明的产品，也不得扩大 scope。

#### Scenario: 读取未声明的产品

- **WHEN** Trader 请求读取研究历史或任意标的的行情，而它只声明了执行报价
- **THEN** 读取 SHALL 被拒绝并报告未声明的产品
- **AND** SHALL NOT 以其他消费者的声明代替

#### Scenario: 数据产品不合格

- **WHEN** 主理人每日决策所需的某个数据产品在读取前检查不合格
- **THEN** 读取 SHALL 显式失败，受影响的决策运行 SHALL 记为不完整
- **AND** SHALL NOT 以空结果或旧表内容补齐

#### Scenario: 不依赖路由表与资格账本

- **WHEN** 路由表与读取资格账本已退役，消费者按声明的产品与 scope 读取
- **THEN** 读取 SHALL 只依据声明与读取前检查结果判定
- **AND** SHALL NOT 因缺少路由行或资格记录而失败

#### Scenario: scope 与当次入口不一致

- **WHEN** 一次读取使用的实体、时间范围或消费者与当次入口绑定的 scope 不一致
- **THEN** 读取 SHALL 被拒绝，SHALL NOT 吞为空结果

### Requirement: 每次运行必须重查实际输入的有效性与新鲜度

每次运行 SHALL 重查实际输入的有效性与新鲜度；读取失败 SHALL 显式报错，SHALL NOT 吞成空结果。独立运行的 as_of 与查询窗口 SHALL 用于当次输入的截止与新鲜度判断。最小健康探测和数据刷新 SHALL 可自动重复执行，无需逐次人工批准，但 SHALL NOT 自动改写已有签核；历史报告与签核 SHALL 保留原始内容。

#### Scenario: 工作流已启用但输入已陈旧

- **WHEN** 工作流处于启用状态，但实际来源数据不满足当次新鲜度或缺失必需输入
- **THEN** 该运行 SHALL 被拒绝并显式报错
- **AND** SHALL NOT 以工作流已启用替代输入证明
