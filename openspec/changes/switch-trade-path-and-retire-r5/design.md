# Design

## Context

动机见 proposal.md。下面只列决定做法的现状与约束（2026-10-21 核对）。

**新交易实现已经在代码里，但生产没在用：**
- 决策图（`graph/chief.py`）已经按新契约运行：固定研究快照、`create_cycle`、不可变修订、`review_revision`、审批绑定、十字段授权。隔离环境的 22 项订单追溯核验（`test_phase_f_integration_evidence`）在当前代码上通过。
- 仍然连着旧实现的有四处：
  - 主理人上下文渲染直读旧表（`agents/chief/assemble.py`，登记 `chief.legacy_table_direct_reads`）；
  - 交易历史与绩效直读（`store.direct_trade_reads`）；
  - 决策修订被镜像写回旧 `cycles/decisions` 表，`chief show` 与上下文读的是旧表（`memory.store.legacy_decision_write_path`）；
  - Clerk 没有任何排程（`scheduler.adhoc_journal_jobs`）。

**三道实盘锁都在：**
- 生产券商提交禁用：`trader/execute.py` 的 `AUTO_EXECUTION_ENABLED = False`，即 C3；
- 实盘路由 `live_trader` 为 `disabled`；
- `ats live gate` 没有有效授权引用。

授权库里有一行来源未核实的 `LIVE-X`，gate 对它会返回 authorised。它之所以没造成实际后果，只是因为另外两道锁还在。

**六条切换边界从未接线。** `ats cutover state` 显示 `projection_read` 等五条为 `legacy`、`live_trader` 为 `disabled`，全部 `wired: false / enforced: false`。研究侧切换实际走的是消费者读路由（`consumer_cutover`）和调度负责人（`ownership`），没用这六个开关。

**审批方式现状：**
- 飞书审批卡用签名链接，经 cloudflared 隧道回到 `com.ats.serve`，回传经常失败；
- 现有命令行通道 `CLIChannel` 是前台交互式阻塞，适合手动运行，不适合定时任务，还提供“批准部分标的”，与“Boss 只能批准或拒绝”冲突；
- 恢复执行的入口 `resume_cycle(thread_id, approval)` 与通道无关，可以复用。

**演练模式下被批准的周期会被记为 `executed`**（`graph/chief.py` 的终态判定）。这样观察期的统计会把“演练批准”误当成交。

**IBKR 连接标识按进程区分**（`broker/ibkr.py`：base + pid 偏移）。Dispatcher 是同一个常驻进程：Technical 评审（10:00 ET）已经在里面读 IBKR，再加上主理人和 Clerk 后，同进程内的并发连接会撞到同一个 client_id。

**约束：**
- 不重复发模型请求；费用上限在 API key 一侧，不加应用内护栏；
- 常驻进程改代码后必须重启；
- 台账只增不改；
- 历史数据不删；
- 用户本人加的护栏不拆，只能找不冲突的设计。

## Goals / Non-Goals

**Goals：**
- 一个可执行的交易路径人工核验门 T0：核验内容、环境、通过标准。
- 一条在实盘锁定下可回退的生产切换路径：主理人改读新输入、决策与审批只认审计表、Clerk 与主理人每日由 Dispatcher 排程、审批在命令行完成。
- 一个有停止条件的观察窗口，以及窗口后按依赖顺序执行的 R5 退役计划。
- 把实盘解锁的前置条件单列出来，供你以后决策。

**Non-Goals：**
- 不解锁实盘，不开放 C3，不切 `live_trader`，不签发也不核实任何实盘授权。
- 不使用 IBKR Paper 下单。Paper 写入需要你另行授权，见 D2。
- 不做任何 IM 审批通道。飞书卡片与 `com.ats.serve` 不改也不退役，等你选定 IM 工具后另立 change。
- 不改研究侧 7 个工作流的调度与读路径。
- R5 不触及长期资金安全机制：路由仲裁与代次、实盘授权、券商写禁令、授权生命周期、未终结订单处置、隔离模拟券商（见 D10）。

## Decisions

### D1. 交易路径人工核验门 T0

**形式：** 和研究侧 G0 相同。清单与结论写在 `openspec/changes/switch-trade-path-and-retire-r5/verification.md`。每项写核验对象、方式（命令或查看位置）、通过标准、自动证据引用、核验人、日期、结论。自动证据由我准备，结论由你填。任一项未通过，都不做生产切换。

**环境：** 链路核验在隔离环境，用无网络的模拟券商（FakeBroker），只写隔离账本。只读核验在生产上做（真实研究快照、真实账户读取），不写决策、不下单。

