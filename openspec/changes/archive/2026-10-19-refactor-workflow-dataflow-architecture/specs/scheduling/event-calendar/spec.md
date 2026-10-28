## Purpose

为财报、FOMC、核心宏观发布和人工事件提供自动更新、版本化且可幂等触发 Workflow 的日历，并保留改期、取消和补偿记录。

## ADDED Requirements

### Requirement: 日历必须覆盖首期核心事件

Schedule Calendar SHALL 支持公司财报日期及 BMO/AMC/盘中窗口、FOMC 会议/决议/发布会、CPI、PCE、NFP、GDP 及人工创建事件。

#### Scenario: 新的财报日期被发布

- **WHEN** 受治理的财报日历来源发布公司财报日期和 AMC 窗口
- **THEN** Calendar SHALL 发布带有公司实体、计划时间、时区、session 和来源血缘的事件版本

### Requirement: 外部事件必须有稳定身份和版本

同一外部事件 SHALL 使用稳定 `event_id`。计划时间、状态或关键 payload 变更时 SHALL 创建递增 `event_version`，保留旧版本、修订时间和来源血缘。

#### Scenario: FOMC 发布时间被修订

- **WHEN** 日历来源对已存在事件发布新计划时间
- **THEN** Calendar SHALL 保留相同 `event_id` 并创建新 `event_version`
- **AND** 旧版本 SHALL 保留为修订历史而不得被覆盖

### Requirement: 人工覆盖必须显式且可追溯

人工事件配置 SHALL 作为自动日历的覆盖和自定义层，不是可自动发现事件的唯一来源。手动取消、改期或补充 SHALL 记录 actor、原因、时间和被覆盖版本。

#### Scenario: 人工改期已自动发现的财报

- **WHEN** 操作者以可验证来源对自动财报事件提交改期
- **THEN** Calendar SHALL 创建显式 manual override 版本
- **AND** SHALL 保留原自动版本、actor 和改期原因

### Requirement: 事件触发必须幂等

事件 Workflow 的逻辑触发身份 SHALL 由 `event_id + event_version + workflow_id` 组成。相同身份的重复调度、重启或 misfire 补偿 SHALL NOT 创建第二个逻辑触发。

#### Scenario: 重启后重放已触发事件

- **WHEN** 调度器重启并重新扫描到已成功触发的同一事件版本和 workflow
- **THEN** Trigger Ledger SHALL 返回已有触发记录
- **AND** Dispatcher SHALL NOT 创建新逻辑任务

### Requirement: 改期和取消必须更新尚未执行触发

事件新版本发布时，旧版本中尚未执行的触发 SHALL 被取消或失效，并为新版本计算新触发。已执行的旧触发 SHALL 保留历史而不得删除。

#### Scenario: 财报在旧触发执行前改期

- **WHEN** 旧版本定时任务未执行且新版本更改了发布时间
- **THEN** 旧触发 SHALL 被标记为因改期失效
- **AND** 新版本 SHALL 拥有以自己身份建立的触发记录

### Requirement: Calendar 必须正确处理时区、session 和 misfire

每个事件 SHALL 保存原始时区和规范化时间，财报事件 SHALL 保存 BMO/AMC/盘中/未知 session。休市、进程休眠或 misfire 后是否补跑 SHALL 由可配置策略决定并写入 Trigger Ledger。

#### Scenario: 进程在事件时间处于休眠

- **WHEN** 调度器恢复时发现一个已过计划时间的事件触发
- **THEN** 系统 SHALL 按该 workflow 的 misfire 策略补跑或跳过
- **AND** Trigger Ledger SHALL 记录决定、延迟时间和原因

### Requirement: Calendar 不得注入研究结论

Schedule Calendar SHALL 只提供事件身份、时间、范围、来源和触发上下文，SHALL NOT 包含或传递可被 Agent 当作研究结论的分析观点。

#### Scenario: FOMC 事件触发 Macro

- **WHEN** FOMC 日历事件触发 Macro Workflow
- **THEN** 触发上下文 SHALL 只包含事件元数据和数据产品定位信息
- **AND** Macro Analyst SHALL 通过正常 Data Product 读取材料，不从 Calendar 接收结论

