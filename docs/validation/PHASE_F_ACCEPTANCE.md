# Phase F 验收记录（`implement-phase-f-shadow-run-and-cutover`）

2026-10-09 本轮 **13.3 完成**，当前 **80/116 完成**、36 项待办。实际持久停止、全部在途/周期盘点、只读迟到对账、已验收 simulation 换代恢复与失败持续冻结验证通过；停止跨 uv 进程生效。固定源码 Routine/Event 独立重放 6 项通过，新报告 unsigned；闭包 465，旧报告保留历史。见 [交易安全停止验证](PHASE_F_TRADE_SAFE_STOP_2026-10-09.md)。生产/TWS/资格未由此完成，C3 保留。

2026-10-09 本轮 **11.3、5.15 完成**，当前 **79/116 完成**、37 项待办。真实交易仲裁与 exact-scope 联合冻结恢复验证通过，固定源码 Routine/Event 独立新进程重放 6 项通过；源码/提示闭包 **465**。新报告 unsigned，旧报告保持历史指纹；生产资格/切流、TWS 未测，C3 保留。实际覆盖与阶段失败见 [联合与交易恢复验证](PHASE_F_JOINT_AND_TRADE_RECOVERY_2026-10-09.md)。后续 13.3 可推进，13.1/13.2 仍待正式读/调度适配。

2026-10-09 本轮 **7.8 → 13.4 → 13.5 完成**，当前 **76/116 完成**、40 项待办。动态索引只读复算原始 report/input/run/refs；Routine/Event 四轮非空订单完整链与仲裁/提交拒绝通过，58 项有效检查通过，生产 52 trades / 13 fills 在本轮前后逐行摘要不变。报告仍 unsigned，TWS/生产资格与激活未测；详见 [动态索引与全链集成验证](PHASE_F_INTEGRATION_2026-10-09.md)。下文原时点结果保留，不以历史口径覆盖当前状态。

2026-10-09 本轮 **11.2 → 3.10 完成**：选定 Routine/Event 六类研究、Chief/Risk/人工批准、Trader A/FakeBroker/Clerk 与同输入跨进程重放均通过，全部必需断言 passed；纯研究及缺数据签核拒绝也通过。核心 29 项和修正 SQL-owner 夹具后的启动 11 项通过，相关回归 274 passed。当前 **73/116 完成**，43 项待办，第 3 组 10/10。完整源码/提示闭包 **464**、保护并集 **100**。三个通过报告保持 unsigned；生产资格、授权、owner、路由及 C3 不变。见 [独立新入口与隔离授权验证](PHASE_F_INDEPENDENT_RUNNER_2026-10-09.md)。原失败、旧记录和旧报告保留。

本轮 **3.4–3.6 完成**：空库/混合历史/重复重开保留 native revision，实际跨进程 Clerk 批准与归因恢复；新入口/重放报告追加保存，独立签核，必需失败/未测和撤销/漂移拒绝；无旧侧真实 Technical 与新进程 CLI 已通过专项验证。见 [新入口报告与恢复验证](PHASE_F_ACCEPTANCE_REPORTS_2026-10-09.md)。完整交易的阶段输入报告仍由 3.10 承接，不是生产资格或全范围完成。

2026-10-09 当前状态：新入口按需求独立验收，旧侧仅诊断及退出安全。本轮 **3.4 → 3.5 → 3.6 已完成**：修复兼容迁移重开污染、追加式需求报告/签核及实际记录 checker；当前 **71/116 完成**，45 项待办，第 3 组 9/10。旧报告保留历史类型，旧签核不转换；3.10 仍须独立 capture、选定全 workflow 窗口与完整交易重放，完整交易还依赖 11.2。保护并集 **100 个路径**，当前完整源码/提示闭包 **463**，历史 458/461 证据保留。生产资格、授权、owner、路由及 C3 未改变。下文历史时点结果保留，当前任务和本段优先。


