## Purpose

规范旧表、旧入口、旧调度任务和兼容读模型的退出流程，使分阶段迁移不会变成无期限双路径或静默回退。

## ADDED Requirements

### Requirement: 旧实现退出前必须建立墓碑登记

任何旧实现被宣布待退或已退时，登记 SHALL 包含唯一标识、类型、替代实现、目标 Phase、消费者清零条件、数据对账条件、回滚窗口和当前状态。

Phase F 的消费者清零 SHALL 指生产消费者清零，已关闭的离线诊断和历史证据 SHALL NOT 被计为生产消费者。数据对账 SHALL 验证新需求覆盖、血缘及必需历史可读，SHALL NOT 强制旧业务结果一致。回滚窗口 SHALL 登记观察窗口及已演练的安全停止或已验收版本恢复，SHALL NOT 以修复或恢复旧业务为退役前置。仍能影响触发、发布、提交或状态的旧入口 SHALL 证明关停或失权。

#### Scenario: Phase 开始前登记旧 Scheduler 任务

- **WHEN** Dispatcher 迁移开始且旧硬编码 Scheduler 任务仍在使用
- **THEN** 每个待替换任务 SHALL 在退役登记中列出替代 workflow ID 和退出条件

### Requirement: 已退役标识必须 fail closed

同一标识 SHALL NOT 同时出现在在用和已退役集合中。读取或调用命中已退役标识时 SHALL 返回显式退役原因和替代路径，SHALL NOT 静默回退到另一实现。

#### Scenario: 请求已退役的旧数据源标识

- **WHEN** 读取方使用已登记退役的旧标识
- **THEN** 系统 SHALL 返回机器可读的 retired reason
- **AND** SHALL NOT 自动尝试同名、别名或旧存储路径

### Requirement: 物理清除必须使用两段式流程

退役资产的物理清除 SHALL 默认只生成 dry-run 范围、行数/文件数、依赖检查和可恢复导出信息。只有显式确认后才可执行物理清除，并保留 actor、时间、范围和备注。

#### Scenario: 未确认的清除命令

- **WHEN** 操作者运行清除而未提供显式确认
- **THEN** 系统 SHALL 只返回 dry-run 计划
- **AND** SHALL NOT 删除表、行、文件或旧入口

### Requirement: 每个迁移 Phase 必须维护待退清单

每个 Phase SHALL 在产物中列出本阶段引入的兼容层和待退旧实现，并为每项标记已满足或尚缺失的退出条件。

#### Scenario: 阶段结束但消费者未清零

- **WHEN** 新读路径已上线但旧读模型仍有消费者
- **THEN** 待退项 SHALL 保持为未完成
- **AND** SHALL 列出未迁移消费者而不得宣布旧实现已退役

