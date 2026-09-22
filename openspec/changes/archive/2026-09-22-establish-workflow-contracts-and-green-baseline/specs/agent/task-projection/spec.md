## Purpose

为所有分析角色定义统一的产出外壳，使 Workflow memory 中的每条 Agent 产出都带有完整的来源、时点、依赖与版本信息，可被机器解析、可被缓存与幂等复用、可被审计。含观点的结论与被引用的数据事实通过该外壳分离。

## ADDED Requirements

### Requirement: 所有 Agent 产出必须使用统一投影外壳

每个 Agent 的产出 SHALL 以统一外壳写入 Workflow memory 的 `task_projection_envelopes` 表，至少包含：投影标识、所属运行标识、角色标识、作用域、`as_of` 时点、有效期、schema 版本、输入引用、数据 vintage 引用、模型版本、提示词版本、类型化 payload、内容哈希与状态。

#### Scenario: 分析角色产出一条投影

- **WHEN** 任一分析角色完成一次分析并写出结果
- **THEN** 该结果 SHALL 以统一外壳写入 `task_projection_envelopes`
- **AND** 外壳 SHALL 填满角色、作用域、`as_of`、输入引用与数据 vintage 引用

#### Scenario: 作用域表达标的与范围

- **WHEN** 一个投影只覆盖某个标的、行业、产业层或整个组合
- **THEN** 该范围 SHALL 在作用域字段中显式表达
- **AND** 消费者 SHALL 能据此判断该投影是否适用于自己的查询范围

### Requirement: 统一外壳使用独立表，不复用既有投影表

统一外壳 SHALL 由独立表 `task_projection_envelopes` 承载。既有 `task_projections` 表及其读取方 SHALL 保持可用且结果不变，SHALL NOT 被改造为统一外壳的承载表。

#### Scenario: 既有投影表的读取方不受影响

- **WHEN** 既有读取方（投影血缘解析、证据事实投影查询）读取 `task_projections`
- **THEN** 其结果 SHALL 与本能力引入前一致
- **AND** 统一外壳的写入 SHALL NOT 改变既有表的列语义

#### Scenario: 两表并存期旧表不再被填充

- **WHEN** 新写路径产出一条投影
- **THEN** 该投影 SHALL 只写入 `task_projection_envelopes`
- **AND** `task_projections` SHALL 在新写路径中保持零写入，从而具备下线判定条件

### Requirement: payload 必须经角色专用 schema 校验

投影 payload SHALL 由角色专用 schema 校验后才写入。系统 SHALL NOT 允许只保存一段无法机器解析的自由文本作为 Agent 的唯一产出。校验失败时该次产出 SHALL 被判定为失败，SHALL NOT 以自由文本降级写入。

#### Scenario: payload 不符合角色 schema

- **WHEN** 一个角色返回的 payload 缺少该角色 schema 要求的字段或取值越界
- **THEN** 该校验 SHALL 失败并指出缺失或越界的字段
- **AND** 该次产出 SHALL NOT 被写成其他角色的合法输出

#### Scenario: 模型返回了可解析但结构错误的内容

- **WHEN** 模型把列表字段返回为字符串等结构错误形式
- **THEN** 系统 SHALL 在写入前完成可接受的规范化
- **AND** 规范化无法挽救时该次产出 SHALL 判定为失败而不得写入残缺投影

### Requirement: 投影必须记录输入引用与数据 vintage

投影 SHALL 记录其所依赖的上游投影标识，以及所消费数据产品的 `as_of` 或 vintage 引用。系统 SHALL 能据此判断两个投影之间的依赖关系与所用数据的时点。

#### Scenario: 复用未过期的上游投影

- **WHEN** 下游角色需要一个上游投影，且存在未过期、作用域相容、schema 相容且关键数据 vintage 未变的投影
- **THEN** 系统 SHALL 依据输入引用判断该投影可被复用
- **AND** 复用 SHALL NOT 需要重新运行上游角色

#### Scenario: 上游数据 vintage 变化

- **WHEN** 下游角色所需的共享数据在产品层的 vintage 发生变化
- **THEN** 系统 SHALL 将既有投影的输入引用判定为已变化
- **AND** 该投影 SHALL NOT 被当作与变化后输入等价的产出复用

### Requirement: 内容哈希必须覆盖规范化 payload 与关键输入引用

投影 SHALL 计算内容哈希，其输入至少包含规范化后的 payload 与关键输入引用。该哈希 SHALL 用于缓存、幂等与审计，SHALL NOT 因无关的序列化差异而变化。

#### Scenario: 相同语义内容重复产出

- **WHEN** 同一角色在相同输入与相同数据 vintage 下再次产出一条语义相同的投影
- **THEN** 其内容哈希 SHALL 与上一次相同
- **AND** 系统 SHALL 据此识别重复并避免产生等价的多份产出

#### Scenario: 输入引用变化

- **WHEN** 同一角色产出的 payload 语义相同，但其引用的上游投影或数据 vintage 改变
- **THEN** 内容哈希 SHALL 随之改变
- **AND** 系统 SHALL NOT 将两者视为同一份产出