本轮 **3.2、5.7 完成**：运行前固定新需求版本、实际计划及 task/read scope、所选模式、必需断言与输入程度；实际读消费支持逐 scope 安全停止、已验收版本恢复和明确选择的旧目标，核对版本/证明/资格/退役/真实 refs，拒绝撤销、漂移及旧 worker 发布。133 项相关回归通过，含实际 Chief 入口、state API、历史工具回归；新需求断言执行及完整报告/runner 仍待后续。见 [新需求矩阵与恢复验证](PHASE_F_NEW_REQUIREMENTS_AND_RECOVERY_2026-10-09.md)。

## 2026-10-09 新入口验收标准与任务调整

| 验收对象 | 必需标准 | 不作为阻塞条件 |
|---|---|---|
| 六分析与 Chief | 所选 Event/Routine、其他必需类别/任务 scope、治理输入/投影/血缘/时效与实际成功记录 | 旧分析输出不同、旧侧失败、未选模式没运行 |
| Risk/审批/Trader/Clerk | 审查/完整批准修订、A 两阶段报价、非空订单与无网络 FakeBroker、幂等及部分/迟到成交恢复 | 旧算法不同、旧 Clerk 业务红项 |
| 新调度 | 固定需求/配置/事件的独立预期集合，实际触发覆盖、去重与发布权 | 旧调度是否成功、新旧触发并集 |
| 报告与重放 | 新入口与新进程实际 run/refs、固定输入/版本、六面需求断言，必需项全部 passed，有效签核 | legacy run ID、左右结果相等、旧差异接受 |
| 故障安全/退出 | 经真实入口验证安全停止或已验收版本恢复，旧入口失权，在途处置、历史保留 | 必须恢复旧业务、补齐废弃旧用途 |

旧错误若仍影响新入口、共享事实、必需历史状态或写权限，按对应新需求失败处理，不能排除。未测如实列 untested；not-applicable 仅用于事先固定的无关面，optional/no_coverage/partial 原政策不变。全仓测试仍跑并逐失败归因；废弃旧业务经可达性/隔离证据排除后不阻塞新范围，不能凭“既有失败”排除新需求错误。

实施缺口：旧 capture-business/run-pair 仅保留历史诊断用途；新报告/checker 和独立 Dispatcher 重放入口已交付，独立 capture/全选定范围及完整交易窗口仍待 3.10。原八项重开中的 3.2–3.6、5.7 已按任务粒度完成，12.2/12.3 仍待办；原证据不删，旧报告不能代替新报告，不表示新契约整体已通过。

依赖顺序：**3.2、3.3 与 5.7 已完成**，下一步 **3.4 → 3.5 → 3.6**；完整交易验收补 **11.2 → 3.10**。3.10 不再以 3.9 双跑为前置；6.1 加入新报告链/3.10 的最终实现，再由 6.4 追加资格。12.3 在 13.1–13.3/13.10 安全恢复实测后核验观察窗口。无关研究子范围按 0.3 独立推进，生产/实盘授权不扩大。




本轮 **7.7、7.17 完成**：实际 scope/产物绑定逐消费者处置，保留 optional/no_coverage/FactSet/partial；旧 Clerk 缺陷只登记，切流适配仍按 4/5 组完成。完整指纹闭包与十角色漂移/旧契约回归、最终隔离交易恢复通过。当前 **66/116 完成，50 项待办**。详见 [逐消费者处置与 A 回归报告](PHASE_F_CONSUMER_DISPOSITION_AND_A_REGRESSION_2026-10-08.md)。7.8 仍待 3.10，6.1/6.4 仍待最终冻结前置；生产资格/切流及 C3 不改变。

本轮 **7.3、7.5 完成**：实际观点输入/发布 lineage 与旁路拒绝通过；真实六角色研究 → Chief/Risk/审批/Trader/FakeBroker → Clerk 的非空订单、多轮审查、价格变化重批、部分/迟到成交、绩效重建及跨进程恢复通过。当前 **66/116 完成，50 项待办**。新增验收 13 passed，核心回归 96 passed；扩大检查两条既有失败保留并在本轮前源码复现。详见 [观点与完整执行恢复报告](PHASE_F_OPINION_AND_EXECUTION_2026-10-08.md)。生产资格/切流与 C3 不改变。

