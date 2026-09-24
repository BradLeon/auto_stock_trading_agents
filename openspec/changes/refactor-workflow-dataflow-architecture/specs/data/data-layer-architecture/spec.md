## ADDED Requirements

### Requirement: 中性证据的写入必须完全归属数据平台

来自文档、观察窗口、产业链来源、文章和 CLI 导入的中性证据事实 SHALL 通过数据平台写入接口持久化，SHALL NOT 写入 Workflow Memory 中已退役的数据表。写入 SHALL 保留原始文档版本、来源血缘和准入结果。

#### Scenario: 观察名单从电话会抽取证据

- **WHEN** 观察工作流从已准入文档生成中性证据观测
- **THEN** 观测、事实、投影和失败记录 SHALL 写入数据平台所有的证据存储
- **AND** SHALL NOT 尝试恢复或写入 Workflow Memory 中的旧证据表

#### Scenario: 数据写入失败

- **WHEN** 数据平台证据写入接口不可用或拒绝数据
- **THEN** 调用方 SHALL 获得显式失败和 reason code
- **AND** SHALL NOT 静默回退到旧 Workflow Memory 表

### Requirement: Workflow Memory 必须只保存观点和数据血缘引用

Workflow Memory SHALL 保存 Agent 投影、决策、审批和运行状态，并使用不可变 ID 引用数据平台的文档、事实、观测和 vintage。Workflow Memory SHALL NOT 复制或写入一份可被当作共享事实真相的数据副本。

#### Scenario: FundamentalExpectationUpdate 引用数据 vintage

- **WHEN** Fundamental 发布新预期投影
- **THEN** Workflow Memory SHALL 保存该观点 payload 和依赖的 data vintage IDs
- **AND** 被引用的原始数据 SHALL 仍由数据平台提供和解释

### Requirement: 数据写路径迁移必须可对账与回滚

中性证据写路径切换前 SHALL 验证新旧语义的实体、时间、文档版本、来源和幂等键映射。切换过程 SHALL 保留可恢复导出和明确回滚步骤，但 SHALL NOT 通过恢复已退役的 Workflow Memory 数据表来回滚边界决定。

#### Scenario: 新写路径的幂等键与旧数据不一致

- **WHEN** 切换前对账发现同一证据在新存储中会生成不同幂等身份
- **THEN** 切换 SHALL 被阻断
- **AND** SHALL 在修正映射并重新对账后才可重试

### Requirement: 目标 Dataflow 的关键路径必须逐项可验证

系统 SHALL 对持久化结构化、持久化非结构化、runtime 即时输入和内部交易状态分别明确来源、更新或查询责任、准入或完整性状态、规范存储或网关、面向消费者的读取入口和血缘。已存在的数据平台能力 SHALL 以可复现的契约或集成证据接收；任何没有证据的目标节点或关键连线 SHALL 标记为未验证或缺口，不得因旧 change 已归档而宣称整条数据流完成。

#### Scenario: 核验一项已实现的公司研究数据产品

- **WHEN** 数据产品被标记为可供 Agent 使用
- **THEN** 验收记录 SHALL 能追溯其来源、原始资产或观测版本、准入结果、as-of 语义和产品读取结果
- **AND** 无法重现的环节 SHALL 显式保持未验证状态

#### Scenario: 目标图的一条读取路径尚无实现

- **WHEN** 某类目标数据或某个消费者缺少受治理的读取入口
- **THEN** 系统 SHALL 暴露该数据域或消费者的缺口与 owner
- **AND** SHALL NOT 以其他数据域已通过或兼容入口存在作为该路径验收通过的替代

### Requirement: 数据更新与按需读取必须保持职责分离

持久化数据的发现、刷新、准入和发布 SHALL 由数据平台负责，并显式区分未发布、来源失败、准入拒绝与数据陈旧；Agent/Workflow SHALL 按需读取已发布 Data Products。实时行情与期权等输入 SHALL 经 Runtime Data Gateway 即时查询，默认不发布为持久化共享事实。Clerk 和券商来源的内部交易状态 SHALL 经 Internal State API 提供，不得混入外部研究事实产品。

#### Scenario: 持久来源刷新失败

- **WHEN** 自动刷新未取得新的可准入材料
- **THEN** 数据平台 SHALL 记录来源失败或陈旧状态并保留可追溯的上一已发布版本
- **AND** Agent SHALL NOT 通过直接调用该 Provider 来伪装数据产品仍然新鲜

#### Scenario: Agent 同时需要市场报价和历史交易

- **WHEN** Agent 读取当前价格及组合交易历史
- **THEN** 价格 SHALL 来自 runtime 读取边界，交易历史 SHALL 来自带 as-of 与完整性状态的 Internal State API
- **AND** 两者 SHALL NOT 被当作同一种持久化外部研究观测

### Requirement: 数据读取切流必须按数据域和消费者独立设门禁

系统 SHALL 在每个数据域和直接消费者的新读取路径启用前，验证持久化域的唯一规范 writer 或 runtime 域的查询 owner、来源与 vintage 血缘（适用时）、发布质量或即时完整性、旧新读数差异归因、故障行为和可演练回滚。未通过的域或消费者 SHALL 保持其现有稳定路由，不得因 Dispatcher、Calendar 或其他数据域已切换而整体放行。

#### Scenario: Calendar 已验收而公司文档产品仍有缺口

- **WHEN** 事件日历可正常发布，但公司文档产品无法证明正文完整性或版本血缘
- **THEN** 系统 SHALL 允许 Calendar 所属路由独立进入影子或切流验收
- **AND** 公司文档消费者 SHALL 保持未通过状态，不能随 Phase F 的整体开关被静默切换

#### Scenario: 新读取路径回滚

- **WHEN** 一个已切换的数据域或消费者出现可复现的平台回归
- **THEN** 系统 SHALL 支持将该范围的读取路由恢复到登记的稳定路径并保留新路径审计证据
- **AND** SHALL NOT 通过重建已退役的 Workflow Memory 共享事实表或让 Agent 直连 Provider 来回滚
