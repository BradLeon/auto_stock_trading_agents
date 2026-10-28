# 任务 12.8：观察窗口结束后的退役逐项核验（2026-10-18）

> 结论先说：**27 个登记条目里，3 个可以退出（本来就是 retired），24 个继续 pending；本次没有修改任何登记，也没有退役任何新条目。** 这是预期结果，不是缺陷：新路径已经在生产上跑起来，但旧的生产读写在 CLI、调度、Chief 里仍然存在，所以不能退役。窗口提前在今天结束，是你 10-18 的裁决（取代 12.3 文档里建议的 10-28）；窗口里的事件路径只验证了 TSM、ASML 两家，其余标的财报没有等到。

## 一、做了什么

| 动作 | 命令 / 方法 | 结果 |
|---|---|---|
| 逐消费方清零 | `uv run ats retirement zero` | 53 个消费方每个都有结论（全文 `docs/archive/phase_f_first_run_review_20261016/retirement_zero_20261018.txt`） |
| 逐条目裁决 | `uv run ats retirement decide --drill-reference "owner-log:35-rows;route-drills:7-consumers;window:phase-f-dispatcher-2026-10-17"` | 27 条登记，**可退出 3，保持 pending 24**（全文 `retirement_decide_20261018.txt`） |
| 四个条件逐条目核对 | 脚本 `tmp/phase_f_first_run_20261016/verify/r128/matrix.py`（只读），结果 `retirement_matrix_20261018.json` | 见第三节 |
| 六条停止条件 | 用窗口内的真实台账与日志核对 | 见第二节 |
| 登记是否被改动 | `git diff config/workflow/legacy_retirement.yaml` | 无改动；没有任何条目被放行 |

## 二、六条停止条件（窗口 2026-10-17 → 2026-10-18）

| 停止条件 | 结果 | 依据 |
|---|---|---|
| ① 每日健康检查失败 | **未触发** | 六个消费者今天重跑都是 `health_ok`；`consumer-health.err.log` 里没有任何非 `health_ok` 记录 |
| ② 同一逻辑触发重复认领 | **未触发** | 调度认领台账（`schedule_runtime_claims`）279 条 complete，同一身份的多条 complete 为 0；`trigger_runs` 的触发键唯一 |
| ③ 必需投影缺失 / 认领长时间 running | **有一次需要你知道的缺失，不是长时间 running** | 当前 running 认领为 0。但今天 12:09 的例行 tick 里，LRCX 的信息简报没有发布（该标的 7 天内没有任何已准入资讯），认领记为 failed，导致 LRCX 的预期基线被上游依赖卡住。原因是**数据缺口**，不是新逻辑把已有数据算坏；详见 `PHASE_F_FIRST_PRODUCTION_RUN_20261016.md` 末尾附录。这一条是否算「触发」要你裁决，我按字面如实标出 |
| ④ 连续两次整批失败（原因是新逻辑缺陷） | **未触发** | 窗口内只有一次 `incomplete`（就是上一行的 LRCX）；它的原因是该标的无数据，条件里明确排除这一类。窗口之前（10-16）的 10 次 `incomplete` 属于首轮生产运行，在窗口之外，已在首轮记录里逐条复盘 |
| ⑤ 外部 API 上限被触发（花费异常） | **未触发** | 本窗口的模型请求都有逐条记录：Macro 1 次、Layer 34 次、Sector 62 次、事件评审若干次（TSM/ASML，复核时命中缓存不再发请求）、今天的例行 tick 14 次。没有出现重试风暴或重复请求；费用上限由你在 API key 一侧控制 |
| ⑥ 任何券商写入尝试 | **未触发** | `trades` 表 2026-10-16 以来 **0 条**（最近一笔仍是 2026-07-23）；`phase-e` 日志里没有下单相关记录；`com.ats.schedule`（带 `--live` 的旧调度）未加载，实盘保持锁定 |