本轮按 **9.3、9.6 → 7.2 → 7.4** 完成研究读模型、六角色实际入口及所选模式完整性门禁。当前 **66/116 完成**，50 项待办。Event/Routine 两条实际 Chief No Action 与跨进程校验通过；隔离完整链已由 7.5 补齐；生产资格、切流仍未达成。结果/fixture 范围/保留失败见 [研究读模型与门禁报告](PHASE_F_RESEARCH_GATE_2026-10-08.md)。

旧口径下 **5.7、7.11 完成**（5.7 本次已重开）：实际读入口核验 scope 回退证明、退役状态与真实可读性；五角色消费治理产品/投影、state API、受限行情/批准授权与执行回报，保留真实 refs。进度 **66/116**，50 项待办。验证与剩余边界见 [安全读与消费边界报告](PHASE_F_SAFE_READS_2026-10-08.md)。

本轮 **7.1 → 3.7 完成**，实际隔离入口及影子意图/提交拒绝/发布防污染见 [验证报告](PHASE_F_SHADOW_LEDGER_2026-10-08.md)。进度 **66/116**；十角色动态验收与实际业务双跑仍待后续任务。

旧口径下 3.1/3.6 完成（3.6 本次已重开）：见 [输入与报告门禁报告](PHASE_F_SHADOW_FOUNDATION_2026-10-08.md)。进度 **66/116**，50 项待办；完整输入恢复与强制报告校验已实现，3.9/3.10 的实际业务影子验收仍未完成。

## 当前验收状态：2026-10-08 裁决后修正

进度：**66/116 完成**，含已保留模块、前置确认、启动/broker、scope 恢复、业务读/写接线与 Trader A 核心四项；**门禁实现验收、生产读/调度切流、实盘切流均未完成**。当前结果见 [实际审计](PHASE_F_ACTUAL_COMPLETION_AUDIT_2026-10-07.md)、[前置确认](PHASE_F_RECOVERY_PREFLIGHT_2026-10-07.md)及 [任务清单](../../openspec/changes/implement-phase-f-shadow-run-and-cutover/tasks.md)。

- wired 声明和静态 5/10 不是 actual enforced/实跑；审批关闭写入反例已由 5.3 修复；broker 无 grant 到达 session 的反例已经修复并有实际出口回归。
- exact-scope 门禁已接实际入口，研究读模型已验收，切前业务回退仍待完成，报告/checker 缺省放行、逐 scope 存储/解析已实现，正式读执行器与联合半迁移仍待修复。旧调度无 claim 不能靠“再跑一次旧 job”补证据。
- 影子输入/实际双跑/非空意图和提交拒绝、修复后业务回退尚未验收；三条退役仍 pending。当前账本缺失不能全部解释为 TTL。
- A 已接实际报价/规范化/审查/审批与执行条件入口；完整隔离 FakeBroker 可模拟接收和成交，生产 C3 保持停用，IBKR（含 Paper）写入需另行明确授权。完整研究→交易→Clerk 隔离恢复已由 7.5 验收；十角色动态补验仍待 7.17，见 [方案 A 报告](PHASE_F_TRADER_A_2026-10-08.md)。
- DEP-2026-10-06-A 有记录，原始来源及动作范围按 8.6 核对；LIVE-X 有记录但来源未核实，不据其认定实盘授权。前置代码为 6b72d05，tests 未跟踪的限制已记录，原全量测量不等于有效回归归因。

0.1/0.3/0.4/0.6 前置材料已完成；2026-10-08 用户已确认选定模式不可替代且其他必需类别齐全；0.2/0.5 已完成，第 0 组 6/6 收尾。本轮不签发资格、不改生产路由/授权、不连接 broker。


1.1/2.2/2.3/2.8 局部工程验收完成：相关回归 294 passed，实际 IBKRBroker 实现经模拟 transport 与子进程验证，详见 [恢复报告](PHASE_F_BROKER_ENFORCEMENT_2026-10-08.md)。真实网关只读验证、全交易链及真实 shadow 仍未验收。


上轮 5.2/5.5/5.14 的机制验收及 324 passed 结果仍见 [scope 恢复报告](PHASE_F_SCOPED_ROUTING_2026-10-08.md)，该报告保持原时点。