| # | 核验对象 | 环境 | 方式 | 通过标准 |
|---|---|---|---|---|
| T0.1 | 主理人决策输入 | 生产只读 | 用当天真实研究快照渲染一次主理人上下文，与六类投影逐段对照；`ats retirement zero` 看 `chief.legacy_table_direct_reads` | 每段都能指到投影 ID 与 hash；无旧表读取（zero）；人为去掉一类投影时阻断并出缺口报告，不建周期。你读一份渲染结果，确认内容是你认可的研究 |
| T0.2 | 主理人—风控多轮 | 隔离 | 状态机测试：首轮通过 / 驳回一次后通过 / 多次驳回后通过 / 超过 3 轮转人工复核 / 不行动 | 全绿；驳回是结构化的；裁剪建议没有静默改单；你读一份驳回—修订样例 |
| T0.3 | 命令行审批 | 隔离 | `ats approval pending/show/approve/reject` 全流程，以及重复批准、旧修订 hash、已终结周期、前一日待审批周期 | 显示修订 hash、风控结论、组合快照时间、交易摘要和“批准后不会下单”；错 hash、旧周期、已终结一律拒绝；重复批准返回首次结果；你亲手跑一遍 |
| T0.4 | 执行前检查与锁 | 隔离 + 生产只读 | 隔离：授权十字段、60 秒新鲜度、报价 1% 规则、client_order_id 稳定、重试不重复。生产：锁定下批准一次演练周期 | 隔离下模拟成交正确；生产下到券商交接点被拒绝，进入“已批准未执行”，并记录原因；券商侧零提交（TWS 订单列表对照） |
| T0.5 | Clerk 对账 | 隔离 + 生产只读 | 隔离：部分成交、迟到成交、撤单、拒单、人工订单、无法归因、重启、重放。生产：在生产库副本上 `ats clerk run --dry-run`，覆盖最近 30 天 | 隔离全绿；重放不产生新事实。生产演练的持仓与资金与 TWS 一致，差异逐条可解释；无法归因的数量与原因由你确认可接受（7 月实盘留下的旧式订单号预计落在这里） |
| T0.6 | 订单追溯 | 隔离 | 复跑 22 项订单追溯 | 全部通过；每笔模拟订单联接唯一的快照、修订、风控与批准 |
| T0.7 | 单活与回退 | 隔离 | 双活拒绝、旧代授权拒绝；按 D8 回退步骤演练一次（停用工作流 → revert 读取方提交 → 重启） | 校验对否定路径判失败；回退后旧读取方能读到观察期内的决策（依赖镜像写，见 D3） |
| T0.8 | 测试基线 | 固定依赖 | 重写 4 个决策路径夹具（`phase_d_e2e` 3、`test_chief` 1），跑全量 | 交易路径相关测试全绿；其余与 TEST_BASELINE 逐项对照，无新增未归因失败 |
| T0.9 | 运维说明 | — | 按 OPERATIONS 第 7 节的新版本操作一遍 | 照文档能完成查看待审批、审批、停用与回退 |

对照 ARCHITECTURE §15.5 的上线门禁：
- 第 1、2 条由 T0.2、T0.5、T0.8 覆盖；
- 第 3、4 条由 T0.4、T0.6 和观察窗口覆盖；
- 第 5 条由 T0.7 覆盖。

**替代方案：** 只看自动测试全绿。没采用：自动检查只能证明“在跑”，证明不了“主理人读到的研究是对的”“对账结果与你的真实账户一致”。

### D2. 核验环境：隔离 + 生产只读，不用 IBKR Paper

- 隔离环境已有完整的模拟券商和 22 项核验，能覆盖成交、部分成交、迟到成交等券商行为；生产只读能覆盖真实输入和真实账户。两者合起来，覆盖了 Paper 能提供的绝大部分证据。
- Paper 唯一额外能证明的，是“真实 TWS 会话下的下单回报格式与时序”。这一点只在解锁实盘时才是必需的，所以把它放进实盘前置（D9）。规格要求 Paper 网络写入另行明确授权，本 change 不申请。
- **替代方案：** T0 就做 Paper 下单。没采用，原因是它需要一份新的写入授权，而切换本身并不依赖它。

### D3. 切换步骤与顺序

每一步单独提交、单独上线，需要你批准；上线后重启 `com.ats.dispatcher`，用 `launchctl list` 确认 PID。

