# Tasks

> **顺序**（见 design.md D3、D10）：第 1–3 组只改代码并在隔离环境验证，生产上什么都不启用；第 4 组的每一步都需要你批准；第 6–9 组要等第 5 组的观察结论由你签字后才开始，每条退役也需要你明确同意。
>
> **全程不变：** 不解锁实盘、不开放 C3、不切 `live_trader`、不使用 IBKR Paper 下单、不做 IM 审批通道。

## 1. 测试夹具与基线（先于任何切换代码）

- [x] 1.1 按冻结血缘重写 4 个决策路径夹具（`test_phase_d_e2e` 3 个、`test_chief` 1 个），隔离环境下使用无网络的模拟券商。验证：这 4 个测试通过；TEST_BASELINE 把它们从“夹具债”移出
- [x] 1.2 在当前代码上复跑交易路径基线：22 项订单追溯、状态机、快照、独立验收，结果记入本 change 的 `verification.md`（初稿）。验证：全部通过，记录里写明测量条件与提交号

## 2. 切换所需实现（隔离环境验证，生产不启用）

- [x] 2.1 主理人上下文只从研究快照渲染：去掉 `agents/chief/assemble.py` 对旧分析表（`sector_reviews`、`macro_reviews`、`technical_reviews` 等）的直读；某类投影缺失时阻断并出缺口报告。验证：新增测试断言上下文每段都带投影 ID 与 hash；`ats retirement zero` 对 `chief.legacy_table_direct_reads` 判 zero；缺一类投影时不建周期
- [x] 2.2 主理人、风控与 `channel.context` 的交易历史与绩效改经 Internal State 读取，完整性不完整时在上下文中显式标出。验证：测试覆盖“未对账缺口被显示”；`store.direct_trade_reads` 的三个消费方判 zero
- [x] 2.3 决策记录读取方改读审计表：`chief show`、上下文最近决策、`last_chief_run`、`recent_decisions`；镜像写 `mirror_revision_to_legacy` **保留**。验证：测试断言读取来自审计表；镜像写的测试仍通过；revert 演练后旧读取方能读到新写入的决策
- [x] 2.4 新增终态“已批准未执行”：
  - 实盘锁定或演练模式下被批准的周期进入该状态，记录原因与执行前检查结果；
  - 锁定模式下 Trader 先完成只读检查（授权字段、新鲜度、实时报价、资金上限），最后在券商交接点拒绝，路由与锁的检查仍先于任何券商接触；
  - 修正演练批准被记为 `executed` 的问题；
  - Internal State 与复盘统计单列该状态。

  验证：新增测试覆盖锁定批准（含报价检查结果入账）、演练批准、统计不计入已执行，以及锁定下零券商提交；状态机原有测试全绿
- [x] 2.5 新增命令组 `ats approval pending/show/approve/revise/reject`（design.md D6）：
  - `approve`、`revise`、`reject` 都必须给出至少 12 位修订 hash 前缀；
  - `revise --note` 驳回当前修订并附修改意见：主理人带意见重新修订（一次模型请求），新修订重新风控后再送审；最多 2 次，用完只能批准或拒绝；`reject` 直接终结周期，备注可选；
  - `pending` 只列需要下单且已通过风控的修订；
  - 复用 `resume_cycle` 在当前进程恢复执行；
  - `show` 显示“批准后不会下单”及锁定原因；
  - `approve` 先重取账户快照，只对同一修订用确定性风控重审（不重新规范化订单、不调用模型），显示差异后执行，全程不超过 60 秒；重审通过，这一次批准即生效，不需要第二次人工审批；重审得出不同结论就拒绝这次批准，由你选 `revise` 或 `reject`；
  - 周期进入待审批时发单向文字提醒（不带链接），被次日周期取代时再提醒一次；
  - `ats chief run` 的前台交互收敛为批准、驳回修改或拒绝，去掉部分批准。

  验证：测试覆盖错 hash 拒绝、旧周期拒绝、已终结拒绝、重复批准返回首次结果、拒绝备注保存、驳回修改生成新修订且重新风控再送审、驳回修改超过 2 次被拒、批准时快照已过期会先重审且不要求第二次批准、重审得出不同结论拒绝批准、`pending` 不列未通过风控的过程、提醒不含链接，以及即时通讯不可用时照常完成
