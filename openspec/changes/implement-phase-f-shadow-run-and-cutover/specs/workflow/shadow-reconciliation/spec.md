# Spec Delta

## Purpose

为 Phase F 提供统一的影子运行与差异归因能力：在相同数据 vintage 下让新旧分析、风控与调度路径并行运行并逐类比较差异，同时从进程能力层禁止新路径提交真实订单，使切流决策有可签核的证据而不是主观判断。

## ADDED Requirements

### Requirement: 影子运行必须固定数据 vintage

影子运行 SHALL 让新旧路径在同一份已发布数据快照上执行。影子报告 SHALL 记录所使用的数据 vintage、投影 hash 与运行标识；任一路径读到不同 vintage 时 SHALL 标注该差异，SHALL NOT 以「数据刷新导致差异」掩盖分析或调度差异。

#### Scenario: 两条路径读到同一 vintage

- **WHEN** 影子运行中新旧路径使用相同的数据快照与投影
- **THEN** 影子报告 SHALL 记录该快照标识与投影 hash
- **AND** 报告中的差异 SHALL NOT 归因为数据版本差异

#### Scenario: 路径之间出现 vintage 漂移

- **WHEN** 影子运行中检测到两条路径使用的数据 vintage 不一致
- **THEN** 影子报告 SHALL 将该不一致单独列为数据版本差异
- **AND** 该轮影子 SHALL NOT 被用作分析或调度一致性的通过证据

### Requirement: 影子差异必须覆盖六类比较面

影子差异报告 SHALL 至少覆盖：输入快照（数据 vintage 与投影集合）、分析输出（各角色产出内容与缺口）、调度遗漏（旧调度已触发而新调度未触发，或反之）、风控 verdict 与 counterproposal、审批链（人工批准/拒绝与轮次）、交易归因（影子订单意图与归属）。每类 SHALL 分别给出 matched、diverged 或 not-compared 的结论，SHALL NOT 以汇总数字掩盖未比较的面。

#### Scenario: 新调度遗漏了一次旧调度已触发的运行

- **WHEN** 同一触发在旧调度路径已执行而新调度路径未产生对应运行
- **THEN** 调度遗漏面 SHALL 记为 diverged 并列出该触发的标识与时间
- **AND** 该差异 SHALL 独立于分析输出一并进入报告

#### Scenario: 某一面未比较

- **WHEN** 某类比较面因缺少可比数据而未执行
- **THEN** 报告 SHALL 将该面记为 not-compared 并说明原因
- **AND** SHALL NOT 省略该面或将其计为一致

#### Scenario: 风控 verdict 不一致

- **WHEN** 影子运行中风险方对同一提案给出与旧路径不同的 verdict 或 counterproposal
- **THEN** 风控面 SHALL 记为 diverged 并列出两侧 verdict 与差异理由
- **AND** 系统 SHALL NOT 自动以任一侧结论覆盖另一侧

### Requirement: 影子期必须从进程能力层禁止新路径真实下单

新路径在影子期 SHALL NOT 具备向券商提交真实订单的进程能力。该禁止 SHALL 由能力层实现并覆盖全部下单出口，SHALL NOT 仅依赖调用方自觉传入的模拟标志。缺少该能力的进程 SHALL 拒绝启动影子任务并报告缺失项。

#### Scenario: 影子任务尝试下单

- **WHEN** 影子运行中的新路径到达下单调用点
- **THEN** 系统 SHALL 拒绝该调用并记录为影子期禁止的真实下单尝试
- **AND** SHALL NOT 因订单被拒而将该次影子运行标记为失败

#### Scenario: 影子运行缺少能力禁令

- **WHEN** 部署的进程不具备禁止真实下单的能力
- **THEN** 影子任务 SHALL 拒绝启动
- **AND** 报告 SHALL 指出缺失的能力项

### Requirement: 影子订单与真实订单账本必须隔离

影子运行产生的订单意图 SHALL 写入独立影子账本，SHALL NOT 写入真实成交账本或参与持仓、绩效与资金对账。真实下单记录 SHALL NOT 出现在影子差异报告中充当已成交证据；影子意图 SHALL 保留可重建所需的全部归因字段。

#### Scenario: 影子意图进入真实成交账本

- **WHEN** 影子运行的订单意图被写入真实成交账本
- **THEN** 系统 SHALL 判失败并指出该写入
- **AND** 该记录 SHALL NOT 参与持仓、绩效与资金对账

#### Scenario: 影子归因可独立重建

- **WHEN** 审计方请求重建某次影子运行的下单意图
- **THEN** 系统 SHALL 能从影子账本重建该意图及其归因字段
- **AND** 重建 SHALL NOT 改变任何原始记录

### Requirement: 影子报告必须可签核且不可事后改写

每份影子差异报告 SHALL 包含生成时间、覆盖范围、逐面结论、差异明细与未比较项。报告 SHALL 为追加式记录，SHALL NOT 覆盖既有报告或修改其结论；签核、驳回与撤销 SHALL 作为新记录追加，并记录操作者与理由。

#### Scenario: 驳回一份影子报告

- **WHEN** 审阅者驳回某份影子差异报告并给出理由
- **THEN** 系统 SHALL 追加一条驳回记录指向该报告
- **AND** 原报告的结论 SHALL 保持可读且不被改写

#### Scenario: 差异报告未覆盖必需面即被引用

- **WHEN** 切流决策引用了一份存在 not-compared 必需面的影子报告
- **THEN** 系统 SHALL 拒绝将该报告作为该范围的通过证据
- **AND** SHALL 报告未比较的面