| 步 | 内容 | 回退 |
|---|---|---|
| S1 | 主理人上下文只从研究快照渲染；交易历史与绩效改经 Internal State（含完整性标记，不完整时在上下文里显式标出） | revert 该提交 |
| S2 | 决策记录的读取方改读审计表：`chief show`、上下文里的最近决策、`last_chief_run`、`recent_decisions`。**镜像写保留** | revert；旧表因镜像写仍然完整 |
| S3 | 审批与账本成对切换（见 D4）：命令行审批上线；Clerk 的串联与读模型发布只认审计表 | revert |
| S4 | 启用 Clerk 每日对账（工作流从停用改为启用） | 停用该工作流 |
| S5 | 启用主理人每日决策 | 停用该工作流 |

- **顺序理由：** 先改“读什么”（S1、S2），再改“谁来批、谁来记”（S3），最后才让它每天自动跑（S4、S5）。
- **Clerk 先于主理人启用：** 主理人第一天读到的 Internal State 应当已经过一次对账。
- **镜像写保留到观察窗口结束：** 回退时旧读取方仍能看到观察期内的完整决策。它在 R5 中随 `memory.store.legacy_decision_write_path` 一并移除。

### D4. 审批与账本成对切换：用一次代码发布，不补接线边界开关

规格要求审批写入与账本发布成对切换，不能暴露“审批写新表、账本还按旧表串联”这样的半迁移状态。

- **推荐做法（方案 B）：** 在同一个提交里完成：
  - 命令行审批写入审计表；
  - Clerk 的决策链联接只读审计表；
  - 去掉 Clerk 及报表中对旧决策表的读取。

  上线前后各跑一次只读核验脚本，确认没有任何审批读取方或账本发布方仍读旧决策表。回退就是 revert 这一提交。由于审计表一直在写、镜像写也一直在写，回退不需要恢复数据。
- **没有推荐的方案 A：** 先给 `approval_lifecycle` 和 `clerk_publication` 两个边界在实际写入点补接线，再用 `ats cutover` 联合批次切换。代价是为一次性的切换补一套 R5 马上就要删的控制面，而且这两个边界的“新旧两侧”在当前代码里已经不是两套写入，只是读取方不同。
- **方案 B 的代价：** 切换后六个边界开关仍显示 `legacy`，与事实不符，直到 R5 删除它们。缓解办法是在 OPERATIONS 第 7 节写明“边界开关不是权威状态”，并在 verification.md 记录切换的提交号。
- **已定：** 用户于 2026-10-21 选定方案 B。

### D5. Dispatcher 排程两个交易路径工作流

- **时间表：** 新建 `config/workflow/trade_schedules.yaml`。研究时间表文件不改，研究与交易的停用互不影响。

  | 工作流 | 时点 | 范围 | 说明 |
  |---|---|---|---|
  | `clerk-daily` | 交易日 16:30 ET（收盘后） | 全账户 | 窗口从上次成功对账的次日到当天；漏跑的日子由 Clerk 已有的漏跑窗口登记补上，同一窗口幂等 |
  | `chief-daily` | 交易日 10:30 ET | `ai_hardware` 决策 profile，Fundamental 例行模式（财报事件日改事件模式，规则沿用研究快照的固定需求判定） | 沿用旧调度器的时点：开盘后一小时，北京时间晚上，便于你当晚审批 |

- **交易日日历：** 沿用 Dispatcher 现有的交易所日历（含假期与提前收盘）。
- **负责人默认 `paused`：** S4、S5 才改为启用。
- **与研究的衔接：** 主理人运行前经 Chief 完整性门禁。研究不齐时以不完整终结，不建周期，也不调用模型。研究任务失败不会因主理人排程而重跑；主理人不触发分析师重跑。
- **前一日待审批的周期：** 新的 `chief-daily` 周期创建前，把仍在待审批或已驳回待修订的旧周期转为“失效”，原因写“被次日周期取代”。这样不会同时挂着两份待批方案。
- **IBKR 连接：** 同进程内每个连接分配不同的 client_id（pid 偏移加进程内序号），不改变“跨进程不冲突”的现有规则。
- **替代方案：** 两个工作流写进 `research_schedules.yaml`。没采用，因为文件名与停用语义都是研究侧的，混在一起不利于单独停用交易路径。

### D6. 命令行审批

- **命令组 `ats approval`：**
  - `pending`：列出待审批过程（周期、修订号、hash 前 12 位、风控结论、组合快照时间、等待时长）。
  - `show <cycle>`：完整审批对象。包括交易摘要、风控审查摘要（违规项与边界）、研究快照引用、“批准后不会下单”及锁定原因。
  - `approve <cycle> --revision <hash 前缀，至少 12 位>`：hash 必须等于当前被风控批准的修订，否则拒绝并显示当前 hash。
  - `reject <cycle> --note "…"`：备注作为修改意见保存，由主理人生成新修订。
