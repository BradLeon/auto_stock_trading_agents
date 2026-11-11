## ADDED Requirements

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

## MODIFIED Requirements

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

## REMOVED Requirements

### Requirement: Phase F 读取资格必须按数据域与消费者 fail closed

**Reason**: 逐数据域、逐消费者的读取切流资格是迁移期机制。研究读路由已固定，交易路径切换后不再有待切换的消费者；资格与代码指纹绑定，任何改动都会让资格作废，只增加维护负担，不再提供保护。
**Migration**: 读取保护改由「消费者必须按声明的数据产品与业务 scope 读取」承担：读取前检查数据产品，不合格就显式失败。SEC 原文的非阻塞处理并入数据产品检查规则，R5 执行前核对（tasks.md 10.1）。历史资格记录保留只读。

### Requirement: 已有资格证据受控登记为审计记录，切流前置为消费者健康检查

**Reason**: 资格账本只为迁移期的切流前置而存在。切流全部完成后，它既不再是前置条件，也没有消费方。
**Migration**: 资格账本停止写入，保留只读，作为历史审计记录。消费者健康检查改为读取前的数据产品检查，见上文新增要求。

### Requirement: 每个直接消费者必须有独立读取契约和回退证据

**Reason**: 这条要求的“切流控制、稳定回退路由、读取切流资格”都是迁移期概念，读路由表与资格账本已在 R5d 删除。
**Migration**: 每个消费者的独立读取契约由「Data 与目标角色的每条对接关系必须有独立契约」承担；读取前检查与显式失败由「消费者必须按声明的数据产品与业务 scope 读取」承担。

### Requirement: 持久化来源覆盖必须以权威注册表和旧流程对照

**Reason**: 与旧采集流程逐项对照（A/B/C 分类）只服务于迁移期的切流资格判断；旧调度流程已退役。
**Migration**: 权威注册表与“只有 cadence 配置不等于已刷新”的规则由 `data/refresh-orchestration` 的「持久化数据来源必须显式登记在权威注册表」与「更新策略必须绑定可执行的刷新所有者」承担。

### Requirement: A 行情契约升级必须同步治理清单并重验受影响资格

**Reason**: 这是一次性的契约升级与资格重验；资格与指纹绑定已删除，没有可重验的资格。
**Migration**: Trader 只读执行报价的限制由覆盖清单中的 Trader 契约与「消费者必须按声明的数据产品与业务 scope 读取」承担；执行报价规则见 `docs/ARCHITECTURE.md` §10.4。

### Requirement: 十个角色的实际接入必须以运行记录核验

**Reason**: 这是迁移期的接入核验，其工具（`intake`、`intake_verification`、`analysis_verification`）已在 R5c 删除。
**Migration**: 完整决策请求固定必需类别与 Fundamental 模式的规则由 `workflow/dispatcher-runtime` 与 `decision/research-snapshot` 承担；Provider 旁路由架构守卫与「消费者必须按声明的数据产品与业务 scope 读取」承担。

### Requirement: 决策与执行链的接入验证必须无券商写权限

**Reason**: 这是迁移期的接入验证方式，相应验收工具已在 R5c 删除。
**Migration**: 隔离模拟提交与生产下单禁令的分离由 `execution/authorization-gate` 的「隔离模拟执行必须与生产下单禁令分离」承担；账本幂等由 `execution/clerk-ledger` 承担。

### Requirement: 实际缺口必须形成逐消费者处置

**Reason**: 逐消费者缺口处置只服务于迁移期资格认定，处置工具 `consumer_disposition` 已在 R5c 删除。
**Migration**: 输入缺口在读取时显式标记为 partial 或失败（`consumer_api.read_input` 的状态），不再另做处置报告。

### Requirement: 隔离接入验收不得以生产资格为前置

**Reason**: 生产读取资格已不存在，这条要求失去对象；隔离接入验收入口 `isolated_entry` 已在 R5c 删除。
**Migration**: 隔离运行不得产生生产副作用由 `isolation.assert_write_destination` 与 `execution/authorization-gate` 的隔离模拟要求承担。

### Requirement: 持久部署批准不得替代每次输入有效性

**Reason**: “持久部署批准”是读窗口与联合授权机制的一部分，已在 R5d 删除。
**Migration**: 每次运行重查输入有效性的要求原样保留为新增的「每次运行必须重查实际输入的有效性与新鲜度」。
