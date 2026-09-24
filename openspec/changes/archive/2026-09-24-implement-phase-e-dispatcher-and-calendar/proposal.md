## Why

目标架构的 Phase E 要让分析任务可按依赖独立、并发、定时或由事件运行。当前 `runtime/scheduler.py` 仍以单 worker 和硬编码顺序组织分析，`config/events.yaml` 仍承担人工维护的事件日期；已有 `WorkflowRun*`、`TriggerContext`、Task Projection 和审批契约尚未接成统一的调度运行时。

本专项 change 细化 `docs/TARGET_WORKFLOW_DATAFLOW.md` §8、§13、§14.5 和 `refactor-workflow-dataflow-architecture` 的 Phase E，形成可独立实施、验收的 Dispatcher、Schedule Calendar 与 Trigger Ledger 方案。它复用现有契约和 Phase D 已发布的分析角色输出，不重新定义分析观点或交易审批。

## What Changes

- 建立可校验的任务注册表和 Dispatcher：支持手动单任务、自动补齐依赖的子流程、全量分析，以及显式选择进入决策周期；只允许 Layer → Sector、Information → Fundamental 两条跨分析师依赖。
- 以有界并发执行 ready 任务，持久记录 `workflow_runs`、`agent_runs` 和每次投影复用/失效原因；上游失败只阻断其下游，无关分析继续完成。完整性门禁在进入 Chief 前复查必需类别、scope、schema、输入血缘和新鲜度。
- 建立持久 Trigger Ledger：手动、cron 和事件触发统一归一为 `TriggerContext`；重复投递、崩溃恢复、misfire 补偿和多 worker 竞争只形成一个逻辑触发与任务执行。
- 在 Data Platform 发布版本化 `schedule_events` 与只读 Calendar Data Product；自动刷新财报、FOMC、CPI、PCE、非农和 GDP 计划，并把 `config/events.yaml` 转为可追踪的人工覆盖与自定义事件层。计划时间不等于数据已发布；事件型分析仍须核验已准入材料。
- 按 workflow ID 迁移现有分析定时入口与财报窗口，保留采集、Clerk 对账等各自领域的作业语义；建立新旧同一逻辑任务互斥的开关与影子对照。Phase E 默认不启用新的真实券商写路径。
- **BREAKING：** 已迁移的分析 workflow 不再由旧 scheduler 直接调用 Agent；未登记任务、未声明依赖、过期投影、日历来源不明或不完整的全量分析不得静默进入决策。

## Capabilities

### New Capabilities

- `workflow/dispatcher-runtime`：注册表校验、依赖展开、任务适配器、有界并发、投影复用、失败隔离、运行持久化与 Chief 完整性门禁。
- `scheduling/trigger-ledger`：三类触发的稳定身份、持久认领、租约与恢复、重复投递、misfire 决议和新旧调度入口互斥。
- `scheduling/event-calendar`：受治理事件发现、稳定身份与版本、来源冲突、人工覆盖、计划/实际发布区分、时间与交易时段语义、数据产品和改期/取消处理。

### Modified Capabilities

无。现有 `workflow/run-contracts`、`agent/task-projection` 和 `data/data-layer-architecture` 的行为要求作为实施输入；本 change 在上述三个新增能力中约束其接线和运行行为。

## Impact

- `src/ats/workflow/`：新增 Dispatcher、任务注册、执行适配器、运行与触发 repository。
- `src/ats/runtime/scheduler.py`、`src/ats/runtime/cli.py`、调度配置和 `config/events.yaml`：逐项迁移旧分析入口、手动命令和定时/事件触发。
- `src/ats/data/` 与 `config/data/`：事件来源适配、准入、版本化存储、Calendar Data Product 和刷新运行记录。
- Workflow Memory：additive 的 `workflow_runs`、`agent_runs`、`trigger_runs`；Data Platform：additive 的 `schedule_events` 及来源修订记录。
- Layer、Information、Sector、Fundamental、Macro、Technical 的现有运行入口和 Task Projection 发布；Chief 仍按已有审批契约启动。
- 测试与运维：并发、缓存失效、恢复、事件改期、时区/休市、日历来源故障、重复投递、旧作业互斥和影子对照的专项验收。