新增完成 **5.3/5.6**：Analyst 新旧投影、审查/审批和 Clerk 已接入真实发布控制，隔离/影子检查实际 SQLite 连接与 Clerk 子步骤落库。CLI/Workflow/Dispatcher、Chief graph/快照及 native API 按当次实体/时间/事件 scope 查询资格，历史复用重查、不同范围不借资格、混合任务独立阻断。相关回归 **562 passed**，新增 40 场景通过；另四条既有失败在有限 HEAD 模块对照中复现并保留，不宣称全量全绿。共享 consumer_api 漂移影响十角色，旧证据保留，最终冻结补验继续由 7.17/6.1/6.4 承接。上述报告时点尚缺的 5.7/7.11 已由本轮补齐；完整读模型验收 9.3/9.6 已完成；联合提交 5.15 仍待实施。生产资格/授权/路由及 C3 未改变。详见 [业务接线报告](PHASE_F_BUSINESS_WIRING_2026-10-08.md)。

## 历史原文（原 13.8 验收时点，结论已修正）

原文本完整保留；其中“门禁已达成、只缺授权证据、无实盘授权记录”等不再是当前结论。对应模块结果只证明当时的测试范围，重开的实际接线/影子/回退仍需验收。

本文件是 Phase F 的**验收产物**（13.8）。它回答一个问题：**这一阶段交付了什么、
凭什么说交付了、以及哪些东西明确没有交付。**

与其它文档的分工：

| 文档                                | 记什么                                 |
| ----------------------------------- | -------------------------------------- |
| 本文件                              | **测出来是什么**——验收结论与其证据索引  |
| `docs/TEST_BASELINE.md`              | 怎么测、怎么归因                       |
| `docs/validation/PHASE_F_GROUP_PROGRESS.md` | 实施过程中每组验证到什么程度、为什么没做 |
| `docs/validation/PHASE_F_CUTOVER_RUNBOOK.md` | 怎么操作                         |

**一条贯穿全文的纪律**：本记录里每个结论后面都跟着它的来源。**回溯不到证据账本
或运行产物的结论，不写进来**；查不到、读不了、部分合格，都按它本来的样子记，不
折算成通过。

---

## 1. 收尾状态总览（13.9）

| 状态                               | 是否达成 | 依据                                                         |
| ---------------------------------- | -------- | ------------------------------------------------------------ |
| **门禁实现验收完成**               | **是**   | 第 2 节全部证据 + 13.6 回归判决（0 例本 change 引入的回归）  |
| **生产读/调度切流完成**             | **否**   | 资格 0/10；且两项切换任务未获执行授权（第 5 节）             |
| **实盘切流完成**                   | **否**   | `LIVE-*` 实盘授权未签发；trader 自动下单处于 C3 停用（用户裁决） |

**「门禁实现完成」不等于「可以切流」。** 前者说机制、门禁、演练、验收都齐备且经过
验证；后者说真实路由已经切换。前者已达成，后者缺的是**授权与证据**，不是代码。

---

## 2. 接收矩阵

六条边界各自的状态与声明的调用点。**「未接线」与「已接线但未切换」是两种不同状态**，
前者是缺陷，后者是尚未执行。

| 边界               | 路由      | 接线 | 声明的权威写入方                     | 可切换 |
| ------------------ | --------- | ---- | ------------------------------------ | ------ |
| `projection_read`  | legacy    | 是   | `cutover_routing.read_route`         | 见 §4  |
| `analyst_output`   | legacy    | 是   | `cutover_wiring.guard_analyst_output` | 见 §4  |
| `dispatcher_schedule` | legacy | 是   | `dispatch_claims.claim`              | 见 §4  |
| `approval_lifecycle` | legacy  | 是   | `cutover_wiring.guard_approval_write` | 见 §4  |
| `clerk_publication` | legacy   | 是   | `cutover_wiring.guard_clerk_publication` | 见 §4 |
| `live_trader`      | disabled  | 是   | `broker_write_guard.check_grant`      | **否**（无实盘授权） |

**实测读数**（`ats cutover preflight`，2026-10-07）：六条边界全部 `wired: true`，
路由全部 `legacy`（`live_trader` 为 `disabled`）。**没有一条被切到 target。**

