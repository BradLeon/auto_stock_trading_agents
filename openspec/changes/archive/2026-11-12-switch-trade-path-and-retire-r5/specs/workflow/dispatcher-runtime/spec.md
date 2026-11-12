## ADDED Requirements

### Requirement: 交易路径工作流必须由 Dispatcher 按交易日排程

交易路径切换后，主理人每日决策与 Clerk 每日对账 SHALL 作为两个独立工作流由 Dispatcher 排程，各有独立的逻辑触发身份与幂等键，只在交易所交易日触发。主理人每日决策 SHALL 经既有 Chief 完整性门禁进入决策周期；Clerk 对账 SHALL NOT 依赖主理人决策成功。两者 SHALL 可分别停用。错过、跳过与失败的触发 SHALL 在触发台账中可见并带原因。

#### Scenario: 休市日不触发

- **WHEN** 当天是交易所休市日
- **THEN** 两个交易路径工作流 SHALL NOT 触发
- **AND** 触发台账 SHALL 记录跳过及休市原因

#### Scenario: 研究不完整时不建决策周期

- **WHEN** 主理人每日决策触发时，任一必需分析类别缺失、失败或过期
- **THEN** 该次运行 SHALL 以不完整终结并产出缺口报告
- **AND** SHALL NOT 创建决策周期，也 SHALL NOT 调用主理人、风控或 Trader

#### Scenario: 主理人失败不影响对账

- **WHEN** 当天主理人每日决策失败或被阻断
- **THEN** Clerk 每日对账 SHALL 照常按自己的时间表运行

#### Scenario: 重启后不重复执行已完成触发

- **WHEN** Dispatcher 在某交易路径触发完成后重启并执行启动补跑
- **THEN** 同一逻辑触发 SHALL NOT 再次执行
- **AND** SHALL NOT 产生第二个决策周期或第二次对账事实

#### Scenario: 停用期间不被唤醒

- **WHEN** 某交易路径工作流处于停用状态
- **THEN** 定时 SHALL NOT 唤醒它，触发台账 SHALL 记录跳过原因
- **AND** 另一个交易路径工作流的排程 SHALL 不受影响