> **2026-10-19 补充（第 ③、④ 条）：** 为验证 10.6，10-18 至 10-19 手动连续跑了 5 次 `incomplete`（LRCX 无资讯、抽取空返回、财务缺失），之后第 6、7 遍均 `complete`；原因都是数据或模型输出问题，已逐个修复。这些失败尝试留下 6 条 `running` 认领，但工作进程均已死亡（最后一条历史记录是 `execution_error`），按设计只在下一次负责人切换时作废，不影响定时 tick。第 ③ 条所说的缺失（LRCX 简报）现已解决。窗口已在 10-18 结束，上表为当时的核对结果。

同期出现、**不属于**停止条件的警告（与 10-17 相同，没有新增）：Macro 模型输出过一次未登记的主题键 `monetary_policy`（已丢弃）、Sector 结构分析在一层上输出校验失败一次（`StructureView` 的列表字段被输出成字符串）、yfinance 与证据判读各有几次网络连接被重置后重试成功。

另一条要如实写下的运行事实：10-17 21:24 起守护进程在一次重启后，Information、Fundamental 例行、Technical 三个定时任务因超过错过容忍时间被**跳过**（`misfire_grace_exceeded`，3 条），不是失败；Macro、Sector 当时已补跑完成。

## 三、四个退出条件，逐条目核对

四个条件是：**清零**（没有消费方还在读旧路径）、**对账**（旧新结果在所有用途上一致）、**回滚窗口**（演练过且停止条件没有触发）、**墓碑**（登记为 retired 的必须真的读不到）。

| 条件 | 27 条的现状 | 说明 |
|---|---|---|
| 清零 | 3 条清零；24 条未清零 | 其中 **4 条**有消费方被**确认**还在旧路径：`chief.legacy_table_direct_reads`、`sector_reviews`、`store.direct_trade_reads`（都是 `agents.chief.assemble`）和 `decision_roles.unwired_to_governed_read_api`（`agents.risk_officer.review`、`execution.clerk`）；其余 **20 条**是**状态未知**：扫描器认不出这些模块属于哪个角色（`MODULE_TO_ROLE` 没登记），按规则不能当作清零 |
| 对账 | **没有对任何条目执行过** | `ats retirement reconcile` 需要逐条目给出全部数据用途。`decide` 不检查这一项，所以「可退出 3」只证明清零与演练，不证明对账。那 3 个条目本身没有数据用途，不受影响；24 个 pending 条目要退出，还必须补这一项 |
| 回滚窗口 | 演练引用已给出，`window_ok` | 如果把上面第 ③ 条（LRCX 简报缺失）按「已触发」处理，`verify_rollback_window` 会改判 `window_refused`——对结论没有影响：24 条 pending 本来就不是因为窗口 |
| 墓碑 | 只用**确认**的读取方判断：0 条不一致 | 但有 **8 条登记里写着 `retired`，实际是「状态未知」**（下表）。这不是已证实的错误，而是登记比证据走得靠前 |

### 登记写着 retired，但证据不足的 8 条（需要你知道）

| 条目 | 目标阶段 | 现状 |
|---|---|---|
| `workflow_memory.evidence_observations` | A | 4 个消费方状态未知 |
| `workflow_memory.evidence_facts` | A | 2 个消费方状态未知 |
| `workflow_memory.evidence_failures` | A | 2 个消费方状态未知 |
| `workflow_memory.evidence_fact_projections` | A | 2 个消费方状态未知 |
| `agents.risk_validator.apply_guardrails` | B | 1 个消费方状态未知 |
| `risk.checks.in_place_clipping` | B | 2 个消费方状态未知 |
| `runtime.server.in_process_resume_dedup` | B | 2 个消费方状态未知 |
| `graph.pead.internal_risk_and_sizing` | D | 2 个消费方状态未知 |

处理建议（需要你决定，不会自动做）：给这些模块在 `MODULE_TO_ROLE` 里补登记并重扫，扫描通过则登记与证据对上；扫描发现仍有读取则要把登记改回 pending。**本次没有动登记。**

### 可退出的 3 条（本来就是 retired）

`agent.evidence_observer`、`reconcile.origin_manual_fallback`、`reconcile.partial_fill_as_filled`：没有消费方，演练引用有效，墓碑一致。

## 四、24 个 pending 条目各缺什么

全部缺的是**清零**；对账没有对任何一条执行过。按目标阶段归类（逐条目的消费方清单见 `retirement_decide_20261018.txt`）：