- [x] 2.6 审批与账本成对切换的代码（design.md D4 方案 B）：Clerk 的决策链联接与读模型发布只读审计表，去掉 Clerk 与报表对旧决策表的读取；新增只读核验脚本，列出仍读旧决策表的审批或账本代码路径。验证：核验脚本对当前代码输出为空；Clerk 的部分成交、迟到成交、撤单、拒单、人工订单、无法归因与重放测试全绿
- [x] 2.7 Dispatcher 交易路径工作流：
  - 新建 `config/workflow/trade_schedules.yaml`：`clerk-daily` 交易日 16:30 ET，`chief-daily` 交易日 10:30 ET；
  - 注册两个任务：在时间表里以 `enabled: false` 起步，负责人处于停用状态（内部取值仍是 legacy，见退役 change 5.4 的决定；对外一律说停用 / 恢复），见 design.md D5；
  - 覆盖核验的预期触发集合扩展为同时读取 `trade_schedules.yaml`；
  - 新的 `chief-daily` 周期创建前，把前一日仍待审批或待修订的周期转为失效。

  验证：新增测试覆盖休市不触发、研究不完整不建周期且零模型调用、主理人失败不影响对账、重启补跑不重复、前一日周期被取代；`enabled: false` 时不注册定时、不进入预期触发集合；停用时记录跳过原因与“因停用而跳过”的覆盖分类沿用 90d26b2 已实现的能力，只补交易路径工作流的用例
- [x] 2.8 IBKR 连接标识：同一进程内每个连接分配不同的 client_id，跨进程规则不变。验证：测试在同一进程内并发开两个连接，client_id 不同；现有 broker 测试通过
- [x] 2.9 实盘授权来源核实：授权记录增加核实状态（未核实、已核实、已作废）及核实依据；实盘门只采信已核实记录；核实或作废必须引用用户的确认原文；既有记录（含 `LIVE-X`）一律视为未核实，不删除。验证：测试覆盖未核实拒绝、作废后拒绝且原记录可读、缺确认引用的核实被拒；`ats live gate --reference LIVE-X` 返回“授权来源未核实”
- [x] 2.11 决策与交易角色的读路由（design.md D3-1）：允许 `chief`、`risk`、`trader`、`clerk` 使用整消费者读路由，为它们定义数据产品检查，切换沿用现有的批准与记录。验证：测试覆盖未切换时主理人决策图在风控节点被读窗口门禁拒绝；切换后同一路径通过；路由不改变其他边界与实盘锁；切换要求检查通过；研究侧 6 个消费者的行为不变
- [x] 2.10 更新 `docs/OPERATIONS.md` 第 7 节（草稿，随第 4 组生效）：命令行审批、两个交易路径工作流的停用与恢复、“六个边界开关不是权威状态”、回退步骤、三道实盘锁。更新 `docs/TEST_BASELINE.md` 中本组新增与改写的测试。验证：照文档能在隔离环境完成一次“查看待审批 → 批准 → 看到已批准未执行”

## 3. T0 交易路径人工核验（我准备，你签结论）

