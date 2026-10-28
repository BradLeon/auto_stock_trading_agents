## Purpose

为手动、定时和事件调度提供跨进程持久的触发身份、认领与恢复记录，使重复投递、进程休眠和调度器迁移不会产生重复的逻辑运行。

## ADDED Requirements

### Requirement: 每种触发必须有稳定身份和冲突检查

手动触发 SHALL 使用调用方键或系统返回后供重试复用的键；定时触发 SHALL 使用 schedule ID、原计划时刻和 workflow ID；事件触发 SHALL 使用 event ID、event version 和 workflow ID。相同键但请求范围、模式或参数不同 SHALL 被拒绝为身份冲突，不得复用旧结果。

#### Scenario: 手动请求重试

- **WHEN** 客户端以原返回的手动键和相同请求重试
- **THEN** Trigger Ledger SHALL 返回原逻辑触发及其运行状态
- **AND** SHALL NOT 建立第二次逻辑运行

#### Scenario: 相同键携带不同 scope

- **WHEN** 第二次投递使用相同事件键，却把分析范围从一个标的改为另一标的
- **THEN** Trigger Ledger SHALL 返回明确的冲突错误
- **AND** SHALL NOT 将旧运行结果误用于新范围

### Requirement: 触发认领必须跨 worker 原子化并可恢复

Trigger Ledger SHALL 原子记录认领、所属 worker、租约期限、attempt、运行引用和状态转移。重复投递 SHALL 返回已有记录；仅租约已失效且未处于不可重放外部副作用的触发可被恢复认领。已完成触发 SHALL 不得再次执行 Agent。

#### Scenario: 两个 worker 同时收到事件

- **WHEN** 两个 worker 同时认领同一 event/version/workflow
- **THEN** 最多一个 SHALL 获得执行租约
- **AND** 另一方 SHALL 读取既有触发状态而不启动第二个 Agent

#### Scenario: worker 在任务中途退出

- **WHEN** 触发租约过期且任务仍为非终态
- **THEN** 恢复者 SHALL 根据已持久化的 Agent attempt 和投影状态决定续跑或重试
- **AND** SHALL 保留前次 attempt 与恢复原因

### Requirement: misfire 必须形成明确决议

定时和事件触发的补偿 SHALL 使用原计划时刻计算身份；每个 workflow SHALL 声明宽限期、补跑或跳过策略，以及最大回溯范围。超过宽限或遇到休市时 SHALL 按策略形成 `skipped` 或补跑记录，并保存实际延迟、策略版本和原因。

#### Scenario: 进程休眠跨过计划时刻

- **WHEN** 调度器重启时发现一个仍在补跑窗口内的计划触发
- **THEN** Trigger Ledger SHALL 使用原计划时刻认领并记录补跑延迟
- **AND** SHALL NOT 按重启时刻生成新逻辑键

#### Scenario: 过期触发被跳过

- **WHEN** 已错过事件的补跑期限
- **THEN** Trigger Ledger SHALL 持久记录跳过原因
- **AND** SHALL NOT 静默丢失该触发

### Requirement: 事件修订和取消必须阻断旧版本的新决策

新日历版本发布后，旧版本尚未认领的触发 SHALL 标记失效；已运行版本保留历史。若旧版本触发仍在运行，SHALL 显式标记其研究结果与当前日历版本不一致，并在进入决策周期前重新核验，不得以旧计划继续自动交易。

#### Scenario: 财报改期时旧触发仍在运行

- **WHEN** 旧版本的财报分析尚未完成，日历发布改期版本
- **THEN** 旧运行 MAY 完成研究记录，但 SHALL 被标记为事件版本过期
- **AND** SHALL NOT 依据旧版本自动进入 Chief

### Requirement: Ledger 必须支持运维查询和调度切流

运维接口 SHALL 能按 workflow、计划窗口、event、状态和时间范围查询触发、attempt、跳过和错误。新旧 scheduler 对同一 workflow 的所有权 SHALL 由持久或可审计的切流状态约束，重启后保持一致。

#### Scenario: 核对漏跑窗口

- **WHEN** 操作者查询某个时段的预期触发与实际执行
- **THEN** 系统 SHALL 显示已完成、在途、跳过和缺失的窗口及原因
- **AND** SHALL 提供对缺失窗口进行幂等补偿的入口
