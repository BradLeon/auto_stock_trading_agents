# Phase F 5.2 / 5.5 / 5.14 验证报告

日期：2026-10-08。change：`implement-phase-f-shadow-run-and-cutover`。本轮三项局部工程任务完成，当前 **46/116**。依赖 0.3/5.1 已完成；完整门禁、生产资格及读/调度迁移尚未完成。

结论：**声明/接线证明门控、Chief/Sector 责任映射、逐业务 scope 的持久路由及历史通过局部验收**。不能据此把六条边界全部标为 enforced，或执行生产切换。

## 5.2 接线声明与证明

`boundary_evidence.CALL_POINTS` 关联六边界的 26 个实际函数/方法，包括手动 CLI、Dispatcher、旧 scheduler、两种投影发布、repository 审查/审批、Clerk 和 broker 提交/撤单。每项列明 scope 语义、consumer、模式、权威写方、预期守卫及后续实施任务。AST 检查只作“函数存在/守卫缺失”的诊断，不能通过它认定 enforced。

历史 `wired=1` 兼容列、bootstrap 或非空声明不会开放切流。查询分别显示 declared 与 enforced；接线证据按完整身份和模式保存。证明须覆盖适用实际入口，包含正向与否定测试、JUnit 摘要、测试实际记录的入口/身份/模式、零否定副作用及代码/测试依赖摘要。重验时证据不可读、测试缺失/失败、scope 或模式不符、代码/证据漂移均拒绝。证明是受审阅的验证材料，不是独立部署授权；不签发 broker grant 或生产资格。

实际 `IBKRBroker._submit`、`cancel_all` 的四个正反入口测试通过模拟 transport 跑出带绑定属性的 JUnit，并在临时控制库登记 isolated 证明，验证可复读、跨范围拒绝、不能改标 production、摘要漂移拒绝。其他未接通入口保留 declared/未证明；例如 repository 仍缺实际 guard，由 **5.3** 实施，不能用 guard 函数存在替代业务调用。

真实 `cutover set-route` 与原读执行器对仅有 bootstrap 的切流请求拒绝，返回不变更状态。原执行器尚缺 exact-scope 协议，安全关闭其目标迁移路径；**9.2** 完成后才可经新协议开放。旧逻辑测试显式模拟独立接线服务，只用于授权、报告、结果汇总和历史逻辑，不计为实际接线验收。

## 5.5 Chief/Sector 责任

- Chief：`ats.graph.chief.assemble_context` → `ats.agents.chief.assemble.projection_context_block`，实施责任 **9.3**：真实投影读取/渲染、旧读兼容、缺投影回退或停止、重启引用。
- Sector：`ats.runtime.cli.run_sector_html` → `ats.workflow.cutover_routing.read_route`，实施责任 **9.6**：CLI 治理投影与兼容读、当次 scope 门禁、缺输入失败、跨进程引用。

校验实际任务行唯一存在、任务覆盖对应职责、owner 为可解析的源码调用点。`F.6.2/F.6.3`、非空占位符和仅改编号但删去迁移职责均失败。**9.3/9.6 仍未完成**，本项只确认责任没有丢失。

## 5.14 逐 scope 权威路由

`scoped_routes.RouteIdentity` 固定 `domain_id + consumer_id + contract_version + scope`，要求 kind/id、显式 entities 和有时区的 UTC 时间窗；事件需 event_id/version 成对。排序去重实体、统一时区，额外业务字段保留，返回副本防止调用方修改。产品列表不能替代业务 scope，实体/窗口/事件版本/契约变化产生新身份。

状态与追加式历史放在 cutover 控制库的独立表，单个 scope 的状态和历史在同一 SQLite 事务提交；expected generation 拒绝过期操作者。提交锁内重验接线、资格、报告和回退，三个检查收到并返回同一身份；缺 checker、无引用或 scope 不符拒绝。回退 target → legacy 必须有该 scope 的有效回退证明。全局状态保留为兼容诊断和 disabled 紧急停止，不能把未知 scope 提升为 target。CLI `cutover state` 可只读查看逐 scope 状态。

真实持久化 API 与 `cutover_routing.read_route` 的隔离验证：Layer 合格而 Sector 不合格时，只保存 Layer target；Sector、不同实体和未登记范围保持 legacy，调度未变；新进程仍解析同一路由。读取 target 时重新查询该 exact-scope 资格和接线证明。原全局 target 布尔参数不能放行；缺/损坏权威数据库拒绝且只读不建库。双进程同 generation 只有一方提交；历史写入故障回滚状态；回退保留前后历史。

**验证界限**：这一组路由测试明确模拟资格、报告、回退与接线证据适配器，验证的是实际控制库、runtime resolver 和 CLI 状态展示；不证明生产 Layer 已合格或实际研究 CLI/Workflow/Dispatcher 已迁移。实际调用方统一接入由 **5.6**，安全回退由 **5.7**，正式执行器由 **9.2** 承接。单 scope 数据库事务不等于跨边界/覆盖层/owner YAML 联合提交；后者仍由 **5.4/5.15** 完成。

## 验证结果

- 相关回归：**324 passed，2 个既有 `\d` SyntaxWarning**。包括新增 47 个 scope/接线/owner/真实 CLI 拒绝测试，原控制平面、读执行器、实际 broker/启动保护及单活回归；JUnit 见 [regression.junit.xml](phase_f_scoped_routing_20261008/regression.junit.xml)。其中嵌套运行四个实际 broker 入口场景用于证明属性验证，不额外累加 pytest 主报告计数。
- 文档/记录守卫：**20 passed**，见 [record-guards.junit.xml](phase_f_scoped_routing_20261008/record-guards.junit.xml)。OpenSpec 两 change strict、依赖无环及选定任务直接依赖、代码检查、受保护/配置/uv 摘要与生产只读 inventory 核对见 [verification.json](phase_f_scoped_routing_20261008/verification.json)。四个 broker 入口的隔离证明包保存为 [isolated-entry-proof.json](phase_f_scoped_routing_20261008/isolated-entry-proof.json)，绑定本轮 JUnit 与依赖摘要，不写生产证据库。
- 未重跑全量测试；之前冻结的全量数字及两轮恢复报告保持原时点，不能作为本轮全量归因。

**后续注意/待修复项**：5.3 实际审批/Clerk/两种 Analyst 发布；5.6/5.7 真实读取及安全回退；5.4/5.15 兼容与联合恢复；9.2 强制报告/投影/部署授权适配；9.3/9.6 Chief/Sector 实际迁移。随后进入隔离全链/真实 shadow、最终冻结取证及获授权生产动作。C3 保持停用。

**为何现在不修**：本轮授权范围为 5.2/5.5/5.14；上述工作有独立验收和依赖，需要逐项推进。明确模拟适配器的局部路由测试不能冒充业务链已接线或生产资格，不新增授权或生产运行记录。
