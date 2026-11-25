# Spec Delta

## ADDED Requirements

### Requirement: 交易工作流的触发必须落在所用交易时段内并留足审批窗口
定时触发的首席决策 SHALL 在运行前确认其将使用的交易时段处于开放状态。时段未开放时 SHALL 记录跳过原因且不产生提案。时段开放时，审批请求 SHALL 带有截止时间，该时间不晚于时段收盘前的固定审批窗口。时段收盘时仍未成交的该时段订单 SHALL 被撤销并记录，不得自动带入下一个交易时段。

#### Scenario: 周六上午触发
- **WHEN** 首席定时任务在台北周六触发，隔夜时段不开放
- **THEN** 系统 SHALL 记录 `skipped_session_closed`，不产生提案与审批请求

#### Scenario: 审批截止时间
- **WHEN** 首席在隔夜时段内生成待审批提案
- **THEN** 审批请求 SHALL 显示截止时间，且截止时间不晚于收盘前 4 小时

#### Scenario: 盘前运行
- **WHEN** 首席定时任务在纽约交易日盘前（04:00 至开盘）触发
- **THEN** 系统 SHALL 以延长时段 venue 运行，订单为允许常规时段以外成交的限价当日单
- **AND** 审批截止时间 SHALL 不晚于当日常规收盘前 4 小时

#### Scenario: 收盘时仍有未成交订单
- **WHEN** 隔夜时段收盘前 10 分钟仍有未成交的隔夜订单
- **THEN** 系统 SHALL 撤销这些订单并记录 `cancelled_session_end`
- **AND** SHALL 不在常规时段以原价格依据重新提交
