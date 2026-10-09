# Phase F 实际达成情况审计

审计日期：2026-10-07。对象：`implement-phase-f-shadow-run-and-cutover` 的规划、实现、测试、`PHASE_F_*.md` 验证材料及本机当前持久化状态。

审计基线：Git 分支 `feat/rebuild_workflow_dataflow`，提交 `02c81f260718ee0ecdb78a63f2ef96d133a1e7d7`。本机只有该工作分支；本文中的 `refactor-workflow-dataflow-architecture` 与 `implement-phase-f-shadow-run-and-cutover` 是仓库中的 OpenSpec change 名称。没有切换分支、修改业务实现、登记生产证据、改变生产路由或连接券商。

## 1. 审计结论

**Phase F 尚未达到既定目标，也不能认定“门禁实现验收完成，只剩授权与证据”。**

已经交付的是一批有价值的控制、审计、隔离和比较模块，以及围绕这些模块的专项测试。尚未完成的是让这些机制约束真实业务调用、按实际 scope 运行新旧流程、形成可重放影子证据、逐消费者切流并完成业务回滚闭环。

发现五个已用隔离反例确认的关键问题：

1. 审批边界为 `disabled` 时，实际审批仓库仍能写入审批记录。
2. 券商入口缺少 write grant 时仍能进入 broker session；默认状态没有强制拒绝。
3. 不提供影子报告校验和投影可用性校验时，读切流执行器仍可返回 `switched`。
4. 批次中只有一个消费者合格时，执行器仍把全局读边界切到 `target`。
5. 所谓“整链原子切换”分两次提交；第二次失败后留下不兼容的半切换状态。

以上均属于工程缺口。增加部署授权、等待证据 TTL 窗口或再运行一次现有 CLI 都不能消除它们。建议保留已有实现，重开受影响任务；在业务接线和门禁缺陷修复并验证之前，不执行生产读/调度切换。

## 2. 已经达到什么目标

| 目标 | 本次判断 | 达成范围与限制 |
|---|---|---|
| OpenSpec 规划文件完整、结构有效 | 已达成 | 本次 strict 校验通过；这不是业务验收结果 |
| 独立控制状态、六边界记录、批次与操作历史 | 基础已交付 | 本机可读回六边界和五批次；`wired=true` 只表示声明登记 |
| qualification 的追加式证据、指纹、撤销、过期和精确 scope 判断 | 机制已交付并有专项测试 | 实际消费者资格仍为 0/10；调用包装层还有 scope 丢失问题 |
| 进程禁写与隔离账本工具 | 工具及模块测试已交付 | 隔离探针有效；不代表所有真实入口已在能力仲裁下 |
| 交易代次、授权生命周期、冻结/切换工具 | 模块已交付 | 没有生产活跃 route 行；broker 默认拒绝和真实账户绑定不完整 |
| 六面差异比较、签核、撤销与适用性判定 | 模块已交付 | 缺少真实双流程 runner；没有当前可引用影子报告 |
| read/schedule/trade 回滚演练工具 | 有模块演练及文档记录 | 尚不足以证明实际业务路径回滚；审批/Clerk 批次缺演练入口 |
| 缺口登记、未通过项保持旧路由 | 已有记录，当前路由未切 | 保持未切是正确的，但不是切流目标已达成 |
| 十角色实际接入与恢复 | 未完成 | 当前“5/10 合规”来自静态扫描，不能当作五角色实跑成功 |
| 同一输入的新旧分析/风控影子运行 | 未完成 | 输入 hash、比较器和账本存在，实际运行与重放闭环未建立 |
| 逐消费者读切流、统一触发认领、观察与回退 | 未完成 | 有执行器，缺业务接线且有实证门禁缺陷 |
| 旧实现消费者清零与退役 | 未完成 | 三项保持 pending，尚无一项满足退出条件 |
| 核心测试全绿、完整自动交易与整图验收 | 未完成 | 既有材料报告 45 项失败；自动下单仍按此前 C3 裁决停用 |