`live_trader` 的 `disabled` 是**正确状态**而非待修项：它表示「此边界当前没有活跃
路由」，而下单能力另有仲裁层（`broker_write_guard`）在每次提交时重验
`(route_id, generation, environment, account)`。把它写成 `legacy` 会暗示存在一条
可下单的旧路径——而按用户 2026-10-06 裁决，自动下单处于 C3 停用。

---

## 3. 逐消费者资格

**实测：0/10 合格。** 逐个调用 `assurance.qualification()` 的读数（2026-10-07）：

| 消费者        | 资格      | 证据 TTL | 备注                                     |
| ------------- | --------- | -------- | ---------------------------------------- |
| `layer`       | ineligible | 90 天    | 缺全部 7 类证据                          |
| `sector`      | ineligible | 90 天    | 同上                                     |
| `information` | ineligible | 30 天    | 同上                                     |
| `fundamental` | ineligible | 30 天    | 同上                                     |
| `macro`       | ineligible | 30 天    | 同上                                     |
| `technical`   | ineligible | 1 天     | 缺 5 类证据                              |
| `chief`       | ineligible | 7 天     | 同上                                     |
| `risk`        | ineligible | 7 天     | 同上                                     |
| `trader`      | ineligible | 1 天     | 行情契约未授权（见 §7）                  |
| `clerk`       | ineligible | 1 天     | 同上                                     |

**「0/10」是诚实的读数，不是失败。** 证据账本（`dataflow_assurance_events`）从未
建立，因此没有任何消费者持有证据。**未取证不等于不合格**——但它同样不等于合格，
所以门是关的。

**TTL 决定了取证时点，这是排期的硬约束**：

| TTL    | 消费者                      | 何时取证                       |
| ------ | --------------------------- | ------------------------------ |
| 1 天   | technical、trader、clerk    | **临切流前一日**，提前补即过期 |
| 7 天   | chief、risk                | 切流前一周内                   |
| 30 天  | information、fundamental、macro | 切流前一个月内             |
| 90 天  | layer、sector              | 可提前取证                     |

**提前补 1 天档等于白补**——切流那天证据已过期，门重新关上，且因为「曾经通过过」
更容易被误认为仍然通过。

---

## 4. 接入核验与逐批清单

### 4.1 十角色接入核验（F.0.3–F.0.5）

**实测：5/10 合规。** digest `cf0ea1e27a6577639eaa652c34b4c0011e44f49f1d1dc85942acd7e1391bc867`

| 消费者                       | 合规 | 原因码                              |
| ---------------------------- | ---- | ----------------------------------- |
| layer / sector / information / macro / technical | 是 | —                     |
| chief / risk / fundamental   | 否   | `governed_read_surface_not_reached`  |
| clerk                        | 否   | `governed_read_surface_not_reached`  |
| trader                       | 否   | `governed_read_surface_not_reached` + `disabled_bypass` ×2 |

**`disabled_bypass` 的确切含义**：`trader/execute.py:358-359` 的 `_last_price_enabled()`
里保留了 `ats.data` / `ats.schemas.market` 的导入，**当前不可达**（下单已停用）。
保留是为方案 A 恢复时使用，但**保留即阻断**——它仍读未授权的行情契约。

**「五个角色未触达受治理读取面」已登记为 `PF-7-02`，只登记未修**（修它要触及受指纹
面 `state_api.py`，且属 Phase F 之后的接线工作）。

### 4.2 逐批清单与 dry-run

**实测（13.8 本轮登记演练引用后重跑）：5 批 · ready 1 · blocked 2 · not_switchable 1 · verified_in_place 1**

| 批次                       | 类别                  | 结论                | 资格       | 可切换 |
| -------------------------- | --------------------- | ------------------- | ---------- | ------ |
| `batch-collection`         | collection_publish    | verified_in_place   | 不适用     | 是     |
| `batch-schedule`           | schedule              | **ready**           | 无消费者   | **是** |
| `batch-research-read`      | research_read         | blocked             | 0/6        | 否     |
| `batch-live-trader`        | live_trader           | blocked             | 0/1        | 否     |
| `batch-internal-approval`  | internal_state_approval | not_switchable    | 0/4        | 否     |