- **审批人与幂等：** 审批人记为本机用户名，可用 `--actor` 覆盖。幂等键沿用现有审批幂等键。
- **恢复执行：** 复用 `resume_cycle`，在执行 `approve` 的这个命令行进程里继续跑到 Trader。这个进程有自己的 pid，因此有自己的 IBKR client_id。
- **批准时刷新快照：** 规则要求执行时账户与行情快照不超过 60 秒（`config/risk.yaml` 的 `max_snapshot_age_seconds`），超过就作废授权、重新风控、重新审批。命令行审批天然比 60 秒慢，不处理的话每次批准都会被打回，3 轮后转人工复核。所以 `approve` 按这个顺序做：
  1. 重取账户与行情快照；
  2. 用确定性风控对**同一修订**重审，不调用模型，新审查单独留记录；
  3. 显示与上次审查的差异；
  4. 你确认后立即执行，整个过程在 60 秒内完成。

  重审不通过就拒绝这次批准：周期退回驳回状态，主理人按既有多轮规则修订，修订完再等你审批。这样不放宽 60 秒阈值，也符合“新快照重新风控后必须重新人工批准”。
- **只有整体批准或拒绝：** 不提供部分批准。`ats chief run` 的前台交互也收敛为批准或拒绝，去掉“部分批准”；批准后同样进入上述恢复路径。
- **提醒（2026-10-21 你确认要提醒）：** 周期进入待审批时，发一条单向文字提醒：周期号、一行交易摘要、`ats approval show <cycle>` 命令，不带任何链接或回调。走数据健康告警已在用的通道，以后换 IM 只改这一处。周期被次日周期取代时，再发一条“已过期未审批”。忘记审批只会让次日周期取代它，实盘锁定期间没有资金影响。
- **飞书：** 卡片与回调代码保留不动，新路径不调用。`com.ats.serve` 在交易路径上不再是必需服务，是否卸载由你决定，不影响本 change。

### D7. 实盘锁定下的执行语义

- **`chief-daily` 以非演练模式运行，三道锁不动。** 批准后 Trader 完成授权十字段、新鲜度、实时报价（30 秒内、价差不超过 1%）与资金上限检查，在券商交接点拒绝，周期进入新的终态“已批准未执行”，并记录每项检查结果。
- **现状更正：** 当前代码在生产上走不到报价检查。授权绑定到活跃交易路由那一步，就因为“没有活跃交易路由”被硬拒绝。tasks 2.4 要把顺序调整为：先做只读检查（授权字段、新鲜度、报价、资金上限），最后在券商交接点因实盘锁定而拒绝。路由与锁的检查仍然排在任何券商接触之前，不放宽任何一道锁。
- **为什么不用演练模式：** 演练模式下 Trader 节点直接跳过，观察期就看不到执行前检查在真实行情下的表现。而这正是以后判断能否解锁的关键证据。
- **演练模式的终态修正：** 演练模式下被批准的周期也改记“已批准未执行（演练）”，不再记“已执行”。
- **复盘口径：** Internal State 与复盘把“已批准未执行”单列，不计入成交与绩效。
- **你在 TWS 的手动交易：** 由 Clerk 记为人工订单，不与审批自动关联。

**现有执行模式一览**（`ats chief run` 与运行环境）：

| 模式 | 走到哪一步 | 碰不碰券商 | 适合场景 |
|---|---|---|---|
| 只决策（`--no-execute`） | 主理人 + 风控，不进审批、不进 Trader | 不碰 | 调试主理人提示词或风控规则，不打扰你 |
| 演练（默认，不加 `--live`） | 主理人 + 风控 + 审批，Trader 整体跳过 | 不碰 | 手动试跑、看决策内容；现在被误记为“已执行”，由 2.4 修正 |
| 非演练 + 实盘锁定（本 change 推荐用于每日运行） | 主理人 + 风控 + 审批 + 执行前全部检查，在交接点拒绝 | 只读（账户、报价），不提交 | 观察窗口：积累“如果解锁会怎样”的证据 |
| 影子（`ATS_RUN_MODE=shadow`） | 同上，另写影子意图账本，用于与旧路径对比 | 禁写 | 迁移期新旧对比工具，R5c 退役，不再推荐 |
| 隔离模拟（FakeBroker） | 全链路，含模拟成交与 Clerk 对账 | 不碰真实券商，只在隔离目录 | T0 核验与回归测试 |
| IBKR Paper | 全链路，在 TWS 模拟账户真实下单 | 写模拟账户，需你单独授权 | 解锁实盘前的一次演练（D9-3），目前没有现成入口 |
| 实盘 | 全链路真实下单 | 写真实账户 | 锁定，需要 D9 全部前置与你的授权 |

