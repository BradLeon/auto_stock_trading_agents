## REMOVED Requirements

### Requirement: 新入口验收必须使用可重放的固定输入

**Reason**: 新入口验收是切换前的一次性验收，验收工具（`acceptance_*`、`paired_business`、`shadow_*`）与回放冻结已在 R5c（8.4）删除。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 新入口验收必须按需求矩阵分级要求

**Reason**: 同上，需求矩阵（`acceptance_matrix`）随验收工具删除。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 新入口验收报告必须覆盖六类需求面

**Reason**: 同上，验收报告（`acceptance_reports`、`shadow_reports`）随验收工具删除。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 新入口需求失败必须修复并重验

**Reason**: 验收已完成，交易路径已切换；之后的缺陷按常规缺陷处理，不再有验收重验流程。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 切流必须校验新入口验收报告的适用性

**Reason**: 切流已完成，激活检查已在 R5c 改为一律拒绝并随 R5d 删除切换控制面。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 影子期必须从进程能力层禁止新路径真实下单

**Reason**: 影子期已结束，影子执行与影子账本已在 R5c 删除。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 下单禁止的验收以提交审计为主证据

**Reason**: 影子期验收已完成，相应工具已删除。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 影子订单与真实订单账本必须隔离

**Reason**: 影子订单意图与影子账本已在 R5c 删除，不再产生影子订单。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 新入口验收报告必须可签核且不可事后改写

**Reason**: 验收报告工具已删除；已签核的历史报告保留在 `var/` 下只读。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 固定输入验收必须区分部署许可与运行时间

**Reason**: 部署许可（读窗口与联合授权）已在 R5d 删除，固定输入验收工具已在 R5c 删除。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。

### Requirement: 本轮Routine有限验收保留后补缺口

**Reason**: 一次性的有限验收已结束，后补缺口在当时的 verification 记录中处置。
**Migration**: 进程级禁止真实下单由 `execution/authorization-gate`「隔离模拟执行必须与生产下单禁令分离」与生产 C3 承担；单一可提交路由由 `execution/live-route-switch`「任何时刻只允许一条可提交真实订单的路由」承担；隔离运行不得写生产账本由 `isolation.assert_write_destination` 承担。历史验收报告与签核保留只读。
