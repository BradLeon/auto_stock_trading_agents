## ADDED Requirements

### Requirement: 冲突的交易路由配置必须在启动时 fail closed

同一时刻出现两条及以上可提交真实订单的交易路由，或交易路由的权威状态不可读、不可解析时，系统 SHALL 在启动阶段判定失败并指出冲突的路由标识，SHALL NOT 继续运行、默认选取其中一条路由或降级为无门禁运行。交易路由关闭 SHALL NOT 阻止研究服务启动。

#### Scenario: 同时存在两条活跃交易路由

- **WHEN** 配置或权威状态同时把两条交易路由声明为可提交真实订单
- **THEN** 启动 SHALL 失败并指出两个路由标识
- **AND** SHALL NOT 选取其中任一路由继续提供服务

#### Scenario: 交易路由权威状态不可读

- **WHEN** 启动时交易路由的权威状态不可读或不可解析
- **THEN** 系统 SHALL fail closed，不开放任何可提交真实订单的能力
- **AND** SHALL 报告不可读的原因

#### Scenario: 交易路由关闭时研究服务可启动

- **WHEN** 没有活跃交易路由而研究服务启动，且无其他冲突
- **THEN** 研究服务 SHALL 允许启动
- **AND** 研究读取与交易路由 SHALL 作为两件独立事处理

### Requirement: 数据产品检查不合格时必须阻断受影响任务并告警

研究消费者读取的数据产品 SHALL 在两个时点检查：数据刷新任务完成后，以及 Dispatcher 实际执行该消费者的任务之前（复用既有投影时不必检查）。任务前检查不合格时，系统 SHALL 阻断该消费者的任务并告警，保留历史与审计，与它无关的任务 SHALL 继续执行。系统 SHALL NOT 以单一总开关或「同一数据域其他消费者已通过」为由放行，也 SHALL NOT 改用旧路径、旧表或空结果补齐。告警 SHALL 只在消费者状态变化时发出；检查记录 SHALL 只增不改。检查结论 SHALL NOT 被解释为任何交易、审批或下单权限。

#### Scenario: 数据刷新后产品不合格

- **WHEN** 一次数据刷新完成后，某研究消费者读取的数据产品检查不合格
- **THEN** 系统 SHALL 告警并记录检查结果
- **AND** SHALL NOT 改变任何读取或调度配置

#### Scenario: 任务执行前产品不合格

- **WHEN** Dispatcher 要执行某消费者的任务，而该消费者读取的数据产品检查不合格
- **THEN** 该任务 SHALL 不执行并记为数据产品不合格的缺口
- **AND** 整次运行 SHALL 记为不完整，与它无关的任务 SHALL 继续执行

#### Scenario: 状态未变化时不重复告警

- **WHEN** 同一消费者连续多次检查结论相同
- **THEN** 系统 SHALL 只在状态由合格转不合格、或由不合格转合格时告警

#### Scenario: 同一数据域其他消费者已通过

- **WHEN** 某数据域的其他消费者均已通过，而本消费者检查未通过
- **THEN** 本消费者的任务 SHALL 被阻断
- **AND** SHALL NOT 以数据域整体通过为由放行

#### Scenario: 只有 SEC 原文缺失

- **WHEN** 只有可选官方原文 `sec_edgar_filing_body` / `sec_filing_documents` 缺失或获取失败，消费者的其他输入均合格
- **THEN** 数据产品检查 SHALL NOT 判不合格
- **AND** 该缺口 SHALL 按 `data/refresh-orchestration` 的「SEC 官方原文失败必须可见但允许为空」保持可见

## MODIFIED Requirements

### Requirement: 切流门禁不变量必须由机器校验

系统 SHALL 提供可重复执行的校验，确认资金安全底线：实盘锁定时禁止券商写、交易入口单活、交易路由恢复方式已演练。校验 SHALL 覆盖否定路径：双活交易入口、锁定下存在可达券商提交的路径、恢复演练缺失，都必须判失败。校验失败 SHALL 阻断交易路由的任何变更。

#### Scenario: 校验捕获双活交易入口

- **WHEN** 校验发现两条交易入口同时处于可提交真实订单的状态
- **THEN** 校验 SHALL 失败并指出两个入口标识
- **AND** 该失败 SHALL 阻断交易路由变更

