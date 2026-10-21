# Tasks

> **顺序**（见 design.md D3、D10）：第 1–3 组只改代码并在隔离环境验证，生产上什么都不启用；第 4 组的每一步都需要你批准；第 6–9 组要等第 5 组的观察结论由你签字后才开始，每条退役也需要你明确同意。
>
> **全程不变：** 不解锁实盘、不开放 C3、不切 `live_trader`、不使用 IBKR Paper 下单、不做 IM 审批通道。

## 1. 测试夹具与基线（先于任何切换代码）

- [ ] 1.1 按冻结血缘重写 4 个决策路径夹具（`test_phase_d_e2e` 3 个、`test_chief` 1 个），隔离环境下使用无网络的模拟券商。验证：这 4 个测试通过；TEST_BASELINE 把它们从“夹具债”移出
- [ ] 1.2 在当前代码上复跑交易路径基线：22 项订单追溯、状态机、快照、独立验收，结果记入本 change 的 `verification.md`（初稿）。验证：全部通过，记录里写明测量条件与提交号

## 2. 切换所需实现（隔离环境验证，生产不启用）

- [ ] 2.1 主理人上下文只从研究快照渲染：去掉 `agents/chief/assemble.py` 对旧分析表（`sector_reviews`、`macro_reviews`、`technical_reviews` 等）的直读；某类投影缺失时阻断并出缺口报告。验证：新增测试断言上下文每段都带投影 ID 与 hash；`ats retirement zero` 对 `chief.legacy_table_direct_reads` 判 zero；缺一类投影时不建周期
- [ ] 2.2 主理人、风控与 `channel.context` 的交易历史与绩效改经 Internal State 读取，完整性不完整时在上下文中显式标出。验证：测试覆盖“未对账缺口被显示”；`store.direct_trade_reads` 的三个消费方判 zero
- [ ] 2.3 决策记录读取方改读审计表：`chief show`、上下文最近决策、`last_chief_run`、`recent_decisions`；镜像写 `mirror_revision_to_legacy` **保留**。验证：测试断言读取来自审计表；镜像写的测试仍通过；revert 演练后旧读取方能读到新写入的决策
- [ ] 2.4 新增终态“已批准未执行”：实盘锁定或演练模式下被批准的周期进入该状态并记录原因与执行前检查结果；修正演练批准被记为 `executed` 的问题；Internal State 与复盘统计单列该状态。验证：新增测试覆盖锁定批准、演练批准、统计不计入已执行；状态机原有测试全绿
- [ ] 2.5 新增命令组 `ats approval pending/show/approve/reject`（design.md D6）：
  - `approve` 必须给出至少 12 位修订 hash 前缀；
  - 复用 `resume_cycle` 在当前进程恢复执行；
  - `show` 显示“批准后不会下单”及锁定原因；
  - `ats chief run` 的前台交互收敛为批准或拒绝，去掉部分批准。

  验证：测试覆盖错 hash 拒绝、旧周期拒绝、已终结拒绝、重复批准返回首次结果、拒绝备注保存，以及即时通讯不可用时照常完成
- [ ] 2.6 审批与账本成对切换的代码（design.md D4 方案 B）：Clerk 的决策链联接与读模型发布只读审计表，去掉 Clerk 与报表对旧决策表的读取；新增只读核验脚本，列出仍读旧决策表的审批或账本代码路径。验证：核验脚本对当前代码输出为空；Clerk 的部分成交、迟到成交、撤单、拒单、人工订单、无法归因与重放测试全绿
- [ ] 2.7 Dispatcher 交易路径工作流：
  - 新建 `config/workflow/trade_schedules.yaml`：`clerk-daily` 交易日 16:30 ET，`chief-daily` 交易日 10:30 ET；
  - 注册两个任务，负责人默认 `paused`；
  - 新的 `chief-daily` 周期创建前，把前一日仍待审批或待修订的周期转为失效。

  验证：新增测试覆盖休市不触发、研究不完整不建周期且零模型调用、主理人失败不影响对账、重启补跑不重复、停用时记录跳过原因、前一日周期被取代