**登记演练引用后 `PF-B-03` 已解除**：`batch-schedule` 从 `not_switchable` 变为
**`ready`**，`batch-research-read` / `batch-live-trader` 从 `not_switchable` 降为
**`blocked`**。两者的区别很重要：`not_switchable` 说「切换不是问题，能退回来才是」，
`blocked` 说「能退回来，但缺证据」。**后者是更接近可切的状态。**

`batch-schedule` 无消费者，故不需要逐消费者资格——**这是它 ready 的唯一原因**，
不代表调度路径已具备切流条件（仍缺 §5 的执行授权与 `PF-B-06` 的账本）。

**`batch-internal-approval` 保持 `not_switchable` 且不登记演练引用**：它覆盖
`approval_lifecycle` + `clerk_publication` 两条边界，**本次三条演练都没有触碰过
它们**。登记一个「已演练回退」引用会是伪造证据。

---

## 5. 三边界独立回滚演练（13.1–13.3）

**三条边界各自独立演练并各报结论**——一条「回退正常」覆盖三边界的报告，会在其中
一条已坏的情况下仍然通过，因为另外两条把它带过去了。

| 演练                 | 边界                 | 结论      | 检查项 | 证据库                                             |
| -------------------- | -------------------- | --------- | ------ | -------------------------------------------------- |
| `F13-READ-20261007`  | `projection_read`    | completed | 7/7    | `var/phase_f_cutover.F13-READ-20261007.drill.sqlite`      |
| `F13-SCHED-20261007` | `dispatcher_schedule`| completed | 6/6    | `var/phase_f_schedule_switch.F13-SCHED-20261007.drill.sqlite` |
| `F13-TRADE-20261007` | `live_trader`        | completed | 5/5    | `var/phase_f_routes.F13-TRADE-20261007.drill.sqlite`      |

### 5.1 读边界：整条配对链回退，不是单边

`projection_read` 与 `dispatcher_schedule` 由控制平面的 `INCOMPATIBLE` 表推导，
**整条链回到 `legacy`**，回退后 `check_compatibility` 仍判相容。只切链上的一条正是
它要拒绝的半迁移中间态。

记录保全读数（回退前 → 回退后）：`revisions 33 → 33`、`approvals 0 → 0`、
`shadow_intents 0 → 0`。**注意 `shadow_reports: -1`**——该库在本机不可读，
`-1` 表示「查不了」而非「零」。**报告未把 `-1` 折算成 0**，这是 `unreadable`
与 `clean` 必须分开的又一个实例。

### 5.2 调度边界：按已执行触发去重

2 个逻辑触发各有执行记录，回退时**两者均被跳过**——一个逻辑触发只执行一次。
反向验证同样成立：声明 1 个未确认触发时，**回退被拒绝**（`refused`），因为把
「没有记录」当作「没有执行过」会让一次回退把每个触发都跑第二遍。

### 5.3 交易边界：正确拒绝，且不静默转入故障路由

**实盘授权门被咨询并拒绝**（`no_live_authorisation`）——回退改变的是「谁可以发真实
订单」，与前向切换需要同等授权。生产路由库经指纹比对**未被写入**。

`--fallback-unavailable` 时结论是 `held`（保持现状）而非退回成功：强行切到一个
不可用的目标，等于把一次故障换成另一次故障。

### 5.4 演练未证明的事

每条演练记录都带 `limits` 字段。**演练通过不等于切流获准**：

- 不验证消费者实际读到的是旧数据
- 不验证券商侧行为（连接、报单、成交、部分成交、拒单）
- 不验证实盘或部署授权会被批准

---

## 6. 端到端验收（13.4–13.5）

### 6.1 可追溯性

**机制就绪，真实数据未产生。** 实现覆盖四���链（研究快照 → 决策修订 → 风控审查 →
人工批准），缺任一即判 `untraceable`，其所在切换判为不合规切换。三种状态
（`traceable` / `untraceable` / `unreadable`）严格分开。

**当前读数：影子账本为空**（0 意图 / 0 提交尝试），因此**没有订单可追溯**。
按 module 自身的纪律，空账本**不是「全部通过」**——它是「未测」。

### 6.2 影子期无未审批真实下单

**结论：机制就绪，但影子期尚未运行，故无验收结论。**

