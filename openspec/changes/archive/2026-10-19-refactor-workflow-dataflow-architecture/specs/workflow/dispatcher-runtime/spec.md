## Purpose

定义所有分析 Workflow 的统一调度行为，使手动、定时和事件任务能够按显式依赖并发执行，并在输入不完整时阻止自动交易。

## ADDED Requirements

### Requirement: Workflow 必须通过统一 Dispatcher 调度

系统 SHALL 通过同一 Dispatcher 接收手动、定时和事件触发，并允许调用单 Agent、依赖子流程或完整流程。分析型运行 SHALL 默认在产生投影后结束，只有显式请求组合决策时才可进入 Chief。

#### Scenario: 手动运行单个无依赖分析师

- **WHEN** 用户手动请求只运行 Macro Analyst
- **THEN** Dispatcher SHALL 只调度 Macro Analyst 及其必需数据读取
- **AND** 运行 SHALL NOT 自动进入 Chief、Risk 或 Trader

#### Scenario: 完整流程显式进入决策

- **WHEN** 调用方请求完整分析并显式要求组合决策
- **THEN** Dispatcher SHALL 等待本次请求中的必要分析任务全部进入成功终态
- **AND** 完整性门禁通过后才可启动 Chief

### Requirement: Dispatcher 必须只展开显式依赖

分析师之间的唯一合法依赖 SHALL 是 Layer → Sector 和 Information → Fundamental。Dispatcher SHALL 对有依赖的手动任务自动补齐上游，并 SHALL 拒绝未声明的跨分析师依赖。

#### Scenario: 单独运行 Sector

- **WHEN** 用户请求 Sector 且没有可复用的 Layer 投影
- **THEN** Dispatcher SHALL 先调度 Layer，再将其成功投影交给 Sector
- **AND** Dispatcher SHALL NOT 因此调度 Macro、Information、Fundamental 或 Technical

#### Scenario: 检测到未声明观点依赖

- **WHEN** 任务声明或运行时请求读取非 Layer → Sector、Information → Fundamental 的分析师投影
- **THEN** Dispatcher SHALL 以显式依赖违规终止该任务
- **AND** SHALL 保留违规角色、投影和任务引用

### Requirement: 无依赖任务必须可并发且失败隔离

Layer、Information、Macro 和 Technical SHALL 可在资源策略允许时并发执行。一个任务失败 SHALL NOT 取消与它无依赖关系的任务。

#### Scenario: Information 失败不中断其他独立分析

- **WHEN** 完整运行中 Information 失败而 Layer、Macro 和 Technical 仍在运行
- **THEN** 三个无关任务 SHALL 继续到各自终态
- **AND** Fundamental SHALL 因依赖失败而不得启动

### Requirement: Dispatcher 必须按兼容性和新鲜度复用投影

Dispatcher SHALL 只复用 scope、schema、关键输入引用、data vintage 和新鲜度均满足任务策略的投影。任一条件不满足时 SHALL 重新运行该依赖，不得静默复用。

#### Scenario: data vintage 改变使缓存失效

- **WHEN** 现有 Layer 投影未超过 `valid_until`，但其关键产业链数据 vintage 已被修订
- **THEN** Dispatcher SHALL 将该投影视为不兼容并重跑 Layer
- **AND** SHALL 在运行记录中保留未复用的原因

### Requirement: 完整性缺口必须阻断自动交易

完整流程中任一被要求的分析输出失败、缺失、过期或 schema 不兼容时，系统 SHALL 将运行标记为 `incomplete`，SHALL 允许发布缺口报告，但 SHALL NOT 创建自动交易决策周期。

#### Scenario: Technical 输出过期

- **WHEN** 完整流程到达 Chief 门禁时 Technical 投影已过期
- **THEN** 系统 SHALL 发布列明 Technical 缺口的不完整报告
- **AND** SHALL NOT 创建 decision cycle 或调用 Trader

### Requirement: 触发和重试必须幂等

每个逻辑任务 SHALL 有稳定幂等键。相同触发和任务定义的重复投递或恢复 SHALL 返回既有结果或继续未完成运行，不得创建第二个逻辑任务。

#### Scenario: 调度器重启后重放同一任务

- **WHEN** 调度器重启并重放一个已成功任务的同一幂等键
- **THEN** Dispatcher SHALL 返回既有终态和投影引用
- **AND** SHALL NOT 重新调用该 Agent