另有三个开关，与模式正交：
- `--yes` 自动批准：只用于测试，实盘与定时任务都不得使用；
- `--no-llm`：不调用模型；
- `--offline`：不读券商。

### D8. 观察窗口、停止条件与回退

- **窗口（2026-10-21 你确认）：** 从 S5 启用当天起算 **5 个交易日**。期间至少要有 3 次你实际在命令行完成的审批（批准、拒绝各至少一次）。若窗口内恰有财报事件日，就覆盖它；没有就记为“未覆盖”，不延长窗口。
- **停止条件**（任一触发即停用两个交易路径工作流，并记录原因）：
  1. 出现任何真实券商提交（TWS 订单列表或券商回报中有本系统的 orderRef）。
  2. 主理人上下文出现旧表读取（运行日志或 `ats retirement zero` 显示 not_zero）。
  3. 审批绑定错误：批准的 hash 与风控审查的修订不一致，或旧修订、旧周期的批准被接受。
  4. 决策链断裂：任一周期无法联接快照 → 修订 → 风控 → 审批。
  5. Clerk 对账与 TWS 持仓或资金不一致且无法解释，或同一窗口重放产生了新事实。
  6. 同一触发重复执行，或同一输入重复发出模型请求。
  7. 主理人连续 2 个交易日因非数据缺口原因失败。数据缺口导致的不完整不算，只记录。
- **不触发停止、但要记录：** TWS 不可用导致的阻断，以及你未审批导致的过期。
- **回退：**
  1. 停用工作流：`ats dispatch pause`（退役 change R1 之后的命令；之前用 `owner-rollback`）。
  2. 必要时按 S3 → S2 → S1 倒序 revert 并重启服务。
  3. 实盘锁定期间没有在途订单需要排空。但仍执行一次路由冻结核验，确认没有未终结授权。

### D9. 实盘解锁的前置（单列，本 change 不执行）

解锁需要你另行明确授权，并在单独的 change 里执行。授权前必须逐项满足：

1. **`LIVE-X` 作废**（2026-10-21 只读调查，结论：很可能是开发时的冒烟测试误写进了生产库）。证据：
   - 字段全是占位值：授权人 `o`、签发人 `i`、账户 `U1`（IBKR 真实账户是 U 加 7 位数字）、有效期到 2099 年、备注与记录人为空；
   - 写入时间是 2026-10-07 11:24（北京时间），比 `live_authorisation.py` 首次提交（同日 19:29，02c81f2）早 8 小时，正处在这个模块的开发过程中；
   - 写入函数的缺省路径就是生产库 `var/phase_f_live_authorizations.sqlite`，同一时刻没有其他库被写；
   - 10-07 当天有记录的 Codex 会话从 13:18 UTC 才开始，只发现过它、没有写过它；当天没有 Claude 会话。

   用户已于 2026-10-21 确认：“这不是我签发的实盘授权，同意作废。”tasks 2.9 实现来源核实后，按这句话追加作废记录，原记录保留。在此之前，实盘门因 2.9 一律拒绝它。另外，账户 `U1` 对不上真实会话账户，实盘门的账户核验本来也会拒绝它。
2. 本 change 的 T0 通过，观察窗口无停止条件触发，你签字确认观察结论。
3. **IBKR 账户与会话核验：** 实盘门核对 broker 实际会话账户与授权账户一致，不能只凭端口或配置。如果你要求，先做一次 Paper 下单演练（需单独授权 Paper 写入）。
4. **TWS 运行保障：** 自动重启时点与周末重新认证的安排，确保主理人运行与审批后的执行不落在 TWS 不可用的时段。
5. **审批通道定稿：** 命令行或你选定的 IM。审批后执行在 60 秒新鲜度内完成；超时就重新风控、重新审批。
6. **风控配置复核：** 单笔与总资金上限、单标的权重上限，以及止损只是声明、不会在券商挂单这一点，由你逐项确认。
7. **路由切换演练：** 按 live-route-switch 的冻结、核验、换代、开放协议，在隔离环境演练切换与恢复各一次。
8. **授权本身：** `ats live authorize` 写明范围、账户、环境、有效期与签发人。C3 的开放是同一授权下的单独一步，并另行记录。