- [x] 3.1 生成 `openspec/changes/switch-trade-path-and-retire-r5/verification.md`：按 design.md D1 列出 T0.1–T0.9，每项附核验方式、通过标准与自动证据。证据文件放在 `var/` 下，不放 `tmp/`。验证：每项都有可复核的证据引用
- [x] 3.2 生产只读证据：用当天真实研究快照渲染一次主理人上下文（不建周期、不调用模型或复用已有结果），并在生产库副本（`var/` 下）上运行 `ats clerk run --dry-run`，覆盖最近 30 天，列出持仓与资金差异、无法归因记录及原因。验证：两份材料写进 verification.md，生产库未被写入（对比写前写后的行数）
- [x] 3.3 把 `LIVE-X` 的调查结论（design.md D9-1，2026-10-21 只读调查）写进 verification.md；2.9 上线后追加作废记录，引用用户 2026-10-21 的确认原文“这不是我签发的实盘授权，同意作废。”，原记录保留。验证：`ats live gate --reference LIVE-X` 返回“已作废”，原记录仍可读
- [x] 3.4 你逐项执行 T0 并填写结论、核验人与日期；未通过项登记为修复任务，修复后复核。验证：verification.md 中没有“未通过”项
- [x] 3.6 T0.1 未通过项修复（design.md D3-2，2026-10-29 用户审阅后登记）：主理人账户状态改读 TWS 实时（含持仓，读不到则阻断）；决策周期开始时在同一快照上现算风控姿态；研究快照只渲染六个分析师输出，不含信息简报原文；回合已实现盈亏在券商未给时按平均成本计算，绩效逐项写明口径与窗口。验证：新增测试覆盖实时账本渲染、不可用时阻断、口径标注、三种已实现盈亏；交易路径各组回归通过；重渲染后交你复核
- [x] 3.7 基本面按标的取模式、事件周期由触发标的发起（design.md D3-3）：计划按标的冻结基本面模式；事件周期只强制触发标的，非触发标的缺基本面时标明不阻断；上下文列出事件标的；读路由检查支持 `per_target`；单标的任务的配置血缘只计入本标的。验证：新增测试覆盖混合覆盖、事件周期部分标的有评审、触发标的缺评审阻断、非触发缺失标明不阻断、事件简报通过来源一致性校验；交易路径回归与 T0.6 通过；在生产库副本上重渲染例行与事件两种模式交你复核
- [x] 3.8 T0.2 未通过项修复（design.md D3-4，2026-10-30 用户审阅后登记）：修订、风控审查、状态转换按各角色完成时刻记时；风控驳回后由主理人模型重新决策（无主理人的运行仍机械采纳边界）；修订记录模型配置与提示词版本。验证：新增测试覆盖时间严格递增和模型修订路径；T0.2 相关测试与交易路径回归通过；在隔离环境出 5 条路径样例，在生产库副本上出 1 份真实主理人样例，交你复核
- [x] 3.9 真实主理人提案接入交易路径（design.md D3-5）：目标权重按差额换算、无可卖持仓即拒绝；规范化失败分类路由；人工复核原因写实，模型失败不再记为不行动；结构化推理与引用核对；现有期权的人工建议；快照中途失效只看冻结后的新版本；chief 输出上限调整。验证：新增测试覆盖上述各项；直接相关测试通过；在生产库副本上用真实持仓、研究与模型跑完一个两轮周期，导出完整修订与风控审查交你复核
- [x] 3.10 命令行审批的可用性（design.md D3-6）：重审的 beta 跨进程缓存一天并在取新快照前预热；审批命令输出降噪；`show` 折叠研究快照引用并带 `--full`；终端里免输 hash，看卡后输入确认词，脚本仍须写明 hash。验证：新增测试覆盖缓存命中与过期、预热、降噪与折叠计数、终端确认的取消与最新修订校验；直接相关测试通过
- [x] 3.5 记录 T0 结论（范围、提交号、已接受的缺口）并提交 verification.md。验证：提交存在

## 4. 生产切换（每步需你批准；上线后重启 `com.ats.dispatcher`，用 `launchctl list` 确认 PID）

- [ ] 4.1 S1 上线主理人改读（2.1、2.2）：同时用 2.11 的命令切换 `chief`、`risk`、`trader`、`clerk` 的读路由（每个单独批准）。验证：重启后用一次手动 `ats chief run` 演练（不批准），确认上下文来自快照，`ats retirement zero` 显示 Chief 两条 zero
- [ ] 4.2 S2 上线决策记录读取方迁移（2.3）。验证：`ats chief show` 显示审计表中的最近周期；旧表镜像写仍有新行
- [ ] 4.3 S3 上线审批与账本成对切换（2.4、2.5、2.6、2.9）。上线前后各跑一次 2.6 的核验脚本，把提交号写进 verification.md。验证：两次核验输出都为空；`ats approval pending` 可用
- [ ] 4.4 S4 启用 `clerk-daily`：时间表改为 `enabled: true` → 重启 `com.ats.dispatcher` → `ats dispatch resume clerk-daily`。验证：第一次定时运行 complete；对账结果与 3.2 的演练差异一致或可解释；同一窗口手动重跑不产生新事实
- [ ] 4.5 S5 启用 `chief-daily`：时间表改为 `enabled: true` → 重启 `com.ats.dispatcher` → `ats dispatch resume chief-daily`。验证：第一次定时运行要么建周期并在待审批处暂停、进程正常结束，要么以研究不完整终结并出缺口报告；记录实际模型请求数
- [ ] 4.6 OPERATIONS 第 7 节改为“交易路径（已切换，实盘锁定）”，ARCHITECTURE §6 实施状态同步。验证：文档描述与 `launchctl list`、`ats approval pending` 的实际输出一致

## 5. 观察窗口（design.md D8）