不宜用 `91/94` 推算业务完成比例。当前 tasks 中许多勾选对应辅助函数或 fixture 验证，而任务原文要求实际入口行为、实际运行引用或端到端结果。

## 3. 本机持久化状态的只读核对

原始结果见 [audit-evidence.json](phase_f_audit_20261007/audit-evidence.json)。数据库使用 SQLite `mode=ro` 与 `query_only`；资格查询使用既有只读 API。

| 对象 | 本次读数 | 含义 |
|---|---|---|
| 六条控制边界 | 五条 `legacy`；`live_trader=disabled`；六条 `wired=1` | 生产切换尚未发生；接线标记不能证明实际生效 |
| `cutover_activations` | 0 行 | 当前库没有激活记录 |
| 五个批次 | 全部 `shadow_report_id` 为空 | 当前批次没有引用影子报告 |
| 消费者资格 | 10/10 `ineligible`，原因均为 `assurance_ledger_missing` | 当前缺的是资格账本，不仅是已有证据过期 |
| shadow reports | 默认报告数据库不存在 | 本机默认路径没有可引用报告 |
| shadow order intents / submit attempts | 0 / 0 | 没有真实影子运行的订单意图和提交拒绝主证据 |
| dispatch owner / claims / expected triggers / freeze | 均 0 行 | 尚未形成业务触发认领证据 |
| trade route state / history / issuance / freeze | 均 0 行 | 不能据此证明实际交易入口已完成单活切换 |
| 调度切换数据库 | 0 张表 | 默认生产路径没有切换记录 |
| 部署授权 | 存在 `DEP-2026-10-06-A`，scope 为六分析消费者，有效期到 2026-12-31 | “尚无部署授权”的笼统描述与当前库不符；具体动作仍须核对授权范围 |
| live 授权库 | 存在 `LIVE-X`，scope 为 trader，标称有效期到 2099 年 | 来源及是否为演练遗留尚未核实；不能认定为用户有效实盘授权，也不能继续写成库中无 `LIVE-*` |

这些是本机默认路径的时点读数，不能证明所有历史环境从未有过运行。已经完成的采集发布工作也不因资格账本缺失而被否定；需要把既有真实材料受控接收、补验并落账。

## 4. 关键问题与证据

### P1-01：接线声明没有约束真实审批、角色发布、Clerk 和读取入口

依据：tasks 5.3 要求在实际写入点接线；cutover-control spec 明确要求审批关闭时拒绝写入、Clerk 关闭时拒绝发布。