### D10. R5 接续：顺序与读路由收窄

- **执行条件：** 观察窗口通过且你书面确认之后，外部门 `trade_path_cutover` 才算满足（登记在退役 change 的门字段里）。
- **每条的做法：** 沿用退役 change 的 V1–V6 验证；每条单独提交；退役前须你明确同意。

| 批 | 条目 | 前提 |
|---|---|---|
| R5a 读取侧 | `chief.legacy_table_direct_reads`、`store.direct_trade_reads`、`sector_reviews`（Chief 消费方）、`legacy_read_models`（`channel.context` 消费方）、`decision_roles.unwired_to_governed_read_api`；`macro_reviews` 与 `technical_reviews` 若最后的读取方是 Chief，一并处理 | S1–S3 已上线并过观察窗口；`runtime.cli` 消费方由退役 change R1 清掉 |
| R5b 写入与入口侧 | `memory.store.legacy_decision_write_path`（移除镜像写）、`trades.client_order_id.legacy_derivation`（旧式订单号行由 Clerk 标为 `legacy_unknown` 或对账承接后）、`scheduler.adhoc_journal_jobs`、`performance.legacy_snapshot`、`journal.report.render_ledger`、`risk.checks.pre_trade_adapter` | R5a 完成。镜像写的读取方已全部清零 |
| R5c 切换期工具（交易侧，加上转入的原 R2） | 分四步，见下文“R5c 的顺序” | R5b 完成 |
| R5d 读路由与资格机制收窄 | `cutover`、`cutover_routing`、`scoped_routes`、`consumer_cutover` 路由表、`consumer_reads`、`read_windows`、`read_recovery`、`invocation_bindings`、`boundary_evidence`、`cutover_wiring`、`assurance_surface` 与指纹清单 | R5a–R5c 完成；退役 change 的 R0（登记字段与门）与 R1（`consumer-cutover` 改名为数据健康命令）完成。不再依赖退役 change 的 R2：它已并入 R5c |

**2026-10-27 从退役 change 转入的原 R2**（依据：退役 change tasks.md 第 4 组开头的“2026-10-27 复核”段，提交 15190e2）

- **原因：** 退役 change 起初只按 `src/` 判断使用方。补查测试和命令组后发现，这些模块没有一个能单独删：
  - `boundary_drill`（`ats drill`）依赖 `batch_manifest`、`read_cutover`、`schedule_cutover`；
  - `paired_business` 和 `ats retirement` 依赖 `intake_verification`；
  - `schedule_cutover` 直接使用 `dispatch_claims`；
  - `analysis_verification`、`isolated_entry`、`scoped_intake`、`consumer_disposition` 被 Phase F 验收测试使用；
  - `cutover_acceptance` 负责验证交易订单可追溯、未经批准的订单被拒。

  这些模块和交易路径绑在一起，所以随交易路径留到 R5，门改为 `trade_path_cutover`。
- **退役 change 已做的解耦（4.1）：**
  - `digest` 搬出 `shadow_replay`（6ebbf11）；
  - 研究侧读窗口不再要求批次报告（e2319c5）；
  - 调度边界的接线登记改指向真实认领（8137c0e）。

  研究侧的 Dispatcher 与读窗口已不依赖这些工具。
- **转入的登记条目：** `migration.batch_read_cutover`、`migration.schedule_cutover`、`migration.intake`、`migration.analysis_verification`、`migration.dispatch_dedup`。
- **转入的模块：** `workflow/batch_manifest`、`read_cutover`、`read_deployment`、`schedule_cutover`、`cutover_acceptance`、`intake`、`intake_verification`、`scoped_intake`、`analysis_verification`、`consumer_disposition`、`dispatch_claims`。
- **转入的命令：** `ats batch`、`ats read`、`ats intake`，以及 `ats dispatch` 的 `runtime-*`、`ownership`、`ledger`、`executed`、`record-execution`、`rollback-plan`、`rollback-history`。
- **转入的测试：** `test_batch_manifest`、`test_read_cutover`、`test_batch_cli`、`test_intake_cli`、`test_consumer_disposition`、`test_cutover_acceptance`、`test_phase_f_scoped_intake`，以及引用这些模块的 Phase F 验收测试（`test_phase_f_stage_one`、`test_phase_f_shadow_business`、`test_phase_f_opinion_execution`、`test_phase_f_disposition_a_regression`、`test_phase_f_safe_reads` 等）。最终清单以 R5c 第 2 步的调用图为准。10-19 登记的 41 个旧失败测试（38 个属批次与读切流，3 个属 `ats cutover`）随之处理。
- **做法：** 照搬退役 change 4.2–4.8 的原文。每条先做 V1–V3，归档命令用法样例，同步冻结面与资格清单，最后复验 V4–V6。
- **口径以本 change 为准：** 原 R2（退役 change 4.5）计划把 `isolated_entry` 整个删掉。本 change 的口径是逐个判定、部分保留：被隔离模拟与 22 项订单追溯使用的部分留下。原计划已被取代。

