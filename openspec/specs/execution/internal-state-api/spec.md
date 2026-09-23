## Purpose

把交易历史、绩效和归因以带 as-of 与完整性标记的内部状态读模型发布给下一轮 Chief、Risk 和复盘流程，使派生结论可重建、可追溯，且缺口显式可见。

## Requirements

### Requirement: 绩效和归因必须可从不可变原始记录重建

Clerk SHALL 使用不可变订单与成交、市场 marks、费用、决策与审批记录重建绩效和归因读模型。重建 SHALL NOT 修改原始事实。

重建 SHALL 记录所用方法版本与重建时点；同一方法版本与同一组原始记录 SHALL 产生相同的重建结果。

#### Scenario: 删除派生绩效读模型后重建

- **WHEN** 运维在保留原始记录的前提下重建某期绩效
- **THEN** 重建结果 SHALL 使用同一方法版本产生相同结果
- **AND** SHALL 记录重建时点与方法版本

#### Scenario: 方法版本发生变更

- **WHEN** 绩效或归因的计算方法版本发生变化
- **THEN** 新版本生成的读模型 SHALL 携带新的方法版本标识
- **AND** SHALL NOT 静默覆盖旧版本结果而无法区分

### Requirement: 派生读模型与原始事实必须分离

派生读模型 SHALL 与不可变原始记录分开存储并可被识别为派生结果。读模型的生成或重建失败 SHALL NOT 回写或修改原始账本事实。

#### Scenario: 读模型生成过程中出错

- **WHEN** 绩效或归因读模型的生成过程发生错误
- **THEN** 系统 SHALL 保留不可变原始记录不受影响并登记失败
- **AND** SHALL NOT 产出部分写入却标记为完整的读模型

### Requirement: Internal State 必须携带 as-of 与完整性标记

Clerk SHALL 发布组合、交易历史、绩效、归因和审计异常的读模型，并标记数据截止时点与完整性状态。

完整性标记 SHALL 反映尚未对账的窗口、无法归因的成交和历史断链缺口，SHALL NOT 在存在缺口时把状态呈现为完整。

#### Scenario: 存在未对账窗口时发布内部状态

- **WHEN** 发布时点存在尚未对账或已登记漏跑的窗口
- **THEN** 内部状态 SHALL 标记完整性为不完整并列出缺口范围
- **AND** SHALL NOT 以静默方式呈现为已完整对账

### Requirement: 下游消费方必须经 Internal State 读取交易与绩效

Chief、Risk 和复盘流程读取交易历史、绩效与归因时 SHALL 使用 Internal State 读模型，SHALL NOT 直接读取原始交易与绩效表。

#### Scenario: Chief 组装新研究快照的内部状态

- **WHEN** Chief 组装新 research snapshot 的内部状态
- **THEN** Internal State SHALL 提供截至指定 as-of 的交易、绩效和归因
- **AND** SHALL 显示尚未对账或无法归因的缺口

#### Scenario: 风控读取历史绩效

- **WHEN** Risk 在审查中读取组合与绩效历史
- **THEN** Risk SHALL 从 Internal State 读取并同时获得完整性标记
- **AND** 完整性为不完整时 SHALL 以显式降级或阻断处理，SHALL NOT 视为完整数据