| 证据                                       | 状态       | 说明                             |
| ------------------------------------------ | ---------- | -------------------------------- |
| 新路径券商提交调用与拒绝审计记录           | **未产生** | 影子期未运行                     |
| 能力校验结果                               | **未产生** | 同上                             |
| 隔离演练券商模拟接收                       | **未产生** | 同上                             |
| `trades` 未污染（独立验收）                | 0 行污染   | **是真实读数**，但**不是主要证据** |

**`prohibition_exercised: false`** —— 影子运行**没有到达下单调用点**，因此
**不能**用它证明「禁令会在那里生效」。`ats shadow attest` 如实报出这一点。

**`trades` 干净不是主要证据的理由**：从未被写入的账本无法证明订单没有到达券商
又返回。因此「券商有回执而本地无记录」被单列为**停止条件**，而 `trades` 只作
**独立**验收。

---

## 7. 回归判决（13.6）

**`45 failed / 3072 passed / 0 errors / 2 deselected`（2533.42s）。0 例本 change 引入的回归。**

| 类                             | 例数 | 判据                                             |
| ------------------------------ | ---: | ------------------------------------------------ |
| 既有失败                       |   27 | `git worktree HEAD` 上**逐条 id** 对照同样失败 |
| 停用下单所致（用户 C3 裁决）   |   16 | 隔离运行逐条确认根因，唯一                       |
| 退役表守卫白名单（本轮已修）   |    1 | 已验证守卫未被削弱                              |
| 顺序依赖                       |    1 | 隔离运行通过                                     |

证据：`docs/validation/baseline/`（JUnit 全文 + A/B 两类 id 清单）。

**全量基线在默认参数下跑不完**——`test_factset_index_semantic_local::
test_semantic_core_passes_with_exact_index_and_technology_reviews` 是预存在 hang
（36% 处卡住），13.6 的读数靠 `--deselect` 该条取得。**这是结论的前提条件。**

---

## 8. 明确未交付的项

**这一节与第 2 节同等重要。** 上面写的每一项「是」，都对应这里的一项「否」。

| 未交付项                     | 根因                                              | 谁能解           |
| ---------------------------- | ------------------------------------------------- | ---------------- |
| 生产读路径切流（9.4）        | 资格 0/10；需临切流前一日取证                     | 取证动作 + 授权  |
| 生产调度切流（10.4）         | 调度账本为空，7 个旧 job 未登记认领                | **需用户决定**   |
| 实盘切流                     | `LIVE-*` 未签发；trader 自动下单 C3 停用          | **需用户签发**   |
| `batch-internal-approval` 切流 | 无演练覆盖其两条边界；资格 0/4                 | 补演练 + 取证    |
| 影子期实跑                   | 未运行；`prohibition_exercised: false`           | 影子期窗口       |
| `PF-7-02` 五角色接线         | 修它触及受指纹面 `state_api.py`                  | Phase F 之后     |
| 1 例预存在 hang              | 事实集语义 PDF 管线，与本 change 无关             | 事实集域负责人   |

---

## 9. 复现方式

```bash
# 门禁与守卫
openspec validate implement-phase-f-shadow-run-and-cutover --strict
uv run python -m pytest tests/test_architecture_guards.py tests/test_cutover_guards.py -q

# 资格与接入
uv run python -m ats.runtime.cli intake report
uv run python -m ats.runtime.cli batch dry-run

# 三边界回退演练（写入各自演练库，不碰生产路由）
uv run python -m ats.runtime.cli drill read-rollback    --drill-id <id> --batch-id batch-research-read \
    --batch-db var/phase_f_batches.sqlite --cutover-db var/phase_f_cutover.sqlite
uv run python -m ats.runtime.cli drill schedule-rollback --drill-id <id> --trigger <t> \
    --executed-json '{"<t>":["ref"]}' --authorisation DEP-... --batch-db var/phase_f_batches.sqlite
uv run python -m ats.runtime.cli drill trade-rollback    --drill-id <id> --route-db var/phase_f_routes.sqlite

# 端到端核验
uv run python -m ats.runtime.cli drill acceptance --run-id <id>
```

**`ats drill` 没有 `--apply`**：本组动作不会切实盘、不会改生产路由，因此不存在
一个能被误用来切真实路由的开关。