- [ ] 2.8 IBKR 连接标识：同一进程内每个连接分配不同的 client_id，跨进程规则不变。验证：测试在同一进程内并发开两个连接，client_id 不同；现有 broker 测试通过
- [ ] 2.9 实盘授权来源核实：授权记录增加核实状态（未核实、已核实、已作废）及核实依据；实盘门只采信已核实记录；核实或作废必须引用用户的确认原文；既有记录（含 `LIVE-X`）一律视为未核实，不删除。验证：测试覆盖未核实拒绝、作废后拒绝且原记录可读、缺确认引用的核实被拒；`ats live gate --reference LIVE-X` 返回“授权来源未核实”
- [ ] 2.10 更新 `docs/OPERATIONS.md` 第 7 节（草稿，随第 4 组生效）：命令行审批、两个交易路径工作流的停用与恢复、“六个边界开关不是权威状态”、回退步骤、三道实盘锁。更新 `docs/TEST_BASELINE.md` 中本组新增与改写的测试。验证：照文档能在隔离环境完成一次“查看待审批 → 批准 → 看到已批准未执行”

## 3. T0 交易路径人工核验（我准备，你签结论）

- [ ] 3.1 生成 `openspec/changes/switch-trade-path-and-retire-r5/verification.md`：按 design.md D1 列出 T0.1–T0.9，每项附核验方式、通过标准与自动证据。证据文件放在 `var/` 下，不放 `tmp/`。验证：每项都有可复核的证据引用
- [ ] 3.2 生产只读证据：用当天真实研究快照渲染一次主理人上下文（不建周期、不调用模型或复用已有结果），并在生产库副本（`var/` 下）上运行 `ats clerk run --dry-run`，覆盖最近 30 天，列出持仓与资金差异、无法归因记录及原因。验证：两份材料写进 verification.md，生产库未被写入（对比写前写后的行数）
- [ ] 3.3 只读查询 `LIVE-X` 的全部字段（签发人、时间、范围、账户、有效期、写入它的提交或命令记录），整理给你确认来源。验证：verification.md 有该节，未改动授权库
- [ ] 3.4 你逐项执行 T0 并填写结论、核验人与日期；未通过项登记为修复任务，修复后复核。验证：verification.md 中没有“未通过”项
- [ ] 3.5 记录 T0 结论（范围、提交号、已接受的缺口）并提交 verification.md。验证：提交存在

## 4. 生产切换（每步需你批准；上线后重启 `com.ats.dispatcher`，用 `launchctl list` 确认 PID）

- [ ] 4.1 S1 上线主理人改读（2.1、2.2）。验证：重启后用一次手动 `ats chief run` 演练（不批准），确认上下文来自快照，`ats retirement zero` 显示 Chief 两条 zero
- [ ] 4.2 S2 上线决策记录读取方迁移（2.3）。验证：`ats chief show` 显示审计表中的最近周期；旧表镜像写仍有新行
- [ ] 4.3 S3 上线审批与账本成对切换（2.4、2.5、2.6、2.9）。上线前后各跑一次 2.6 的核验脚本，把提交号写进 verification.md。验证：两次核验输出都为空；`ats approval pending` 可用
- [ ] 4.4 S4 启用 `clerk-daily`。验证：第一次定时运行 complete；对账结果与 3.2 的演练差异一致或可解释；同一窗口手动重跑不产生新事实
- [ ] 4.5 S5 启用 `chief-daily`。验证：第一次定时运行要么建周期并在待审批处暂停、进程正常结束，要么以研究不完整终结并出缺口报告；记录实际模型请求数
- [ ] 4.6 OPERATIONS 第 7 节改为“交易路径（已切换，实盘锁定）”，ARCHITECTURE §6 实施状态同步。验证：文档描述与 `launchctl list`、`ats approval pending` 的实际输出一致