**R5c 的顺序**（按依赖由外向内；每步每条都需你同意，并单独提交）：

1. **先让退役工具自立。** `ats retirement zero/decide` 用 `intake_verification` 里的消费方扫描器（`scan_consumer_access` 及它依赖的入口清单 `CONSUMER_ENTRY_POINTS`、模块清单常量）判断有没有人还在用旧路径。退役工具要一直用到 R5 最后一条，以后的退役也要用，所以不能在 R5 中途随 `intake_verification` 一起删掉。处理办法：先把扫描器和这些常量搬到退役工具自己的模块（`retirement_clearance` 或一个独立的消费方扫描模块），验证全部条目的 `ats retirement zero/decide` 输出与搬移前逐条一致；`intake_verification` 剩下的隔离接入核验部分，留到第 4 步再删。
2. **判定被验收测试使用的模块：** `analysis_verification`、`isolated_entry`、`scoped_intake`、`consumer_disposition`、`cutover_acceptance`。判据如下：
   - 交易路径长期需要的检查（订单可追溯、未经批准的订单被拒、隔离模拟运行），如果在 22 项追溯与 T0 测试里没有等价覆盖，先把检查迁进长期测试支撑，再删除；
   - 只服务迁移期切流资格的部分，直接删除。
3. **删除叶子工具：**
   - `boundary_drill`（`ats drill`）、`joint_cutover`、`paired_business`、`acceptance_runner/reports/matrix`，命令 `ats cutover`；
   - `shadow_*`（`ats shadow`）。删之前，先去掉 `read_windows` 对交易消费者的批次报告校验：它是 `read_windows` 对 `shadow_reports` 仅剩的导入，切换后交易消费者的读取不再需要签名批次报告，做法与退役 change 4.1 对研究侧的处理相同。
4. **删除被叶子依赖的工具：**
   - `batch_manifest`、`read_cutover`、`read_deployment`（`ats batch`、`ats read`）；
   - `schedule_cutover`；
   - `intake` 与 `intake_verification` 的剩余部分（`ats intake`）；
   - 第 2 步判定可删的 `consumer_disposition` 等；
   - 最后删 `dispatch_claims` 和 `ats dispatch` 的迁移期子命令，必须排在 `schedule_cutover` 之后。

   `read_windows` 对 `batch_manifest`、`read_cutover` 只剩文档字符串里的引用，不构成依赖。

**读路由收窄成“按 scope 读数据产品”**（规格见本 change 的 target-dataflow-assurance 增量）：

- **保留：**
  - 每个消费者的身份，以及它被允许读取的数据产品与业务 scope 声明（例如 Trader 只能读执行报价）；
  - 读取前的数据产品检查；
  - scope 与当次入口绑定一致；
  - 读取失败显式报错。
- **删除：**
  - 路由表（新旧两侧的路由行与精确 scope 路由）；
  - 逐消费者读取资格账本与签发；
  - 代码与配置指纹绑定的资格证据；
  - 边界接线证据与入口证据包；
  - `assurance_surface` 里只为资格存在的指纹清单。冻结面中仍服务于架构守卫的部分保留。
- **结果：** `consumer_api.read_input(consumer, product, scope)` 只做三件事：查声明、查数据产品、读。

**明确保留、不属于 R5 的长期资金安全机制：** `route_registry`、`route_arbitration`、`route_switch`、`route_drill`（`ats live drill/rollback-drill`，实盘解锁时还要用）、`live_authorisation`、`broker_write_guard`、`authorization_lifecycle`、`order_disposition`、`simulation` 与 FakeBroker。

**规格增量的归档顺序：** 本 change 的 cutover-control 与 target-dataflow-assurance 增量只删改退役 change 没碰过的要求。退役 change 新增的“迁移完成后研究读路由必须固定为新路径……”在 R5d 之后变得多余；等退役 change 归档、这条进入主规格后，用 openspec-update-change 在本 change 里补一条删除（tasks.md 10.1）。

### D11. 已知问题对切换的影响