- [ ] 5.1 每个交易日记录观察结果，写进本 change 的 `observation.md`：周期终态、审批结果、执行前检查结果、Clerk 对账差异、模型请求数、TWS 可用性，以及七条停止条件是否触发。验证：每个交易日一行，停止条件逐条有结论
- [ ] 5.2 5 个交易日的窗口内达到最低样本：你在命令行完成至少 3 次审批（批准、拒绝各至少一次）；若窗口内有财报事件日就覆盖它，没有就记为“未覆盖”。验证：observation.md 中可查到对应周期
- [ ] 5.3 若停止条件触发：停用两个工作流，按 D8 回退并记录原因；修复后从 4.x 中对应的步骤重新开始，窗口重新计算。验证：回退记录与重启后的 PID
- [ ] 5.4 窗口结论：汇总已批准未执行的检查失败分布、对账差异、模型请求总数与可用性问题，由你签字确认。同时把退役登记中的外部门 `trade_path_cutover` 记为已满足（依赖退役 change 1.3–1.5 的登记字段；若届时未完成，先完成那几项）。验证：结论已提交；`ats retirement decide` 对 R5 条目不再显示“外部门未满足”
- [ ] 5.5 按 design.md D9 生成实盘解锁前置清单 `live-unlock-prerequisites.md`，逐项写现状（满足、未满足、待你决定），不执行其中任何一项。验证：D9 的 8 项都有现状与证据引用

## 6. R5a 读取侧条目（每条需你同意；沿用退役 change 的 V1–V6）

- [ ] 6.1 `chief.legacy_table_direct_reads`：做 V1–V3，删除旧直读入口，登记改为 retired 并附清零证明。验证：`ats retirement decide` 判可退出；命中墓碑的读取返回退役原因
- [ ] 6.2 `store.direct_trade_reads`：同 6.1。验证：同 6.1
- [ ] 6.3 `sector_reviews`、`legacy_read_models`（design.md D10 R5a；用户 2026-10-27 定）：
  1. 保留的只读命令（`ats sector show` 等读 `sector_reviews` 与旧读模型的命令）改读已发布的任务投影，在同一批历史数据上与原输出逐项对照；
  2. 清掉 Chief 与 `channel.context` 消费方；
  3. `runtime.cli`、Chief、`channel.context` 都判 zero 后退役，旧表保留只读。

  验证：对照记录写进 inventory.md；`ats retirement zero` 对三个消费方判 zero；历史行可读，新写入已停止
- [ ] 6.4 `decision_roles.unwired_to_governed_read_api`：风控、Trader、Clerk、Fundamental 四个消费方经受治理读取面取数后退役。验证：`ats retirement zero` 对四个消费方判 zero
- [ ] 6.5 `macro_reviews`、`technical_reviews`：做法同 6.3。`ats macro show`、`ats technical show` 等改读已发布的任务投影并逐项对照；Chief 消费方已由 2.1 清掉；`runtime.cli` 与 Chief 都判 zero 后退役，旧表保留只读。验证：同 6.3
- [ ] 6.6 本批复验 V4–V6，TEST_BASELINE 与退役 runbook 记录本批。验证：重启后下一次 `chief-daily` 与 `clerk-daily` 正常完成

## 7. R5b 写入与入口侧条目

- [ ] 7.1 `memory.store.legacy_decision_write_path`：确认镜像写的读取方全部清零后，移除镜像写；旧 `cycles/decisions` 表保留只读。验证：V1–V3 通过；新周期不再写旧表；旧表历史可读
- [ ] 7.2 `trades.client_order_id.legacy_derivation`：旧式订单号行由 Clerk 标记为 `legacy_unknown`（不伪造审计字段）或对账承接后，移除旧派生式。验证：新写入全部使用 `cycle + revision + 序号` 派生；旧行可读并带标记
- [ ] 7.3 `scheduler.adhoc_journal_jobs`、`performance.legacy_snapshot`：生产触发只经 `clerk-daily`，`ats journal` 的独立对账入口与 `ats trader snapshot` 降为单步调试或删除（逐个判定）。验证：V1–V3 通过；`clerk-daily` 继续正常完成
- [ ] 7.4 `journal.report.render_ledger`：月度台账改读派生读模型。验证：同一月份新旧台账输出一致或差异可解释
- [ ] 7.5 `risk.checks.pre_trade_adapter`：确认 `runtime.cli` 的组合级巡检已迁到 `review_revision` 或声明退役后，删除适配层。验证：V1–V3 通过；风控测试全绿
- [ ] 7.6 本批复验 V4–V6，记录本批。验证：同 6.6

