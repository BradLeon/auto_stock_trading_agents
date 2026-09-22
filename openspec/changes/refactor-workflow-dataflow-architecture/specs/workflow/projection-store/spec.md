## Purpose

为 Agent 分析产出提供统一、类型化、不可变且可追溯的 Workflow Memory 契约，并为 Chief 决策固定完整的研究快照。

## ADDED Requirements

### Requirement: 每个 Agent 输出必须是类型化 Task Projection

系统 SHALL 以统一 envelope 存储 Agent 输出，至少包含 projection ID、workflow run ID、agent role、scope、`as_of`、`valid_until`、schema version、input refs、data vintage refs、model version、prompt version、类型化 payload、content hash 和 status。Payload SHALL 经角色专用 schema 校验。

#### Scenario: 报告文本缺少可机读 payload

- **WHEN** Agent 只返回一段报告文本而没有通过角色 schema 校验的 payload
- **THEN** 系统 SHALL 将该任务标记为输出无效
- **AND** SHALL NOT 将该记录作为可供下游复用的 Task Projection

### Requirement: Task Projection 必须不可变且内容可校验

一旦投影进入成功终态，其 payload、输入引用和版本字段 SHALL NOT 被就地覆盖。更新 SHALL 创建新 projection ID 和 content hash。

#### Scenario: 迟到文档更新分析

- **WHEN** 一份新电话会文本使已发布的 Fundamental 结论需要更新
- **THEN** 系统 SHALL 创建引用新文档版本的新 Task Projection
- **AND** 旧投影的 payload 和 content hash SHALL 保持不变

### Requirement: 投影可复用性必须可确定评估

系统 SHALL 使用 role、scope、schema compatibility、`valid_until`、input refs 和 data vintage refs 判定投影是否可复用，并 SHALL 返回结构化的可复用或失效原因。

#### Scenario: schema 版本不兼容

- **WHEN** 下游任务要求的 payload schema 与现有投影版本不兼容
- **THEN** 该投影 SHALL 被判定为不可复用
- **AND** 评估结果 SHALL 显式记录 schema incompatibility

### Requirement: Agent 观点必须与共享事实隔离

Task Projection SHALL 存储在 Workflow Memory 中，SHALL NOT 被发布成数据平台的中性共享事实。共享数据只能通过 lineage reference 被投影引用。

#### Scenario: Agent 输出包含主观影响判断

- **WHEN** InformationBrief 对一条新闻给出主观影响候选
- **THEN** 该判断 SHALL 只存入 Workflow Memory
- **AND** SHALL NOT 回写为 Data Platform 的共享事实

### Requirement: Chief 必须使用固定的研究快照

进入交易决策周期前，系统 SHALL 创建不可变的 research snapshot，列出每个被要求角色的 projection ID、content hash、as-of 和新鲜度。同一 cycle 的修订循环 SHALL NOT 静默替换研究投影。

#### Scenario: Loop 期间某个研究投影被新版本取代

- **WHEN** Chief—Risk Loop 正在运行且某个分析师发布了更新投影
- **THEN** 当前 cycle SHALL 继续引用原 research snapshot，或被显式标记为 `superseded`
- **AND** SHALL NOT 在现有 revision 中偷换新投影
