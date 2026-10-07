# Phase F 验收记录（`implement-phase-f-shadow-run-and-cutover`）

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