## 8. R5c 切换期工具（交易侧，加上 2026-10-27 从退役 change 转入的原 R2）

> 顺序与依据见 design.md D10 的“R5c 的顺序”。原 R2 的做法照搬退役 change 4.2–4.8：每条先做 V1–V3，归档命令用法样例，同步冻结面与资格清单，最后复验 V4–V6。

- [ ] 8.1 让退役工具自立：把 `ats retirement` 用到的消费方扫描器（`scan_consumer_access`、`CONSUMER_ENTRY_POINTS` 及其依赖的模块清单常量）从 `intake_verification` 搬到退役工具自己的模块。验证：搬移前后对全部登记条目运行 `ats retirement zero` 与 `decide`，输出逐条一致；`ats retirement` 不再导入 `intake_verification`
- [ ] 8.2 用调用图逐个确认 `joint_cutover`、`boundary_drill`、`acceptance_runner/reports/matrix`、`paired_business`、`shadow_*`，以及原 R2 的 `batch_manifest`、`read_cutover`、`read_deployment`、`schedule_cutover`、`cutover_acceptance`、`intake`、`intake_verification`、`scoped_intake`、`analysis_verification`、`consumer_disposition`、`isolated_entry`、`dispatch_claims`，以及 10-27 核对补出的 `business_replay_inputs`、`exit_requirements`、`cutover_guards`、`shadow_ledger`（Clerk 引用）、`execution/shadow_execution`（Trader 执行、主理人决策图、store 的交易写入调用）的生产调用方、测试依赖与命令组（见 design.md D10“核对时补出的模块”）。结论写进本 change 的 `inventory.md`。验证：每个模块都有删除、迁移或保留的结论；`shadow_execution` 与 `business_replay_inputs` 的回放冻结写明长期是否需要
- [ ] 8.3 处理被验收测试使用的模块（`analysis_verification`、`isolated_entry`、`scoped_intake`、`consumer_disposition`、`cutover_acceptance`）：交易路径长期需要、但 22 项追溯与 T0 测试没有等价覆盖的检查（订单可追溯、未经批准的订单被拒、隔离模拟运行），先迁进长期测试支撑；只服务迁移期资格的部分标为可删。验证：迁移后 22 项追溯、隔离模拟与 T0 相关测试全绿；design.md D10 的长期资金安全机制一律保留
- [ ] 8.4 删除叶子工具：`boundary_drill`（`ats drill`）、`joint_cutover`、`paired_business`、`acceptance_runner/reports/matrix`、命令 `ats cutover`；删除 `shadow_*`（`ats shadow`）之前，先按 8.2 的结论拆掉 Clerk、Trader、主理人决策图、store 对 `shadow_ledger` 与 `execution/shadow_execution` 的引用（删除或迁到长期模块），并去掉 `read_windows` 对交易消费者的批次报告校验。每条做 V1–V3，命令保留一个版本的提示桩，归档用法样例到 `docs/archive/`，同步冻结面与资格清单。验证：22 项追溯、Clerk、架构守卫与 assurance_surface 测试通过；`ats live drill` 仍可用；`read_windows` 不再导入 `shadow_reports`；`src/` 内没有对 `shadow_*` 的导入
- [ ] 8.5 `migration.batch_read_cutover`：V1–V3 后删除 `batch_manifest`、`read_cutover`、`read_deployment`、命令 `ats batch`、`ats read` 及其测试（`test_batch_manifest`、`test_read_cutover`、`test_batch_cli`），同步冻结面与资格清单并归档用法样例。验证：受影响测试与 assurance_surface、架构守卫测试通过；调用 `ats batch` 返回已退役提示
- [ ] 8.6 `migration.schedule_cutover`：V1–V3 后删除 `schedule_cutover`、`cutover_acceptance`（8.3 已迁走需要保留的检查）及其测试。验证：同 8.5
- [ ] 8.7 `migration.intake`、`migration.analysis_verification`：V1–V3 后删除 `intake`、`intake_verification` 剩余部分、`scoped_intake`、`analysis_verification`、`consumer_disposition`、命令 `ats intake` 及其测试；`isolated_entry` 按 8.3 的结论只删可删部分（原 R2 整删的计划已被取代）。验证：同 8.5；`ats retirement zero/decide` 仍正常运行
- [ ] 8.8 `migration.dispatch_dedup`（必须在 8.6 之后）：先把调度覆盖核验（现在的 `runtime-expected` / `runtime-compare`，含“因停用而跳过 / 作废”的分类）搬到保留的命令下；再 V1–V3，删除 `dispatch_claims` 和 `ats dispatch` 的 `runtime-*`、`ownership`、`ledger`、`executed`、`record-execution`、`rollback-plan`、`rollback-history`。验证：同一时间窗口下覆盖核验搬移前后输出一致；`ats dispatch --help` 只剩停用、恢复、负责人日志与覆盖核验相关命令；受影响测试通过
- [ ] 8.9 登记处把本组条目（含原 R2 的 5 条）标为 retired 并附清零证明；更新 OPERATIONS 与 TEST_BASELINE，写明删除了哪些测试、其中多少原本失败（含 10-19 登记的 41 个旧失败）。验证：`ats retirement decide` 显示本组 retired，TEST_BASELINE 有本批记录
- [ ] 8.10 本批复验 V4–V6：全量测试与基线逐项对照；重启 `com.ats.dispatcher`，观察下一次定时触发、数据刷新、日历消费，以及 `chief-daily` 与 `clerk-daily`；确认旧表与台账可读。验证：复验结果写进 inventory.md 的本批记录

