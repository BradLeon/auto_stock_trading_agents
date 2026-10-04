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

### Requirement: 每个直接消费者必须有独立读取契约和回退证据

系统 SHALL 按数据域与直接消费者登记唯一写入/查询 owner、读取契约、来源版本、新鲜度、完整性、切流控制、稳定回退路由、旧入口和差异归因。旧新读取对账 SHALL 在相同实体、范围和 as-of 条件下执行，并 SHALL 将无法解释的差异保持为缺口，不得静默以空值或 Provider 直连补齐。结构化、非结构化、runtime 和 Internal State 的消费者 SHALL 分别验收。

#### Scenario: 同一产品的两个消费者状态不同

- **WHEN** 一个消费者已通过对账与回滚演练，而另一个消费者缺少读取契约
- **THEN** 只有前者 SHALL 可获得读取切流资格，后者 SHALL 保持稳定路由

#### Scenario: 新旧读取结果不一致

- **WHEN** 同实体、同 as-of 的新旧读取结果存在未解释差异
- **THEN** 该消费者 SHALL 不得标记可切流，差异 SHALL 记录到可复现的对账结果

### Requirement: Phase F 读取资格必须按数据域与消费者 fail closed

系统 SHALL 仅在候选数据域及消费者的写入、准入/发布、读取、血缘、完整性和回滚证据齐备时发布可追溯的读取切流资格。资格 SHALL 绑定验证版本及覆盖范围；任一关键证据过期、失效或缺失时 SHALL 撤销或拒绝资格。Phase F 的读取切流入口 SHALL 依据该资格逐路径判定，不得用单一总开关覆盖未通过路径。本 change 仅签发资格，不执行 Phase F 的实际切流。

用户确认的唯一来源可选例外为 `sec_edgar_filing_body` / `sec_filing_documents`：覆盖清单与资格结果 SHALL 区分来源失败事实和切流阻塞属性，并记录 optional / non-blocking、原因及策略版本。其他必需证据齐备且消费者空值/错误处理已验证时，系统 MUST NOT 仅因 SEC 原文缺失或未成功发布而拒绝读取资格；无需等待 SEC 真实抓取成功。来源本身 MUST NOT 因此标记 `verified` 或采集成功。本例外 SHALL NOT 放宽财务报表、电话会、SEC 索引、其他来源或审批/交易门禁。

#### Scenario: 只有 SEC 原文缺失

- **WHEN** SEC 原文失败或为空，但消费者其他必需证据、回滚证据及可选输入空值/错误处理均通过
- **THEN** 资格 SHALL 为 `eligible` 并携带已接受的 SEC 非阻塞缺口及原始失败引用，不得要求先恢复 SEC 抓取；资格签发本身 SHALL NOT 执行切流

#### Scenario: SEC 例外不能掩盖其他阻塞

- **WHEN** SEC 原文缺失已被接受，但财务报表、电话会或其他必需输入/证据不满足原有条件，或消费者无法处理空正文
- **THEN** 资格 SHALL 仍为 `ineligible` 并指出实际阻塞项，不得将 SEC 例外扩展为全局放行

#### Scenario: 数据域通过但消费者缺少回滚演练

- **WHEN** 数据域路径验证通过，而一个消费者没有成功回滚证据
- **THEN** 该消费者 SHALL 不具备读取切流资格，现有稳定路由 SHALL 保持不变

#### Scenario: 资格证据随后失效

- **WHEN** 来源、schema 或路由版本变化使此前验证证据不再适用
- **THEN** 对应资格 SHALL 失效，Phase F SHALL 阻止新的切流操作并报告具体缺口

### Requirement: 持久化来源覆盖必须以权威注册表和旧流程对照

覆盖清单 SHALL 将 `config/data/catalog.yaml` 作为机器装配入口，将 `config/data/structured.yaml` 与 `config/data/unstructured.yaml` 作为持久化来源定义的两个权威领域注册表，并 SHALL 对 Layer Analyst 的结构化、非结构化持久化输入逐项列出 source/dataset identity、直接消费者、旧采集入口/owner、目标刷新 owner、真实运行状态、持久化/发布证据和消费者读回证据。对照状态 SHALL 区分：(A) 已登记且旧流程有实际采集入口；(B) 已登记但旧定时流程未启用、无现行旧 owner 或仅有代码入口；(C) 旧消费者/采集代码使用持久化输入但 registry 缺项或来源身份不合格。仅有配置 cadence、schedule 声明、source rollout 状态或代码路径 MUST NOT 被报告为实际采集成功。

#### Scenario: Agent 使用缺失于两个权威 registry 的持久化来源

- **WHEN** Layer Analyst 的持久化输入不能映射到 `structured.yaml` 或 `unstructured.yaml` 的唯一规范 source/dataset identity
- **THEN** 覆盖清单 SHALL 将其列为 C 类缺口，标注来源、消费者和缺失证据；对应路径 SHALL NOT 获得 `verified` 或读取切流资格

#### Scenario: 旧采集代码存在但定时任务未启用

- **WHEN** 旧 scheduler 代码包含某来源的采集路径，但部署配置或运行证据表明该定时任务未启用
- **THEN** 清单 SHALL 将其归为 B 类并明确标注“代码入口存在、当前无活跃定时 owner”，不得误报为运行中的旧采集 owner

#### Scenario: schedule 已声明但没有运行证据

- **WHEN** 数据源在 YAML 中声明 cadence 或 schedule，但没有可核验部署绑定及最近运行记录
- **THEN** 覆盖状态 SHALL 保持 `unbound` 或未验证状态，不得归为已完成采集或发布

### Requirement: Data 与目标角色的每条对接关系必须有独立契约

系统 SHALL 按 `docs/TARGET_WORKFLOW_DATAFLOW.md` §4 为 Layer、Information、Sector、Fundamental、Macro、Technical、Chief、Risk、Trader 与 Clerk 的每条数据输入维护独立 consumer contract，包含产品/API owner、schema/version、来源与 as-of/vintage、完整性/缺口语义、允许消费者、fallback、旧入口和验证证据。持久化研究输入 SHALL 只通过 Data Products；实时市场输入 SHALL 只通过 Runtime Data Gateway；组合、交易、绩效、决策与审批状态 SHALL 只通过 Internal State API 或受管决策/执行契约。外部研究文档 SHALL NOT 以 Workflow Memory 或底层表作为正式读取 API。

#### Scenario: Layer 只登记了部分目标输入

- **WHEN** Layer consumer contract 只覆盖策展知识或结构化产业数据，而没有覆盖目标要求的已准入文档/命题证据数据包
- **THEN** Layer 数据对接 SHALL 保持不完整且不得获得读取切流资格，直到 `HIER_DATA` 与 `DOC_DATA` 两条输入均通过验证

#### Scenario: Information 从 Workflow Memory 读取外部材料

- **WHEN** Information 的正式研究输入 API 指向 Workflow Memory、底层文档表或 Provider，而不是已准入的 Data Product
- **THEN** 该路径 SHALL 被标记为边界违规并阻断资格，且不得将历史兼容读取视为目标契约

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