## 5. 观察窗口（design.md D8）

- [ ] 5.1 每个交易日记录观察结果，写进本 change 的 `observation.md`：周期终态、审批结果、执行前检查结果、Clerk 对账差异、模型请求数、TWS 可用性，以及七条停止条件是否触发。验证：每个交易日一行，停止条件逐条有结论
- [ ] 5.2 窗口期内达到最低样本：你在命令行完成至少 3 次审批（批准、拒绝各至少一次），且至少 1 次主理人运行落在财报事件日。验证：observation.md 中可查到对应周期
- [ ] 5.3 若停止条件触发：停用两个工作流，按 D8 回退并记录原因；修复后从 4.x 中对应的步骤重新开始，窗口重新计算。验证：回退记录与重启后的 PID
- [ ] 5.4 窗口结论：汇总已批准未执行的检查失败分布、对账差异、模型请求总数与可用性问题，由你签字确认。同时把退役登记中的外部门 `trade_path_cutover` 记为已满足（依赖退役 change 1.3–1.5 的登记字段；若届时未完成，先完成那几项）。验证：结论已提交；`ats retirement decide` 对 R5 条目不再显示“外部门未满足”
- [ ] 5.5 按 design.md D9 生成实盘解锁前置清单 `live-unlock-prerequisites.md`，逐项写现状（满足、未满足、待你决定），不执行其中任何一项。验证：D9 的 8 项都有现状与证据引用

## 6. R5a 读取侧条目（每条需你同意；沿用退役 change 的 V1–V6）

- [ ] 6.1 `chief.legacy_table_direct_reads`：做 V1–V3，删除旧直读入口，登记改为 retired 并附清零证明。验证：`ats retirement decide` 判可退出；命中墓碑的读取返回退役原因
- [ ] 6.2 `store.direct_trade_reads`：同 6.1。验证：同 6.1
- [ ] 6.3 `sector_reviews`、`legacy_read_models`：在退役 change R1 清掉 `runtime.cli` 消费方之后，清掉 Chief 与 `channel.context` 消费方并退役，旧表保留只读。验证：历史行可读，新写入停止
- [ ] 6.4 `decision_roles.unwired_to_governed_read_api`：风控、Trader、Clerk、Fundamental 四个消费方经受治理读取面取数后退役。验证：`ats retirement zero` 对四个消费方判 zero
- [ ] 6.5 `macro_reviews`、`technical_reviews`：如果最后的读取方是 Chief，就一并退役；否则保持原登记并写明理由。验证：登记与代码一致
- [ ] 6.6 本批复验 V4–V6，TEST_BASELINE 与退役 runbook 记录本批。验证：重启后下一次 `chief-daily` 与 `clerk-daily` 正常完成

## 7. R5b 写入与入口侧条目

- [ ] 7.1 `memory.store.legacy_decision_write_path`：确认镜像写的读取方全部清零后，移除镜像写；旧 `cycles/decisions` 表保留只读。验证：V1–V3 通过；新周期不再写旧表；旧表历史可读
- [ ] 7.2 `trades.client_order_id.legacy_derivation`：旧式订单号行由 Clerk 标记为 `legacy_unknown`（不伪造审计字段）或对账承接后，移除旧派生式。验证：新写入全部使用 `cycle + revision + 序号` 派生；旧行可读并带标记
- [ ] 7.3 `scheduler.adhoc_journal_jobs`、`performance.legacy_snapshot`：生产触发只经 `clerk-daily`，`ats journal` 的独立对账入口与 `ats trader snapshot` 降为单步调试或删除（逐个判定）。验证：V1–V3 通过；`clerk-daily` 继续正常完成
- [ ] 7.4 `journal.report.render_ledger`：月度台账改读派生读模型。验证：同一月份新旧台账输出一致或差异可解释
- [ ] 7.5 `risk.checks.pre_trade_adapter`：确认 `runtime.cli` 的组合级巡检已迁到 `review_revision` 或声明退役后，删除适配层。验证：V1–V3 通过；风控测试全绿
- [ ] 7.6 本批复验 V4–V6，记录本批。验证：同 6.6