## 9. R5d 读路由与资格机制收窄

- [ ] 9.1 前提核对：退役 change 的 R0（登记字段与门）与 R1（`consumer-cutover` 改名为数据健康命令）已完成，本 change 的 6–8 组已完成（原 R2 已并入第 8 组，不再是外部前提）。验证：`ats retirement decide` 对本组条目只剩本组内依赖
- [ ] 9.2 `consumer_api.read_input` 收窄为“查声明 → 查数据产品 → 读”：保留消费者身份、允许的产品与 scope 声明、读取前检查与 scope 绑定；去掉路由表、资格账本、指纹绑定与边界接线证据的依赖；去掉对 `business_replay_inputs.governed_read` 的依赖（若 8.2 判定回放冻结属于隔离模拟的长期能力，就迁到长期模块）。验证：新增测试覆盖读取未声明产品被拒、产品不合格显式失败、没有路由表与资格记录时照常读取、scope 不一致被拒；6 个研究分析师和 5 个交易路径角色的读取测试全绿
- [ ] 9.3 删除 `cutover`、`cutover_routing`、`scoped_routes`、`consumer_cutover` 路由表、`consumer_reads`、`read_windows`、`read_recovery`、`invocation_bindings`、`boundary_evidence`、`cutover_wiring`、`exit_requirements`（先拆 `data/assurance.py` 读恢复核验的引用）、`cutover_guards`、`business_replay_inputs` 的剩余部分（先把 `route_switch` 用的 `implementation_hashes` 迁到长期模块，`route_switch` 本身保留），以及 `assurance_surface` 中只为资格存在的指纹清单；历史路由与资格记录保留只读。验证：V1–V3 通过；架构守卫测试通过；`ats live drill/rollback-drill` 与隔离环境的停止恢复测试仍通过
- [ ] 9.4 本批复验 V4–V6：重启后一个完整周期（工作日研究、主理人、Clerk，周六宏观与行业）无因退役导致的失败。验证：观察记录写进 observation.md

## 10. 规格与收尾

- [ ] 10.1 退役 change 归档后，用 openspec-update-change 在本 change 补一条删除：退役 change 新增的“迁移完成后研究读路由必须固定为新路径……”，因为它已被“消费者必须按声明的数据产品与业务 scope 读取”取代。同时核对 SEC 原文非阻塞处理已并入数据产品检查。验证：`openspec validate switch-trade-path-and-retire-r5 --strict` 通过
- [ ] 10.2 复核 `data/target-dataflow-assurance` 与 `workflow/cutover-control` 中其余只服务于资格或接入核验的要求（如“十个角色的实际接入必须以运行记录核验”“隔离接入验收不得以生产资格为前置”），需要删改的用 openspec-update-change 补增量。验证：每条都有保留或删改的结论
- [ ] 10.3 文档：ARCHITECTURE §6、§10、§11，OPERATIONS 第 7 节，退役 runbook，README，同步到 R5 之后的状态。验证：文档中的命令都能照写运行
- [ ] 10.4 全量测试一次，与基线逐项对照，并登记删除的测试与任何新失败的归因。验证：TEST_BASELINE 有本 change 的权威测量
- [ ] 10.5 在退役 change 归档之后，`openspec validate --all --strict` 通过。验证：命令输出通过
