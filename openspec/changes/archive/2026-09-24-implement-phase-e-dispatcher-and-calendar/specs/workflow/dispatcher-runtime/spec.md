## Purpose

让手动、定时和事件触发的分析使用同一个依赖感知执行入口，明确记录每个任务的输入、结果和缺口，并在研究输入不完整时阻断组合决策。

## ADDED Requirements

### Requirement: Dispatcher 必须验证任务注册和运行范围

Dispatcher SHALL 只接受已登记任务，启动前 SHALL 拒绝未知任务、缺失依赖、循环依赖和未声明的跨分析师观点依赖。任务注册 SHALL 声明角色、scope、输出 schema、超时、重试、资源组和支持的触发模式。

#### Scenario: 注册表存在循环

- **WHEN** 两个任务互相声明为依赖
- **THEN** Dispatcher SHALL 在任何 Agent 启动前拒绝该注册表
- **AND** 错误 SHALL 列出形成循环的任务 ID

#### Scenario: 声明未授权依赖

- **WHEN** Sector 任务声明读取 Macro 投影
- **THEN** Dispatcher SHALL 拒绝该任务注册或运行
- **AND** 合法的跨分析师依赖 SHALL 只包括 Layer → Sector、Information → Fundamental

### Requirement: Dispatcher 必须按请求范围展开依赖

Dispatcher SHALL 支持单任务、依赖子流程和完整分析。单任务或子流程只展开其声明的上游；分析型请求 SHALL 默认在投影发布后终结。要求进入决策周期的请求 SHALL 在启动前固定按 scope 确定的必需分析类别；未明确要求完整决策输入的局部请求 SHALL NOT 进入 Chief。

#### Scenario: 手动请求 Sector

- **WHEN** 用户请求 AI 硬件 Sector 分析，且不存在可复用的 Layer 投影
- **THEN** Dispatcher SHALL 运行对应 Layer 后再运行 Sector
- **AND** SHALL NOT 因此运行 Macro、Information 或 Technical

#### Scenario: 局部请求附带决策标志

- **WHEN** 用户只请求 Macro，却设置进入决策周期
- **THEN** Dispatcher SHALL 拒绝该请求并列明缺少的必需分析类别
- **AND** SHALL NOT 用已有旧投影静默补成一个交易周期

### Requirement: 无依赖任务必须有界并发且失败隔离

Dispatcher SHALL 在资源组限制内并发运行已就绪任务，只在上游成功或可复用时启动其下游。一个任务失败或超时 SHALL 阻断其下游，SHALL NOT 取消无关分支。

#### Scenario: Information 运行失败

- **WHEN** 完整分析中的 Information 失败，而 Layer、Macro 和 Technical 已启动
- **THEN** Fundamental SHALL 标为被上游阻断
- **AND** 其余无依赖分支 SHALL 继续到终态

#### Scenario: 同一数据库写资源受限

- **WHEN** 多个已就绪任务属于同一受限写资源组
- **THEN** Dispatcher SHALL 遵守该组并发上限
- **AND** 等待资源的任务 SHALL 保留可观察状态，不得被误报为成功

### Requirement: 投影复用必须记录可验证的依据

复用判断 SHALL 同时检查角色、scope、payload schema、`as_of`/`valid_until`、必需输入投影、关键数据 vintage 和终态。每次复用或拒绝复用 SHALL 持久记录候选投影、判断时点和原因；没有可验证 vintage 的输入 SHALL 不得被假定为未变化。

#### Scenario: 依赖投影未过期但数据已修订

- **WHEN** Layer 投影的有效期未到，但其关键数据 vintage 发生变化
- **THEN** Dispatcher SHALL 重新运行 Layer 或将 Sector 标为输入不完整
- **AND** SHALL 记录失效原因为数据版本不匹配

#### Scenario: 依赖投影可以复用

- **WHEN** Information 投影的全部兼容性条件成立
- **THEN** Fundamental SHALL 接收该投影的精确 ID 和 hash
- **AND** 运行记录 SHALL 标记该依赖为复用，而非伪装为本轮 Agent 新产出

### Requirement: 运行和任务结果必须持久且可恢复

每次调度 SHALL 记录运行请求、固定任务计划、各任务 attempt、输入引用、开始和结束时间、投影引用、结构化失败原因及终态。重启后 SHALL 从已持久化状态恢复或安全重试未完成任务，SHALL NOT 重跑已成功且结果仍有效的任务。

#### Scenario: Agent 发布投影后进程崩溃

- **WHEN** Agent 已发布成功投影，但 Dispatcher 尚未记录任务终态时进程崩溃
- **THEN** 恢复过程 SHALL 按稳定任务身份找到既有投影并补齐运行结果
- **AND** SHALL NOT 生成第二个逻辑产出

### Requirement: Chief 门禁必须以固定必需类别复查完整性

请求进入决策周期时，Dispatcher SHALL 在 Chief 启动前对固定的必需类别重新核验成功状态、scope、schema、输入血缘、新鲜度和投影 hash。AI 硬件完整流程 SHALL 包含 Layer、Information、Sector、Fundamental、Macro、Technical 六类；Fundamental 例行或事件模式 SHALL 按请求语义选择其一，不得要求两者同时存在。任何缺口 SHALL 形成 `incomplete` 结果和可读缺口报告，SHALL NOT 创建 decision cycle。

#### Scenario: 等待过程中 Technical 过期

- **WHEN** 全部任务曾成功，但 Technical 投影在 Chief 门禁时已过期
- **THEN** Dispatcher SHALL 标记该运行 `incomplete` 并列明 Technical 失效
- **AND** SHALL NOT 调用 Chief、Risk 或 Trader

#### Scenario: 非 AI 硬件范围

- **WHEN** 完整流程的范围不支持 Layer Analyst
- **THEN** 必需类别 SHALL 来自该范围明确发布的决策需求清单
- **AND** 系统 SHALL NOT 伪造 Layer 输出或因任务未登记而静默缩减必需类别

### Requirement: 旧分析入口迁移必须保持单一调度所有者

每个已迁移的分析 workflow SHALL 由 Dispatcher 独占执行所有权；旧 CLI 和 scheduler 入口 SHALL 转发到相同运行契约，或在兼容窗口内明确保持旧所有者。影子运行 SHALL 禁止真实券商写入，并 SHALL 能对比新旧任务身份、输入与结果。

#### Scenario: 同一日度分析被新旧 scheduler 同时唤醒

- **WHEN** 一个 workflow ID 已切给 Dispatcher，但旧日度作业也被唤醒
- **THEN** 旧入口 SHALL 不再直接调用该 Agent
- **AND** 同一逻辑窗口 SHALL 只有一个执行所有者
