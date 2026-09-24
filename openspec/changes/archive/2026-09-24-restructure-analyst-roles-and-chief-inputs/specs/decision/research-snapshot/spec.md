## Purpose

主理人是唯一有权汇总全部分析师观点的决策者。它开始决策时必须先固定一份 research snapshot，把本轮消费的每一类分析投影钉死在具体的投影标识与内容哈希上；六类不齐备就不得进入决策周期，更不得自动交易。缺少分析时可以发布缺口报告，但不可以把缺口当作「没有意见」继续下单。

## ADDED Requirements

### Requirement: 决策前必须固定 research snapshot

主理人在开始决策时 SHALL 固定一份 `research_snapshot`，其中列出每个被要求角色的投影标识、内容哈希、as-of 与新鲜度状态。快照 SHALL 在决策周期内保持不变，SHALL NOT 随后续分析到达而原地更新。

#### Scenario: 快照逐项记录来源

- **WHEN** 主理人组装决策上下文
- **THEN** SHALL 为每一类被要求的分析记录其投影标识、内容哈希与 as-of
- **AND** SHALL 记录该投影的新鲜度状态（新鲜 / 过期 / 缺失）

#### Scenario: 循环内不重跑分析师

- **WHEN** 主理人与风控进入多轮修订
- **THEN** 各轮 SHALL 复用同一份快照
- **AND** SHALL NOT 在修订轮次中重跑分析师或偷换研究输入

### Requirement: 六类分析齐备才进入决策周期

被要求的分析类别为六类：层级分析、信息简报、行业配置、基本面、宏观评审、技术面评审。其中基本面以「例行更新或事件评审二者之一满足」计入，两种模式 SHALL NOT 被同时要求。

六类齐备且均未过期时决策周期 SHALL 进入；任一缺失、过期或失败时 SHALL 判定为不完整。

#### Scenario: 六类齐备

- **WHEN** 六类分析均存在新鲜且作用域相容的投影
- **THEN** 决策周期 SHALL 进入并携带完整快照
- **AND** 快照 SHALL 标记为完整

#### Scenario: 基本面两种模式二选一

- **WHEN** 某周期只有事件评审而没有例行更新（或反之）
- **THEN** 基本面 SHALL 判定为满足
- **AND** SHALL NOT 因另一种模式缺失而判为不完整

#### Scenario: 某类分析缺失

- **WHEN** 六类中任一类别无可用投影
- **THEN** 快照 SHALL 标记不完整并列出缺失类别
- **AND** 决策周期 SHALL NOT 进入

### Requirement: 不完整运行必须阻断自动交易并可发布缺口报告

完整流程中任一被要求的分析缺失、过期或失败时，运行状态 SHALL 为 `incomplete`，且 SHALL NOT 进入主理人决策与后续审批、执行路径。系统 SHALL 可发布缺口报告说明缺什么与影响范围。

#### Scenario: 分析任务失败

- **WHEN** 某个被要求的分析任务失败
- **THEN** 运行 SHALL 判为不完整并阻止进入决策周期
- **AND** SHALL NOT 因该失败而取消与之无关的其他分析任务

#### Scenario: 发布缺口报告

- **WHEN** 一次运行被判为不完整
- **THEN** SHALL 产出一份缺口报告，列出缺失 / 过期 / 失败的类别与原因
- **AND** 该报告 SHALL NOT 被当作可决策的输入进入主理人

#### Scenario: 阻断先于任何写操作

- **WHEN** 系统判定快照不完整
- **THEN** SHALL 在写入决策周期之前拒绝
- **AND** SHALL NOT 产生一条「已创建但无法决策」的空周期记录

### Requirement: 主理人从投影读取而非直读旧表

主理人读取六类分析时 SHALL 经 `task_projection_envelopes` 读取投影，SHALL NOT 直接读取旧的分析结果表或报告文本。某类投影取不到时 SHALL 判定为缺口，SHALL NOT 静默降级为空上下文继续决策。

#### Scenario: 上下文来自投影

- **WHEN** 主理人组装某类分析的上下文
- **THEN** SHALL 读取该类最新可用投影并记录其标识
- **AND** 组装结果 SHALL 可追溯到具体的投影与内容哈希

#### Scenario: 取不到投影时不静默降级

- **WHEN** 某类分析无可用投影
- **THEN** 组装 SHALL 记录该类为缺失
- **AND** SHALL NOT 以空字符串或省略该章节的方式让决策照常进行

### Requirement: 快照失效时周期转为 superseded

研究快照本身失效（关键数据 vintage 变化或投影被撤销）时，当前决策周期 SHALL 转为 `superseded` 或转人工处理，SHALL NOT 在原 revision 中偷换研究输入。

#### Scenario: 关键数据 vintage 变化

- **WHEN** 快照中某投影所依赖的数据 vintage 在决策过程中发生变化
- **THEN** 该周期 SHALL 被标记失效
- **AND** 继续决策 SHALL 需要基于新快照重新创建修订

#### Scenario: 投影被撤销

- **WHEN** 快照引用的某条投影被标记为撤销或失败
- **THEN** 当前周期 SHALL 转为失效或人工处理
- **AND** SHALL NOT 以另一条投影替换后继续沿用原 revision