| 目标阶段 | 条目数 | 条目 | 清零现状 |
|---|---|---|---|
| A | 4 | `workflow_memory.evidence_observations`、`evidence_facts`、`evidence_failures`、`evidence_fact_projections` | 登记为 retired；消费方状态全部未知（`scheduler.observe`、`chain.*`、`runtime.cli` 等） |
| B | 6 | `agents.risk_validator.apply_guardrails`、`risk.checks.in_place_clipping`、`runtime.server.in_process_resume_dedup`（登记为 retired）；`memory.store.legacy_decision_write_path`、`risk.checks.pre_trade_adapter`、`trades.client_order_id.legacy_derivation` | 全部状态未知 |
| C | 4 | `journal.report.render_ledger`、`performance.legacy_snapshot`、`scheduler.adhoc_journal_jobs`、`store.direct_trade_reads` | 前三条状态未知；`store.direct_trade_reads` 另有确认读取方 `agents.chief.assemble` |
| D | 8 | `chief.legacy_table_direct_reads`、`sector_reviews`、`layer_verdict.allocation`、`pead_dossier`、`legacy_read_models`、`task_projections.legacy_columns`、`evidence_fact_projections.legacy_observation_id`、`graph.pead.internal_risk_and_sizing`（登记为 retired） | `chief.legacy_table_direct_reads` 与 `sector_reviews` 有确认读取方 `agents.chief.assemble`；其余状态未知 |
| E | 1 | `scheduler.hardcoded_serial_run` | 状态未知（`runtime.scheduler`、`tests/test_scheduler_jobs`；`com.ats.schedule` 未加载） |
| F | 1 | `decision_roles.unwired_to_governed_read_api` | 确认读取方：`agents.risk_officer.review`、`execution.clerk` |

Chief、风险官、执行文员、交易与记账都不在本 change 的研究工作流范围内，所以这些条目现在不可能清零；这是范围边界，不是遗漏。

## 五、12.8 自己的验证

| 要求 | 结果 |
|---|---|
| 条件全满足才受控退役 | 满足条件的只有登记里本来就是 retired 的 3 条；其余全部保持 pending，没有为放行而降低判据 |
| 历史可读 | 登记文件本次无改动（`git diff config/workflow/legacy_retirement.yaml` 为空），3 条已退出条目的墓碑与历史按原样保留 |
| 保留原 target_phase | 27 条的 `target_phase` 与登记一致（A/B/C/D/E/F 与 `complete-target-dataflow`），未被改动 |
| 缺条件 / 外部历史清理未完继续 pending | 24 条继续 pending，缺项逐条列出 |

**判定：12.8 自身的验证成立（「正确地保持 pending」算成立）。** 它并不意味着旧实现可以删除。

## 六、没有做、也没有验证的事

- **没有退役任何新条目**，也没有改 `config/workflow/legacy_retirement.yaml`；要退役需要你明确说「是」。
- **对账没有对任何 pending 条目执行**（需要逐条目的数据用途清单）。
- **窗口只覆盖两天**，事件路径只验证了 TSM、ASML；LRCX（10-21）、GOOG/SKHY（10-27）、MSFT（10-28）的财报没有等到，这是你 10-18 的裁决。
- 8 条「登记 retired、证据未知」需要补扫描登记后才能判断，见第三节。
- LRCX 的预期基线（10.6）在 10-19 补资讯与财务后已建成，见 `PHASE_F_FIRST_PRODUCTION_RUN_20261016.md` 末尾附录。
- `zero` 命令不带 `--identifier` 时，汇总行显示「0 条登记」，逐消费方表仍是完整 53 项；条目级汇总以 `decide` 为准。

## 附录：命令输出与脚本

- `docs/archive/phase_f_first_run_review_20261016/retirement_zero_20261018.txt`
- `docs/archive/phase_f_first_run_review_20261016/retirement_decide_20261018.txt`
- `docs/archive/phase_f_first_run_review_20261016/retirement_matrix_20261018.json`
- 调度认领台账：`var/phase_f_dispatch.sqlite`（`schedule_runtime_claims`：complete 279 / failed 1 / void 13）
- 触发与运行：`var/ats.sqlite`（`trigger_runs`、`workflow_runs`）
