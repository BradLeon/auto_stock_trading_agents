## Purpose

将决策背景、审批、订单、成交、持仓、绩效和归因组织为可重放的确定性交易账本，为对账、复盘和下一轮决策提供可审计内部状态。

## ADDED Requirements

### Requirement: Clerk 必须使用确定性来源构建账本

Clerk SHALL 从决策领域记录和券商回报构建账本，SHALL NOT 使用 LLM 生成、猜测或覆盖 order、fill、position、cash、PnL 或 attribution 事实。

#### Scenario: 可选 critic 对交易生成复盘文字

- **WHEN** LLM critic 为已完成交易生成复盘叙事
- **THEN** 叙事 SHALL 作为附加分析存储
- **AND** SHALL NOT 修改任何账本金额、交易身份或归因计算

### Requirement: 系统交易必须连接完整决策链

每个系统 order 和 fill SHALL 可追溯到 cycle、decision revision、decision hash、risk review 和 Boss approval。任何断链 SHALL 被标记为审计异常，不得静默归因。

#### Scenario: 券商回报一笔系统订单成交

- **WHEN** fill 的 order reference 匹配到系统 client order ID
- **THEN** Clerk SHALL 将 fill 链接到该 order 及其完整决策审批链
- **AND** SHALL 保留券商 order ID、fill ID、时间、数量、价格和费用

### Requirement: Clerk 必须区分系统、人工和无法归因交易

Clerk SHALL 根据可验证订单引用将券商交易分为系统订单、人工订单或无法归因订单。无法归因 SHALL 产生显式异常项，SHALL NOT 被删除或强行归类为系统交易。

#### Scenario: 券商成交不带系统 order reference

- **WHEN** 对账发现一笔无法匹配任何系统订单的 fill
- **THEN** Clerk SHALL 将其记录为 manual 或 unattributed，并保留判定依据
- **AND** SHALL NOT 伪造 cycle 或 approval 链接

### Requirement: 对账和补偿必须可幂等重放

Clerk SHALL 使用券商稳定身份和本地幂等键重放订单、成交、撤单和拒单对账。部分成交、迟到成交、进程重启或某日漏跑 SHALL NOT 造成重复 fill 或丢失原记录。

#### Scenario: 相同 fill 被重复返回

- **WHEN** 后续对账再次收到已记录的 broker fill ID
- **THEN** Clerk SHALL 幂等更新对账时间或返回已有记录
- **AND** SHALL NOT 新增第二笔成交或重复计入绩效

### Requirement: 绩效和归因必须可从原始记录重建

Clerk SHALL 使用不可变订单/成交、市场 marks、费用、决策与审批记录重建绩效和归因读模型。重建 SHALL NOT 修改原始事实。

#### Scenario: 删除派生绩效读模型后重建

- **WHEN** 运维在保留原始记录的前提下重建某期绩效
- **THEN** 重建结果 SHALL 使用同一方法版本产生相同结果
- **AND** SHALL 记录重建时间和方法版本

### Requirement: Clerk 必须发布可追溯的 Internal State

Clerk SHALL 向 Chief、Risk 和复盘工作流发布组合、交易历史、绩效、归因和审计异常的可追溯读模型，并标记 as-of 和数据完整性。

#### Scenario: Chief 读取最近交易历史

- **WHEN** Chief 组装新 research snapshot 的内部状态
- **THEN** Internal State SHALL 提供截至指定 as-of 的交易、绩效和归因
- **AND** SHALL 显示尚未对账或无法归因的缺口