`cutover.declare_wiring()` 只保存 `call_site/authority/semantics` 字符串并设置 `wired=1`，见 [cutover.py:292](../../src/ats/workflow/cutover.py#L292)。`bootstrap_wired()` 批量登记这些声明，见 [cutover_wiring.py:292](../../src/ats/workflow/cutover_wiring.py#L292)。这不会向业务入口注入守卫。

对全部 `src/ats` 的 AST 直接调用扫描显示：`guard_approval_write`、`guard_clerk_publication`、`guard_analyst_output`、`read_route` 的调用点均为零。扫描本身不覆盖所有动态调用，但实际审批反例进一步确认了绕过：

```text
隔离库 bootstrap → approval_lifecycle=disabled
→ DecisionAuditRepository.record_approval(...)
→ actual_repository_write_succeeded=true
```

实际写入点 [decision/repository.py:395](../../src/ats/decision/repository.py#L395) 没有读取该边界；[execution/clerk.py:122](../../src/ats/execution/clerk.py#L122) 也没有调用新增 Clerk 守卫。读取路由辅助函数没有接入实际业务读入口，因此“0/10 时全部实际走 legacy”不能由辅助函数单测推出。

影响：开关可查询、`wired=true` 和守卫函数单测通过，仍不足以保证审批/发布/读取受控制。这里阻塞的是门禁实现验收，不只是生产资格。

完成标准：通过实际仓库、角色发布、Clerk、CLI/Workflow 读入口验证关闭即拒绝、合格范围走新路由、撤销后下游停止或安全回退；测试必须调用业务入口，不能只调用守卫函数。

### P1-02：旧调度未接入 Phase F 共享认领与代次

依据：tasks 4.2 要求旧入口执行前读取共享 owner 和代次并登记认领，旧进程仍在时也不能重复发布。

[runtime/scheduler.py:417](../../src/ats/runtime/scheduler.py#L417) 的旧日度级联仍直接调用各阶段；[scheduler.py:882](../../src/ats/runtime/scheduler.py#L882) 仍由 `phase_e` 参数选择启动分支。Phase E 的 [ownership.py](../../src/ats/workflow/ownership.py) 读取 YAML owner；当前 [workflow_owners.yaml](../../config/workflow/workflow_owners.yaml) 全为 legacy，[phase_e_schedules.yaml](../../config/workflow/phase_e_schedules.yaml) 全为 disabled。

没有发现上述执行路径调用新增 `dispatch_claims` 的共享 owner/claim 协议。源码中名为 `claim` 的两个调用属于数据队列，不是 Phase F 调度认领。实际 Phase F 认领库也全空。

影响：将 `dispatcher_schedule` 控制表改为 target，不会自动改变旧常驻进程或 Phase E YAML owner。缺口不能仅靠“先运行一次旧 job 取认领证据”解决；现有旧 job 没有执行该协议。

完成标准：旧 cron、旧 event、手动入口、新 Dispatcher 使用同一逻辑触发身份和权威 owner/代次；在旧进程存活、并发触发和恢复场景中验证最多一个结果发布者，并对独立预期触发集检查遗漏。

### P1-03：broker 在无能力授权时默认放行，账户来源也不是实际连接核验

依据：tasks 2.2/2.3 和单活交易规范要求在提交出口强制验证能力、路由代次与实际账户。

[broker/ibkr.py:319](../../src/ats/broker/ibkr.py#L319) 只有在 `active_grant() is not None` 时才调用 `check_grant()`；无 grant 反而跳过路由/冻结/账户检查。[broker_write_guard.py:379](../../src/ats/execution/broker_write_guard.py#L379) 的进程检查在默认 `UNSET` 下允许继续。

隔离探针重置进程状态、不提供 grant，并用立即抛异常的模拟 session 替代券商连接，结果：

```text
grant=null → IBKRBroker.place_orders(...) → mock session reached
network_opened=false
```

这证明入口在缺 grant 时没有先拒绝；本次没有提交真实订单。常规 Trader 入口当前由 C3 硬停用挡住，但这不能代替 broker 最底层的默认拒绝，也不能覆盖直接调用 broker 的路径。

另见 [ibkr.py:283](../../src/ats/broker/ibkr.py#L283)：`connected_account()` 返回配置里的 `ibkr_account`，且校验发生在 session 打开之前。它证明配置声明一致，未证明当前 broker session 的实际账户一致。tasks 2.3 将配置来源写入“实际连接账户”验收条件，需要修正。

完成标准：所有真实提交出口在缺能力、缺权威 route、被冻结或代次错误时拒绝；授权账户与 broker 实际会话/订单账户核对；验证直接入口也不能绕过。恢复自动交易须遵守此前裁决，不能把本次审计当作解除 C3 的授权。

### P1-04：缺报告和缺投影校验被当成通过

依据：tasks 3.6、9.x 及 cutover-control 的批次要求：报告适用、投影可读取、回退可用之后才能切流。

- [read_cutover.py:564](../../src/ats/workflow/read_cutover.py#L564)：`report_checker=None` 时完全跳过报告检查。
- [read_cutover.py:414](../../src/ats/workflow/read_cutover.py#L414)：没有 projection checker 返回 `available=True, checked=False`。
- [runtime/cli.py:1116](../../src/ats/runtime/cli.py#L1116)：批次 `shadow_report_id` 为空返回通过；正式 CLI 没有向执行器传投影可用性检查。
- 批次 dry-run 对空报告作标记，但没有将其作为拒绝原因；这解释了无报告的 schedule 批次仍可显示 ready。

隔离探针提供临时部署授权与合格资格桩、已声明回退，不提供报告或投影 checker，结果为 `changed=true, outcome=switched`。桩只用于暴露门禁缺省行为，不代表生产资格。

影响：未来资格落账后，空报告和未核验投影不再挡住切换；当前“因资格不足所以没切”掩盖了执行器的缺省放行。

完成标准：可执行批次必须提供可验证的报告和投影检查；缺项、不可读、不适用或撤销均拒绝。若某批次确有不适用面，应依规范矩阵明确表示，不能以空 ID 等同通过。

### P1-05：逐消费者结果没有对应逐 scope 的运行时路由

依据：cutover-control 要求未合格消费者保持原路由，不能随整体开关迁移；资格须按 `domain+consumer+contract+scope` 判断。

[read_cutover.py:595](../../src/ats/workflow/read_cutover.py#L595) 逐消费者判定，但 [read_cutover.py:665](../../src/ats/workflow/read_cutover.py#L665) 只要存在一个可迁移消费者，就修改全局 boundary state。该状态以 `boundary` 为主键，没有 consumer/scope 维度，见 [cutover.py:64](../../src/ats/workflow/cutover.py#L64)。

隔离结果：layer 合格、sector 不合格 → 返回 `partially_switched`，但全局 `projection_read=target`。sector 的“未切换”只是结果报告里的字段，没有相应持久化路由状态。本次未证明生产 sector 已实际走新路由；P1-01 所述业务路由尚未接线。但当前模型无法提供宣称的范围隔离保证。

还有一个查询问题：[runtime/cli.py:1079](../../src/ats/runtime/cli.py#L1079) 的 qualification reader 收到 scope 后，使用 `_consumer_scope()` 替代它；后者只含 consumer 与产品列表，见 [intake.py:507](../../src/ats/workflow/intake.py#L507)。底层 assurance 精确匹配是正确的，包装层却没有查询批次实际 scope 的资格。

完成标准：确定批次业务 scope 的规范表达，把资格、运行时选择、回退证明和结果记录绑定到同一 scope；验证不同实体/时间范围、合格与不合格消费者在同批次中互不放行。

### P1-06：配对边界切换不是原子提交

[read_cutover.py:665](../../src/ats/workflow/read_cutover.py#L665) 注释声称整链一起迁移，但循环逐项调用 `set_route()`。每次 [cutover.py:228](../../src/ats/workflow/cutover.py#L228) 都独立 `BEGIN IMMEDIATE/COMMIT`。

在第二次写入前注入模拟崩溃，结果：

```text
projection_read=target
dispatcher_schedule=legacy
compatible_after_crash=false
```

这是持久化半切换状态，不是推测。当前配对策略需要事务内验证和提交，或明确的冻结/恢复协议；必须通过执行器故障注入验证提交前、提交中、提交后恢复，不可仅用两条正常路径的最终值验证原子性。

### P1-07：十角色“实际接入”目前主要是静态扫描

依据：父 change F.0.3/F.0.4、tasks 7.2/7.5 要求实际 CLI/Dispatcher/Workflow 运行，保留 run ID、refs、投影 hash、决策链和恢复记录。

[runtime/cli.py:483](../../src/ats/runtime/cli.py#L483) 的 intake verify/report 只调用 `scan_consumer_access()`；[intake_verification.py:646](../../src/ats/workflow/intake_verification.py#L646) 解析模块与导入，并不运行十角色。此次重算十条记录的 product/document/vintage refs 全为空，projection hash 全为空。

因此 `5/10 compliant` 是静态扫描结果，不是五角色完成真实接入验收。扫描对自有模块导入的要求也不能单独证明经公共工具间接读取的消费者一定不合规；正反两种结论都需要实际调用链证明。

文档已登记 fundamental/chief/risk/trader/clerk 的接线缺口，但将其称为“Phase F 之后、不阻塞”与 F.0 实际接入的验收要求不相容。可以暂不切这些消费者并先推进无关研究范围；不能由此宣告完整 Phase F 已验收。

当前还存在两个已被材料承认、尚未解决的业务阻塞：

- `PF-7-01`：研究快照按 CATEGORY 判完整，run contract 按 TASK 判完整；[intake_verification.py:971](../../src/ats/workflow/intake_verification.py#L971) 明确记录生产只按快照判断时可能开周期。需要确定权威契约并验证必需输入缺失时实际入口拒绝。
- `PF-7-03`：选择的 B 方案“参考价随审批/执行授权传递”尚未实现；当前是 `AUTO_EXECUTION_ENABLED=False` 的 C3 停用。停用是有效保护，但不是完整自动交易能力恢复。

完成标准：在隔离账本与模拟券商下实际运行单角色、依赖子流程、完整分析和 Chief→Risk→审批→Trader→Clerk，补齐引用、跨进程解析、多轮审查、重复恢复、部分/迟到成交与绩效重建。无需制造真实订单完成这部分工程验收。

### P1-08：影子 runner 与可重放输入闭环缺失，端到端勾选过早

[shadow_inputs.py:80](../../src/ats/workflow/shadow_inputs.py#L80) 的 packet 保存每面 hash；capture helper 计算摘要，`build_packet()` 接收摘要字符串。没有看到将账户、行情、历史状态的内容或不可变可解析引用持久化，再注入新旧流程及固定逻辑时钟的完整实现。hash 能验证内容一致，不能恢复内容供重放。

[runtime/cli.py:1396](../../src/ats/runtime/cli.py#L1396) 的 shadow compare 接收调用者提供的左右 JSON，调用比较器并登记报告；它没有执行新旧分析/风控。源码中的 packet 构造也没有接入实际 workflow 双运行。

当前影子订单账本无意图、无提交尝试，报告库缺失；验收材料也明确 `prohibition_exercised=false`、无六面报告。tasks 13.4/13.5 的端到端验证却已勾选。这些单测验证了“给定记录时能检查关联/拒绝”，没有证明一次真实影子业务运行完成并产生这些记录。

完成标准：建立可恢复输入与逻辑时钟注入的 runner，用同一输入驱动新旧流程，保留六面适用性、差异及接受/撤销记录、订单链和 broker 拒绝审计；跨进程重放应可追溯。签核后的适用报告必须先于其所支持的生产切换。

## 5. 文档和计划需要纠正的地方

### P2-01：完成口径与证据相互矛盾

- [PHASE_F_SUMMARY.md:15](PHASE_F_SUMMARY.md#L15)、175、292 与 [PHASE_F_ACCEPTANCE.md:30](PHASE_F_ACCEPTANCE.md#L30) 宣称剩余工作只有授权/取证；同一 summary 的 5.1 又承认审批/Clerk 回滚入口需要新增代码。本报告进一步复现了多项代码缺口。
- 将“退役判据已应用、三项 pending”列为“退役清零完成”，混淆了判定工具与退役结果。
- “没有部署授权”的材料与当前授权库不符；`LIVE-X` 来源也需要核实。本次不将它解释为授权，不更改该记录。
- `OWNER_BOUNDARIES` 引用 `F.6.2/F.6.3`，但 tasks 中没有这些编号；owner 检查只要求字符串非空。应补真实可追踪的 owner/task，而不是凭非空文本关闭问题。
- “五角色接线在 Phase F 之后”需要与父 change F.0 和已冻结规范协调，不能由验收报告单方面改变验收范围。

### P2-02：独立边界与配对切换的规范仍不一致

cutover-control 开头规定读开关切换时调度状态保持不变；当前兼容矩阵却规定读/调度必须一起迁移，执行器也自动修改两者。analyst/approval/Clerk 又形成另一条配对链。

应明确：哪些边界能独立切，哪些切换需要联合批次、联合授权和联合回滚；同步 design、spec、tasks、runbook 与测试。不能保留“独立切换”场景，同时把自动改动其他边界判成验收成功。

### P2-03：全量测试没有达到全绿，“零新增回归”的基线不足

已有材料报告 `45 failed / 3072 passed / 0 errors / 2 deselected`。其中 16 项归因 C3 停用，27 项归为既有失败，其余守卫/顺序问题有后续处理。**本次没有重跑约 42 分钟的全量套件，因此这些数字仅引用已有记录。**

summary 用 `git worktree HEAD` 复现来支持“既有失败”。若 HEAD 已包含 Phase F 实现，这种比较只能证明当前已提交状态同样失败，不能证明失败早于本 change。应使用明确的 Phase F 之前提交，并固定依赖/配置/数据条件。对于已授权行为变化引起的测试失败，应维护相应预期且保留安全断言；不能把红色测试永久算作完成全绿目标。

## 6. 建议重开的任务与后续工作

以下是审计建议，未擅自修改原 tasks 勾选或此前用户裁决。

| 顺序 | 工作包 | 关联任务 | 完成判据 |
|---|---|---|---|
| 1 | 校正完成口径，登记本报告问题，确定边界与业务 scope 契约 | 5.1–5.4、8.x、13.8 | 文档与实际行为一致，缺口有有效 owner/task；独立/配对切换语义一致 |
| 2 | broker 默认拒绝、真实账户绑定，审批/角色/Clerk 实际写点接线 | 2.2/2.3、5.3、1.6 | 从真实业务入口执行否定路径，缺授权/开关关闭必拒绝 |
| 3 | 读路由精确 scope、投影/报告强制检查、事务切换与安全恢复 | 3.6、5.6–5.10、9.2/9.3 | 本报告三个切流反例均被真实执行器拒绝或安全恢复；未合格范围保持原路由 |
| 4 | 旧/新调度共享触发身份、owner/代次、认领与发布仲裁 | 4.2–4.6、10.1–10.3 | 旧进程未退出也不能重复发布；所有计划触发均可核对 |
| 5 | 十角色实际运行与恢复、CATEGORY/TASK 冲突处理、B 方案落地 | 7.2/7.4/7.5/7.8、7.9 后续实现、F.0.3/F.0.4 | 完整 refs/hash/链路与模拟成交证据；C3 的运行停用状态继续遵守既有授权 |
| 6 | 可重放影子 runner、实际六面比较与订单拒绝审计；补审批/Clerk 回滚 | 3.1、3.7、11.2、13.1、13.4/13.5 | 同一输入的新旧实跑、跨进程重放、适用报告与业务回滚均可复核 |
| 7 | 接收既有真实材料，按受影响 scope 补验、追加证据、重算资格 | 6.4/6.6、F.0.2/F.0.7 | 原材料可定位、原账本记录不改写；scope/指纹/前置/TTL/回退全部满足 |
| 8 | 按有效部署授权执行首批读/调度切换、观察与回退 | 9.4/9.5/10.4 | 先具备影子报告和业务接线，再取临切流证据，记录实际消费者路由与触发归属 |
| 9 | 满足清零、对账、回滚窗口条件后退役；完成必要回归及父 change 整图验收 | 12.x、13.6–13.9、父 change 12.x | 实际消费者清零、测试目标达成、完整数据流与订单链可追溯 |

上表不意味着要从零重做各模块；应保留现有单测，优先补真实入口和故障边界测试。生产切流任务 9.4/9.5/10.4 继续未完成；13.4/13.5 应区分“验证器测试完成”和“业务端到端实跑完成”。

建议顺序是 **工程接线与门禁修复 → 实际隔离接入/影子/回滚 → 最终基线取证 → 授权范围核对 → 逐批切流与观察 → 退役和整图验收**。[SUMMARY 的推荐顺序](PHASE_F_SUMMARY.md#L267) 将影子运行放在切流之后，不应作为当前执行依据。

## 7. 哪些阻塞需要外部条件，哪些现在就能推进

**可以立即推进的工程工作**：P1-01 至 P1-08 的实现与业务入口验证、审批/Clerk 隔离回滚、实际角色 runner、模拟成交恢复、受影响测试与文档修订。均不需要真实下单或等待生产切流窗口。

**业务契约裁决**：CATEGORY/TASK 的权威定义、独立/配对边界语义、观察窗口和停止条件；属于明确验收契约，不宜留到切换后再判断。

**数据/证据条件**：复用原始验收材料；缺失项进行最小补验；先准备可重复执行取证脚本，最后再刷新短 TTL 项。当前 `assurance_ledger_missing` 不能全部归因于“必须等切流前一天”。

**外部运行条件**：真实 TWS 只读核验需要可达网关；实际生产路由动作需要适用部署授权；恢复并开放实盘另需有效实盘授权和 C3 解除条件。当前已有一条部署授权，应核对具体范围，不能重复笼统声称未授权。

受指纹约束不是永久不能改代码的理由，但修复必须遵守既有“不作废已登记证据”的用户约束。优先在覆盖层/新增适配层接线；若确实必须改受保护面，应先列清影响与处理方案，保留历史记录、在最终基线上追加验证并重新计算受影响资格。**不能直接扩大 Trader 数据契约、放宽指纹标准或篡改旧证据以使门禁变绿。**

## 8. 本次验证、复现与审计边界

本次实际运行结果：

- 24 个 Phase F 专项测试文件：`618 passed`，约 100 秒。这次执行发生在用户补充 uv 偏好之前，直接调用了项目 `.venv/bin/python`。
- 用户补充后，统一通过 `uv run --offline --no-sync` 执行；六个与反例直接相关的测试文件重核：`190 passed`，约 36 秒。这 190 项与上一组重叠，不能相加为独立覆盖数。
- `openspec validate implement-phase-f-shadow-run-and-cutover --strict --json`：valid，issues 为空。
- uv 执行的五个隔离探针：确认上文五种行为；无生产路由/审批写入，无 broker 网络访问。
- 当前资格 API、数据库状态和源码调用点只读核对，结果留存在 JSON。

uv 重核命令：

```sh
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python -m pytest -q -p no:cacheprovider --basetemp=tmp/phase-f-audit-uv-pytest-20261007 tests/test_cutover_wiring.py tests/test_read_cutover.py tests/test_route_single_active.py tests/test_broker_write_guard.py tests/test_intake_verification.py tests/test_dispatch_claims.py
```

探针源码：[probe.py](phase_f_audit_20261007/probe.py)。从仓库根目录可复现，并将新输出写到 tmp，保留本次审计快照：

```sh
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python docs/validation/phase_f_audit_20261007/probe.py tmp/phase-f-audit-recheck
```

探针读取当前默认数据库，并只在临时隔离目录写入测试审批/路由。所用资格桩、部署授权桩、故障注入和 broker session 均用于隔离反例，不能作为生产证据。AST 调用扫描用来定位接线线索，不能代替动态调用链证明。

本次没有运行生产工作流、访问真实 broker、核实 `LIVE-X` 来源、重跑完整套件或认证全部 A–E 历史验收。审计结论依据当前代码和可复现反例；没有把测试通过、文档勾选、静态导入或空账本解释为业务目标达成。