## 8. R5c 交易侧切换工具

- [ ] 8.1 用调用图逐个确认 `joint_cutover`、`boundary_drill`、`acceptance_runner/reports/matrix`、`paired_business`、`isolated_entry`、`shadow_*` 的生产调用方与测试依赖。被隔离测试与 22 项追溯依赖的部分标为保留；design.md D10 的长期资金安全机制一律保留。验证：每个模块都有删除或保留的结论
- [ ] 8.2 删除确认可删的模块和命令 `ats cutover`、`ats drill`、`ats shadow`（保留一个版本的提示桩），同步冻结面与覆盖清单，归档命令用法样例到 `docs/archive/`。验证：22 项追溯、架构守卫与 assurance_surface 测试通过；`ats live drill` 仍可用
- [ ] 8.3 本批复验 V4–V6，记录本批。验证：同 6.6

## 9. R5d 读路由与资格机制收窄

- [ ] 9.1 前提核对：退役 change 的 R1（`consumer-cutover` 改名为数据健康命令）与 R2 已完成，本 change 的 6–8 组已完成。验证：`ats retirement decide` 对本组条目只剩本组内依赖
- [ ] 9.2 `consumer_api.read_input` 收窄为“查声明 → 查数据产品 → 读”：保留消费者身份、允许的产品与 scope 声明、读取前检查与 scope 绑定；去掉路由表、资格账本、指纹绑定与边界接线证据的依赖。验证：新增测试覆盖读取未声明产品被拒、产品不合格显式失败、没有路由表与资格记录时照常读取、scope 不一致被拒；6 个研究分析师和 5 个交易路径角色的读取测试全绿
- [ ] 9.3 删除 `cutover`、`cutover_routing`、`scoped_routes`、`consumer_cutover` 路由表、`consumer_reads`、`read_windows`、`read_recovery`、`invocation_bindings`、`boundary_evidence`、`cutover_wiring`，以及 `assurance_surface` 中只为资格存在的指纹清单；历史路由与资格记录保留只读。验证：V1–V3 通过；架构守卫测试通过
- [ ] 9.4 本批复验 V4–V6：重启后一个完整周期（工作日研究、主理人、Clerk，周六宏观与行业）无因退役导致的失败。验证：观察记录写进 observation.md

## 10. 规格与收尾

- [ ] 10.1 退役 change 归档后，用 openspec-update-change 在本 change 补一条删除：退役 change 新增的“迁移完成后研究读路由必须固定为新路径……”，因为它已被“消费者必须按声明的数据产品与业务 scope 读取”取代。同时核对 SEC 原文非阻塞处理已并入数据产品检查。验证：`openspec validate switch-trade-path-and-retire-r5 --strict` 通过
- [ ] 10.2 复核 `data/target-dataflow-assurance` 与 `workflow/cutover-control` 中其余只服务于资格或接入核验的要求（如“十个角色的实际接入必须以运行记录核验”“隔离接入验收不得以生产资格为前置”），需要删改的用 openspec-update-change 补增量。验证：每条都有保留或删改的结论
- [ ] 10.3 文档：ARCHITECTURE §6、§10、§11，OPERATIONS 第 7 节，退役 runbook，README，同步到 R5 之后的状态。验证：文档中的命令都能照写运行
- [ ] 10.4 全量测试一次，与基线逐项对照，并登记删除的测试与任何新失败的归因。验证：TEST_BASELINE 有本 change 的权威测量
- [ ] 10.5 在退役 change 归档之后，`openspec validate --all --strict` 通过。验证：命令输出通过