#### Scenario: 校验纳入架构守卫

- **WHEN** 新增绕过交易路由仲裁直接提交真实订单的代码路径
- **THEN** 架构守卫 SHALL 判失败并指出该路径
- **AND** 该路径 SHALL NOT 通过人工豁免长期保留，豁免必须声明收敛期限

## REMOVED Requirements

### Requirement: 迁移完成后研究读路由必须固定为新路径并在发布时与读取前检查数据产品

**Reason**: 研究读路由表已随第 9 组退役（tasks 9.3）删除，不再有可固定的读路由。
**Migration**: 读取规则由 `data/target-dataflow-assurance` 的「消费者必须按声明的数据产品与业务 scope 读取」承担，含业务 scope 必须明确、读取失败不得吞为空结果、不得以产品列表代替 scope。两个时点的数据产品检查、状态变化才告警、不以同一数据域其他消费者通过为由放行、检查结论不构成交易权限，并入本 change 新增的「数据产品检查不合格时必须阻断受影响任务并告警」。

### Requirement: 被意外切回旧路径的研究消费者必须能经批准恢复到新路径

**Reason**: 读路由表与 `ats consumer-cutover`（含 restore）已在第 9 组删除，研究消费者不再存在「旧路径」状态，恢复入口没有作用对象。
**Migration**: 无需替代。历史切换日志保留在 `var/phase_f_cutover.sqlite`，只读。

### Requirement: 每条切流边界必须有独立功能开关

**Reason**: 六条边界开关是迁移期控制面。研究侧在 2026-10-15 裁决后改为按消费者整体切换，读路由已固定；交易路径在本 change 中以代码发布成对切换（design.md D4）。这六个开关始终是「已声明、未接线」，从未作为实际门禁生效。交易路径切换并过观察窗口后，它们已没有任何可切换的对象。
**Migration**: 研究读取按 `data/target-dataflow-assurance` 的「消费者必须按声明的数据产品与业务 scope 读取」执行。交易路由单活与代次由 `execution/live-route-switch` 承担。历史开关状态与变更记录保留只读；`ats cutover` 命令随 R5 退役。

### Requirement: 角色发布、审批写入与账本发布必须受各自边界控制

**Reason**: 切换完成后，审批写入与账本发布只有一条路径：决策审计表与 Clerk。不再存在需要用开关隔离的新旧两侧。
**Migration**: 审批写入的约束见 `decision/approval-lifecycle`；账本串联与发布的约束见 `execution/clerk-ledger` 的「系统订单与成交必须绑定完整决策链」和「Clerk 必须是确定性的编排服务」。

### Requirement: 联合边界切换必须显式且不可暴露半迁移状态

**Reason**: 这条要求只服务于「审批与账本发布」成对切换这一次性动作。本 change 已用同一次代码发布完成成对切换，并在切换前后核验没有半迁移状态（design.md D4），联合协调器随之退役。
**Migration**: 联合授权、冻结 token 与批次记录保留只读。以后若再有需要成对切换的边界，在对应 change 里单独规定。

### Requirement: 冲突的切流配置必须在启动时 fail closed

**Reason**: 这条要求针对的是边界开关的新旧路由互斥，以及“激活交易新路由须先取得读取资格”。R5 之后，边界开关与读取资格都已退役，不再存在可冲突的切流配置。
**Migration**: 仍然需要的部分（交易路由双活、权威状态不可读时 fail closed、交易关闭不阻止研究服务启动）移入新增的“冲突的交易路由配置必须在启动时 fail closed”。未知 scope 的处理由“消费者必须按声明的数据产品与业务 scope 读取”承担。

### Requirement: 数据产品检查失败时必须安全停止受影响范围而不得退回已关闭的旧路径

**Reason**: 按登记策略安全停止、恢复到已验收版本、尚未切换边界以旧路径回退，都属于已在第 9 组删除的读取资格与恢复机制；交易路径已切换，不再有尚未切换的边界。
**Migration**: 仍需要的部分（任务前检查不合格即阻断并告警、不得退回旧路径或以空结果补齐）移入新增的「数据产品检查不合格时必须阻断受影响任务并告警」。已组装快照的依赖失效由 `decision/research-snapshot`「快照失效时周期转为 superseded」与 `execution/authorization-gate` 的执行前重新取时、授权失效后重新审查承担。
