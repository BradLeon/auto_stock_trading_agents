# Event Calendar Specification

## Purpose

以受治理、可版本化的持久事件日历发现分析触发时机，保留来源和人工修订历史，并明确区分计划日期与实际已发布的研究材料。

## Requirements

### Requirement: 日历必须自动刷新首期事件并保留来源状态

Calendar SHALL 定期发现公司财报、FOMC 会议/决议/发布会、CPI、PCE、非农、GDP 及人工自定义事件。每个候选 SHALL 保留来源 ID、原生标识、发现/获取时刻、原始时间与时区、规范化时间、实体或统计期、session、质量状态和来源血缘。来源失败 SHALL 显式标记刷新失败，不得将空响应解释为事件被取消。

#### Scenario: 财报来源暂时不可用

- **WHEN** 一个已发布财报计划的来源本次刷新超时
- **THEN** Calendar SHALL 保留最后已发布版本并报告该来源刷新失败与新鲜度
- **AND** SHALL NOT 自动取消该事件

### Requirement: 同一事件必须保持稳定身份和追加式版本历史

`event_id` SHALL 由事件类型、实体/统计期和子事件身份等不随计划日期变化的要素确定；计划时间、session、状态或关键触发语义变更 SHALL 创建新的 `event_version`。完全相同的重复来源记录 SHALL 不创建新版本。每个版本 SHALL 可追溯到原始候选与变更原因。

#### Scenario: 财报从周二改到周四

- **WHEN** 同一实体和报告期的财报计划日期修订
- **THEN** Calendar SHALL 保留同一 `event_id` 并发布新版本
- **AND** 原版本及其来源 SHALL 仍可查询

#### Scenario: FOMC 决议与发布会

- **WHEN** 同一会议包含决议和发布会两个时点
- **THEN** Calendar SHALL 将两者表示为可分别调度的子事件
- **AND** SHALL NOT 因共享会议日期而合并成一个触发

### Requirement: 来源冲突和低质量候选必须有显式状态

无法可靠判定为同一事件的候选或对关键时间存在未裁决冲突时，Calendar SHALL 保留候选和冲突状态，SHALL NOT 静默选择一个时点触发投资分析。只有通过身份、来源和时间校验的版本可发布为可触发事件。

#### Scenario: 两个财报来源相差六天

- **WHEN** 两个来源对同一可能报告期给出相差六天且无法证实的日期
- **THEN** Calendar SHALL 暴露冲突及两条来源记录
- **AND** SHALL NOT 以其中较早或较晚日期自动创建可执行事件触发

### Requirement: 人工覆盖必须可审计且不覆盖原始来源

`config/events.yaml` 或等价人工入口 SHALL 只提供显式 override 和自定义事件。覆盖自动事件 SHALL 记录 actor、原因、来源依据、目标事件和旧版本；人工取消/改期 SHALL 创建新版本。配置撤销 SHALL 保留历史并按当前来源候选重新计算发布结果。

#### Scenario: 人工确认财报改期

- **WHEN** 操作者提交带依据的财报新日期
- **THEN** Calendar SHALL 发布标记为人工覆盖的新版本
- **AND** 原始来源版本、操作者和理由 SHALL 可查询

### Requirement: 时间与交易时段语义必须保真

Calendar SHALL 保存来源本地时区、原始表达和 UTC 时刻；仅有日期或 `unknown` session 时 SHALL 保留不确定性，不得编造精确发布时间。夏令时转换、休市和跨时区公司财报 SHALL 按 workflow 的窗口策略处理，并将调度选择写入触发记录。

#### Scenario: 海外公司财报只有日期

- **WHEN** 来源只提供报告日期，没有可靠发布时刻
- **THEN** Calendar SHALL 保存日期和 `unknown` session
- **AND** 事件型分析 SHALL 等待已准入的实际发布材料，不得按猜测时刻产出 Surprise Scorecard

### Requirement: 计划事件与已发生的材料发布必须区分

计划日期可触发准备或检查任务；财报后 PEAD 与宏观发布后的分析 SHALL 以已准入的实际 release、公告或数据 vintage 为输入条件。仅到达计划时刻 SHALL NOT 被当作实际值已公布。迟到材料 SHALL 依其新的文档/数据版本产生可追踪的后续运行，不得覆写早期结果。

#### Scenario: 财报日到达但公司尚未披露

- **WHEN** Calendar 到达 AMC 计划窗口，但 Data Platform 尚无该期已准入财报
- **THEN** 系统 SHALL 记录待发布或待重试状态
- **AND** SHALL NOT 启动使用不存在 actuals 的事件复盘

### Requirement: 日历只发布元数据并可供 Dispatcher 读取

Calendar Data Product SHALL 提供当前有效事件及指定 `as_of` 的版本视图，包含身份、时间、状态、范围、来源和触发引用。它 SHALL NOT 包含分析观点、交易建议或可执行订单。事件到 workflow 的映射 SHALL 是显式配置且可校验的。

#### Scenario: FOMC 决议触发 Macro

- **WHEN** 已发布 FOMC 决议事件映射到 Macro workflow
- **THEN** Dispatcher SHALL 只收到事件身份、版本、计划时点与数据定位引用
- **AND** Macro SHALL 通过正常 Data Product 获取发布内容
