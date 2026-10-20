# 任务 12.3：回滚窗口登记与停止条件（2026-10-17）

> 2026-10-18 修订：观察窗口按你的裁决在 10-18 结束（原建议 10-28 作废），其余内容为 10-17 当时的记录。
>
> 12.3 要的不是「旧业务还能用」，而是三件有证据的事：**回退演练过**、**停止真的封住入口和写点**、**窗口有登记的停止条件且没有被触发**。下面逐项给出证据，并登记观察窗口。

## 一、已演练：安全停止与恢复

| 范围 | 演练 | 证据 |
|---|---|---|
| 7 个研究工作流的调度负责人 | 切到 Dispatcher → 旧入口被拒 → 切回 legacy → 再切回 | `ats dispatch owner-log` 共 35 条追加记录，7 个工作流各有「切换、切回」配对（代次递增） |
| 7 个消费者的读取路由 | target → legacy → target，经真实读取入口解析 | `ats consumer-cutover log`（路由变更均带原因与代次） |
| 调度观察 | 第 1 遍重算、第 2 遍全部复用（0 次请求） | `docs/PHASE_F_10_5_*_RESULT.md`、`docs/archive/PHASE_F_FIRST_PRODUCTION_RUN_20261016.md` |

演练引用（登记用）：`owner-log:35-rows; route-drills:7-consumers; window:phase-f-dispatcher-2026-10-17`。

## 二、停止真的封住了入口和写点、在途工作有处置、历史保留

| 要求 | 怎么验证 | 结果 |
|---|---|---|
| 旧入口被封 | 负责人切到 Dispatcher 后直接调用旧入口 | 报 `schedule frozen or caller is not current owner`；Layer（`tmp/.../layer/old_entry_refused.txt`）、Macro（`tmp/.../macro/08_legacy_refused.log`）实测，并用「调用即失败」的探针保证被拒时不会调用模型、不会写评审行 |
| 写点被封 | 旧调度路径发布结果前要通过当前负责人的栅栏（`execute()`）；分析师产出写入前经 `guard_analyst_output` | `tests/test_schedule_owner_switch.py::test_the_old_entry_loses_authority_after_the_switch_and_regains_it_after_the_rollback` |
| 在途工作有处置 | 有未完成认领时拒绝切换，除非逐条带理由作废；**运行中的认领绝不被作废**，仅「已以 execution_error 结束却仍标 running」的可以作废 | `test_unfinished_claims_block_the_switch_until_voided_on_purpose`、`test_a_running_claim_whose_worker_already_ended_with_an_error_can_be_voided`、`test_a_running_claim_is_never_voided` |
| 历史保留 | 切换日志只增不改；作废的认领保留并带处置记录；已完成的认领切换后仍在 | 台账 `schedule_result_publications` 与 `schedule_owner_log` 有禁止更新/删除的触发器；当前 13 条处置记录、249 条 complete 认领均保留；`test_completed_claims_do_not_block_and_survive_the_switch` |

## 三、登记的观察窗口

| 项 | 内容 |
|---|---|
| 窗口编号 | `phase-f-dispatcher-2026-10-17` |
| 范围 | 7 个研究工作流（information-brief、fundamental-routine、fundamental-event、layer-review、sector-review、macro-review、technical-review）；不含 Chief、审批、记账、交易 |
| 起点 | 2026-10-17（负责人切到 Dispatcher、`com.ats.phase-e` 加载） |
| 终点 | **2026-10-18**（你 10-18 的裁决，取代原先建议的 10-28）。原建议终点要覆盖周六 10-24 的 Macro/Sector 周度运行，以及 LRCX（10-21）、GOOG 与 SKHY（10-27）、MSFT（10-28）财报的事件路径；提前结束后，事件路径只用最近发布财报的 TSM、ASML 验证，其余标的不逐个等待财报。窗口结束后的退役核验见 `docs/archive/PHASE_F_12_8_RETIREMENT_20261018.md` |
| 停止条件（任一出现即 `owner-rollback`） | ①每日健康检查失败；②同一逻辑触发出现重复认领；③必需投影缺失或认领长时间 `running`；④连续两次整批运行失败，且原因是新逻辑缺陷而非「该标的无数据」；⑤外部 API 上限被触发（花费异常）；⑥任何券商写入尝试 |
| 窗口内的回退方式 | `ats dispatch owner-rollback --workflow <id> --actor <谁> --reason <为什么>`；全部停止：`launchctl bootout gui/$(id -u)/com.ats.phase-e` |

## 四、窗口当前状态（2026-10-17 核验）

| 停止条件 | 现状 |
|---|---|
| 健康检查失败 | 未触发：六个消费者 `health_ok` |
| 重复认领 | 未触发：没有任何工作流有共享同一身份的多条 complete 认领 |
| 缺失投影 / 长时间 running | 未触发：当前没有 running 认领；8 条旧 Layer 投影已备份并标为 failed，读取方只取新的 |
| 连续整批失败 | 未触发：启动补跑 macro、sector 均 complete |
| 花费异常 | 未触发：补跑 macro 1 次请求；sector 约 62 次请求 |
| 券商写入 | 未触发：2026-10-16 以来交易表 0 条，最近一笔是 2026-07-23 |

同期出现但**不属于**停止条件的警告（已记录）：Sector 的结构分析在一层上输出校验失败（`StructureView` 的列表字段被输出成字符串）、yfinance 与证据判读各有网络连接被重置的重试。

## 五、机器核验

- `verify_rollback_window`：已演练且无触发 → `window_ok`；未演练 → `window_refused`；演练过但停止条件触发 → `window_refused`。
- `ats retirement decide --drill-reference <演练引用>`：27 个条目里 **3 个可退出**（`agent.evidence_observer`、`reconcile.origin_manual_fallback`、`reconcile.partial_fill_as_filled`，登记状态本来就是 retired），**24 个保持 pending**，缺项逐条列出，没有为放行而降低判据（全文 `docs/archive/phase_f_first_run_review_20261016/retirement_decide_with_window_20261017.txt`）。

**本项不放行任何 pending 条目**；退役要等 12.7、12.8 和窗口结束。