| 问题 | 影响 | 处置 |
|---|---|---|
| Clash / cloudflared 让审批链接回传不稳 | 改用命令行审批后，交易路径不再依赖隧道 | 无需处理。飞书与隧道问题留给以后选 IM 的 change |
| TWS 每日自动登出 / 重启、周末重新认证 | 主理人读不到账户快照，Clerk 读不到成交 | 两个工作流的时点避开你在 TWS 设置的自动重启时间（T0 核对）。账户快照不可得时主理人显式阻断，不用陈旧数据。Clerk 失败的日子由下次运行的漏跑窗口补上。实盘解锁前另做保障（D9-4） |
| IBKR client_id 冲突 | Dispatcher 同进程内 Technical、主理人、Clerk 的连接可能撞号，导致断连、误记错误 | D5：同进程内按序号区分。T0 期间用 `lsof -nP -iTCP:7496 -sTCP:ESTABLISHED` 观察并发连接 |
| 常驻进程懒加载 | Dispatcher 首次运行主理人时才加载决策图，改了代码不重启会新旧混跑 | 每个切换步骤上线后都重启，并用 `launchctl list` 确认 PID |
| 模型费用 | 每个交易日新增主理人与风控解释的请求，次数随修订轮次变化（最多 3 轮） | T0 与观察期记录实测请求数。研究不完整时不调用模型。重启补跑不重复触发（D5 幂等） |
| 决策路径测试夹具债 | 4 个夹具未按冻结血缘重写，交易路径变更的回归保护不足 | T0.8 先重写，再动切换代码 |
| SEC 不可达、ibkr_news 缺陷 | 影响研究输入的完整性，从而影响主理人是否被阻断 | 不在本 change 处理。观察期按“数据缺口”记录，不算停止条件 |

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 命令行审批比 60 秒的快照时效慢，导致每次批准都被打回 | D6：批准时刷新快照，用确定性风控重审同一修订后立即执行；阈值不放宽 |
| 你每天要审批一次，但审批了也不会下单，容易流于形式 | 观察期要求批准与拒绝各至少一次；`show` 必须展示风控边界与研究引用，让审批有内容可看 |
| “已批准未执行”的执行前检查在真实行情下大量失败（报价过期、价差过大） | 这正是观察要拿到的证据，不算停止条件；窗口结论里单独统计，作为解锁前置 D9-5 的输入 |
| Clerk 首次对账暴露大量历史无法归因记录 | T0.5 先在副本上演练，由你确认可接受的范围；旧式订单号行按 `legacy_unknown` 处理，不伪造审计字段 |
| 方案 B 让六个边界开关在 R5 前显示过时状态 | OPERATIONS 写明不是权威状态，verification.md 记录切换提交号 |
| R5 删多了，误删实盘解锁仍要用的机制 | D10 列出明确保留清单；R5c、R5d 每个模块先做调用图核对（V1） |
| 两个 change 的规格增量冲突 | 本 change 只碰退役 change 没碰的要求，并规定归档顺序（D10） |
| 定时主理人运行时，你正在主工作区改代码 | 沿用“改代码后重启”的规则；切换期间开发在 worktree 中进行 |

## Migration Plan

1. 实现改动（tasks 第 1–2 组），全部在隔离环境验证，生产不启用。
2. 准备 T0 清单与证据（第 3 组），你执行 T0。
3. 按 S1 → S5 逐步上线（第 4 组），每步需你批准。
4. 观察窗口（第 5 组），然后由你写结论。
5. R5a → R5d（第 6–9 组），每条需你同意。
6. 收尾与规格同步（第 10 组）。退役 change 归档之后，本 change 才归档。
7. 回退：见 D8。数据不受影响。

## 用户裁决（2026-10-21，已全部确定）

1. **成对切换方式（D4）：** 方案 B，一次代码发布完成。
2. **观察窗口长度（D8）：** 5 个交易日。
3. **审批提醒（D6）：** 要提醒，发单向文字提醒，不带链接。
4. **执行方式（D7）：** 非演练 + 实盘锁定。
5. **`LIVE-X`（D9-1）：** 用户确认原文：“这不是我签发的实盘授权，同意作废。”作废记录在 tasks 2.9 上线后按 3.3 追加，引用这句原文；原记录保留。

## Open Questions

- `clerk-daily` 的具体时点是否要避开你在 TWS 设置的自动重启时间。T0 核对后定，不影响任务拆分。
- R5c 中 `isolated_entry`、`cutover_acceptance` 等哪些部分被隔离测试与 22 项追溯依赖，在 R5c 第 2 步用调用图逐个确认（D10）。
