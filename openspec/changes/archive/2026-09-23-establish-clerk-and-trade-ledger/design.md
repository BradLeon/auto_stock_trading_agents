## Context

见 `proposal.md` 的 Why。此处只列约束本设计的现状事实（行号为实施前的状态）。

- **没有 Clerk，也没有账本模块**：`src/ats/` 下不存在 `clerk.py` 或 `ledger.py`；唯一的 “ledger” 是 `journal/report.py:62` 的 Markdown 渲染器 `render_ledger()`。账本相关的确定性计算散落在 `trader/reconcile.py`、`journal/marks.py`、`journal/episodes.py`、`journal/predictions.py`、`trader/{performance,analytics}.py`，各自带 `run()`，由 `runtime/scheduler.py:512/622/625/640` 的独立 cron 与 `runtime/cli.py:2936-2978` 的子命令 ad hoc 触发，彼此不共享一次事务。
- **对账只有一档且部分失真**：`trader/reconcile.py::reconcile()`（`:71`）以 `_match()`（`:46`）按 order_ref / perm_id / order_id+日期三级匹配，匹配不到即 `origin = "manual"`（`:112`），**没有「无法归因」这一类**；只要有成交价就把订单直接改写为 `status='filled'`（`:138`），**没有部分成交语义**；DAY 单超期按推定置 `expired` 但依据只写在 `error` 文本里（`:158-169`）。运行痕迹只有 `set_meta("last_reconcile_at")`（`:173`）。
- **决策链关联列已就位但未强制**：Phase B 已给 `trades` / `fills` 加上可空的 `revision_no` / `decision_hash` / `approval_id`，`client_order_id` 由 cycle + revision + 序号派生（`memory/store.py:906`），`order_ref` 同构（`broker/ibkr.py:380`）。文档 §14.3 要求的「强关联」尚未成立——新系统订单缺链仍可写入。
- **order attempt 只有计数、不可逐次枚举**：`trades` 以 `client_order_id`（cycle + revision + 修订内序号 + symbol + action，`store.py:906`，唯一索引 `:622`）唯一，重试走 upsert 把 `attempt` 递增并保留 `first_submitted_at`（`:922-957`），`journal_entries.submit_attempts` 从 `trades.attempt` 同步（`:1059-1060`）；每次尝试的券商身份与费用没有独立行。旧单体 design:339 要求 Clerk 消费 order attempt，本阶段需给出可落地的口径（见 D14）。
- **没有持仓与资金表**：`performance` 表只有 `net_liquidation` 等聚合快照（`store.py:36`），全库无 `CREATE TABLE ... position|cash`；而 §11.1 要求 Clerk 读取持仓与资金，§12.1 第 2 条要求这类账户状态经 Runtime Data Gateway 查询。
- **没有归因实现**：仓库无 attribution 模块或表，仅 `journal/doctor.py:166` 把 attribution 作为诊断 section 名出现；`trader/analytics.py` 提供 `episode_stats` / `max_drawdown_pct` / `summarize` 等确定性公式（`:20/:32/:63`），是目前可复用的归因基础。
- **下游直读原始表**：`agents/chief/assemble.py:213`（`recent_trades`）、`:349`（`recent_fills`）、`risk/assess.py:575`（`performance_history`）、`channel/context.py:21`，均绕开任何读模型；Chief 上下文没有接绩效与归因。不存在任何 Internal State API 模块。
- **存在一条 LLM 写回路径**：`journal/invalidation.py:160-176` 用 `run_structured("invalidation_check", ...)` 得到 `view.triggered` 后 `store.save_episode(...)`，写入的是 `trade_episodes.invalidation_triggered`（布尔判定，非金额/身份/归因）。§11.2 要求 LLM 输出只是附加分析，这条路径必须被显式收口而不是靠约定。
- **漏跑受券商能力硬约束**：`config.py:174` 已注明 `reqExecutions` 只返回当日执行、错过的会话无法事后补回；`scheduler.py:874-952` 的 `misfire_grace_hours`（`config.py:235`，默认 6 小时）只决定迟到的 job 是否还跑一次，账本侧无任何缺口登记。
- **可复用的既有契约**：Phase A 的 `workflow/legacy_retirement.py`（`RetirementRegistry` / `read_gate()` / `purge()`，默认干跑）与 `config/workflow/legacy_retirement.yaml` 条目格式；`workflow/run_contracts.py::TriggerContext.idempotency_key()`（**不含请求时刻**）；`workflow/architecture_guards.py` 的守卫写法；Phase B 的 `decision/*` 五表与 `execution/authorization.py`（提供 cycle / revision / hash / review / approval 的权威记录）。
- **迁移机制**：无 migrations 目录；`TradingMemory.__init__` 顺序执行 `executescript(_SCHEMA)` → `_migrate()` → `_retire_data_tables()` → `_verify_data_layer_write_targets()`（`store.py:401-404`）。additive 先例是 `PRAGMA table_info` 探测 + `ALTER TABLE ADD COLUMN`（`:479-487`）；一次性迁移用 `data_migrations` 表的 `<domain>_v1` 键。
- **已被踩过的坑**：新表必须同步登记 `src/ats/data/stores/ownership.py`，否则 `test_legacy_sqlite_tables_have_explicit_data_or_memory_ownership` 失败（Phase A 教训）。
- **测试基线**：权威基线为 2026-09-22 的 `135 failed / 1398 passed / error=0`（完整 `uv` 依赖、无删除配额环境）；受限沙箱计数（121/1149/263）不得作为验收判据。本阶段会触及 `test_reconcile.py`（15）、`test_marks.py`（20）、`test_episodes.py`（22）、`test_journal_*.py`、`test_trader.py`（11）。

## Goals / Non-Goals

**Goals:**

- 建立 Clerk 确定性编排层，把现有 reconcile、marks、episodes、predictions、performance 与归因的确定性计算串联为**可幂等重放**的一条流水线，复用既有公式，不改领域事实。
- 让订单与成交与 cycle / revision / approval 成为强关联：新系统订单缺链即拒绝或登记异常，历史断链显式可见。
- 把归属从「系统 / 人工」两级改为「系统 / 人工 / 无法归因」三级，无法归因产生显式异常项。
- 补齐补偿语义：部分成交累计与收口、迟到成交回填、撤单与拒单的显式终态、进程重启可重入、漏跑窗口登记为可见缺口。
- 让绩效与归因可从不可变原始记录重建（方法版本 + 重建时点 + 结果确定）。
- 发布带 as-of 与完整性标记的 Internal State，并把 Chief / Risk / 复盘的交易与绩效读取迁上去。
- 固化 LLM 边界并加架构守卫，使其无法成为账本事实来源。
- 登记本阶段待退旧实现（只登记，不执行物理清除）。

**Non-Goals:**

- 不改下单入口与审批链语义（Phase B 已交付，本阶段只消费其记录）；真实交易仍保持 paper / dry-run 默认，切真单属 Phase F。
- 不做 Dispatcher 与调度重构（Phase E）。本阶段 Clerk 提供幂等编排入口，**不**删除 scheduler 里的既有 cron 任务，只把它们登记为待退；双轨期由幂等键保证不重复产生事实。
- 不新建持仓 / 现金主数据（组合口径由 Phase B 的组合快照与 `risk/assess.py` 提供，重复建模会造成口径分裂）；持仓与资金只做对账差异登记。
- 不改动任何风险硬规则数值、不改动分析结论、不越过 §12.1 的 Runtime Data Gateway 边界去直连 Provider。
- 不删除任何旧表旧列（§12.4 additive 优先）；不执行任何物理清除。
- 不提供替代历史 statement / 对账单导入通道（已裁决）。受券商接口限制无法自动补回的窗口只登记为缺口；导入需要新的数据源与校验规则，且导入事实必须与券商回报在来源上可区分，否则会污染账本——超出 §14.3 三条目的范围，留作后续独立变更。

## Decisions

**D1. Clerk 落在 `src/ats/execution/`，是确定性服务，不是 Agent。**
新增 `execution/clerk.py`（编排与补偿）、`execution/ledger.py`（关联与归属分类、异常登记）、`execution/state_api.py`（读模型发布）、`execution/rebuild.py`（绩效与归因重建）。
*理由*：仓库的 `execution/` 域已由 Phase B 建立（`execution/authorization.py` + `execution/authorization-gate` 能力），Clerk、账本与确定性引擎按既定归域同属该域；§11 首句明确 Clerk 是确定性 Workflow Service。
*替代方案*：新建顶层 `clerk/` 包（被否，会让「执行域」一分为二，且 Phase B 已确立的领域划分需保持一致）；把编排塞进 `journal/`（被否，journal 是派生叙事层，与账本事实层职责相反）。

**D2. 账本事实仍由各服务在事件发生时写入，Clerk 只做串联、对账、补偿与发布。**
Clerk 不重写 `trades` / `fills` 的提交意图、数量、标的与决策链标识，只补对账、归属、补偿与派生所需字段。
*理由*：§11 首段明确「各业务服务在事件发生时以事务性写入自己的领域记录；Clerk 负责串联、对账、补偿和发布读模型，不是在事后猜测事实」。若 Clerk 兼写事实，一旦它与写入方不一致就没有可仲裁的原始记录。
*替代方案*：Clerk 统管所有写入（被否，把单点写入变成单点猜测，且与现有 Trader 写路径冲突）。

**D3. 强关联采用「写路径强制 + 列保持可空」，不引入数据库非空约束（已裁决）。**
新系统订单与成交的写路径校验 `cycle_id` / `revision_no` / `decision_hash` / `approval_id` 齐全，缺任一即拒绝写入并登记审计异常；列本身保持可空以容纳历史行、人工单与无法归因单。**除主键外不设任何 NOT NULL 约束**：本阶段新增或修改的列一律可空，强制只发生在写路径上。
*理由*：§14.3 要「强关联」，而 §12.4 要求 additive 且不伪造审计字段——历史行的链接无法还原，强制 NOT NULL 会逼出假值或阻断迁移。把强制点放在写路径上，可以同时满足「新单必全链」与「历史诚实留空」。
*裁决*：旧单体规划 5.1 的「强制非空」存在两种读法（DB NOT NULL / 写路径强制），本 change 按用户裁决取后者，并进一步明确「除主键外字段一律可空」——库级约束留给以后的独立变更评估（SQLite 需重建表）。
*替代方案*：直接给列加 NOT NULL（被否，历史行与人工单无链可填，要么伪造要么迁移）；按归属加条件 `CHECK`（被否，需重建表且把业务规则下沉到 DB）；只加列不校验（被否，等于 Phase B 现状，强关联不成立）。

**D4. 归属三类化，并复用既有证据链。**
`origin` 从 `system|manual` 两级改为 `system|manual|unattributed` 三级；判定依据继续用 `link_confidence` 四级证据（`order_ref` / `perm_id` / `order_id+date` / `none`），其中 `none` 由当前的「归为 manual」改为「归为 unattributed 并生成异常项」。人工订单保留券商身份并单独呈现，不进入系统交易绩效。
*理由*：§11.1 第 4 条与 §15.4 明确要求区分三类且「无法归因不等于忽略」；当前 `reconcile.py:112` 把一切匹配不上的成交静默计为 manual，正是该条要制止的行为。
*裁决*：旧单体 spec 允许「manual **或** unattributed」二选一，本 change 按用户裁决收紧为**一律 unattributed**，不留实现者选择空间——该二义性正是现状缺陷的成因。
*替代方案*：新增布尔 `unattributed` 标记（被否，与 `origin` 并列会产生「manual 且 unattributed」这种无意义组合）；删除无法归因的成交（被否，直接违反 §11.1）。

**D5. 部分成交用累计语义收口，禁止「有价即完全成交」。**
同一订单的多笔成交按 `exec_id` 幂等入账，订单侧累计已成交数量与按成交量加权的均价；在券商给出终态（或超期推定）之前状态保持 `partial`。
*理由*：`reconcile.py:138` 现在只要见到价格就置 `filled`，使部分成交在账本上消失，直接违反 §14.3「增加…部分成交…补偿」与 §15.4 的 Clerk 处理清单。
*替代方案*：保留现状、只在展示层区分（被否，账本事实本身就是错的，绩效与归因会随之失真）。

**D6. 漏跑以显式缺口登记，不伪造重放。**
对每个会话日登记对账窗口状态；券商仍能返回的窗口走补偿重放，券商已无法返回（受 `reqExecutions` 当日限制）的窗口登记为 `reconciliation_gap` 并给出影响范围，计入 Internal State 的完整性。
*理由*：`config.py:174` 说明错过的会话无法事后补回——此时唯一诚实的做法是让缺口可见，否则下游会把「没跑过」当成「跑过且没问题」。
*替代方案*：用最近一次成功结果填充（被否，制造假事实）；静默跳过（被否，就是现状）。

**D7. 终态必须带依据来源。**
终态来源区分 `broker`（券商明确回报的成交 / 撤单 / 拒单）与 `inferred`（无券商证据的推定，如 DAY 单超期），推定终态保留依据文本与判定时点，且可被后续券商证据修正。
*理由*：§11.1 要求读取撤单与拒单；现有的 `expired` 推定把依据塞进 `error` 文本，无法被机器区分，也无法在补偿时追溯。
*替代方案*：只保留券商明确状态、其余永远在途（被否，账本无法收敛，绩效无法结算）。

**D8. 持仓与资金只做差异登记，不新建主数据。**
Clerk 经 Runtime Data Gateway（§12.1 第 2 条）读取券商持仓与资金，与本地账本推导值比对，差异以显式差异项落地（含两侧数值、标的、时点），不静默覆盖任一侧。
*理由*：组合口径已由 Phase B 的组合快照与 `risk/assess.py` 承载，重复建一套持仓表会让「哪个是权威」分裂；差异项既能满足 §11.1 的对账要求，又能保留可审计的两侧证据。
*替代方案*：新建 `positions` / `cash` 表并由 Clerk 维护（被否，与既有组合快照双轨且需要额外的写入权威仲裁）；不读持仓资金（被否，违反 §11.1）。

**D9. 派生读模型单表承载，与方法版本绑定。**
新建 `ledger_read_models`（kind ∈ {performance, attribution}、period、as_of、`method_version`、`source_facts_hash`、`payload`、`rebuilt_at`）。重建只读写模型，不改原始记录；同一方法版本 + 同一组原始事实必须产生相同 `payload`。
*理由*：§15.4 要求「绩效和归因可从不可变原始记录重建，重建不改变原始事实」；把方法与输入指纹记下来，重建结果才可复核、才可解释新旧差异。现有 `performance` 表无方法版本与输入指纹，无法支撑该条。
*替代方案*：原地扩展 `performance` 表（被否，它是按 cycle 的快照表，语义与「可按期重建的派生读模型」不同，混用会让旧读入口继续读到重建结果）。

**D10. Internal State 以 as-of + 完整性为一等字段，消费方迁移后旧直读待退。**
`state_api.py` 发布 `InternalState`（as_of、交易历史、绩效、归因、组合口径、审计异常；`completeness` 含未对账窗口 / 无法归因成交 / 历史断链的计数与 `complete|degraded` 状态）。Chief、Risk、`channel/context.py` 改读该接口；旧直读点登记为待退，退出条件是消费方全部切换且旧直读调用清零。
*理由*：§11.1 末条与 §15.4 要求发布供下一轮 Chief / Risk / Clerk 读取的内部状态，且缺口必须可见；若消费方继续直读原始表，完整性标记形同虚设。
*裁决*：旧单体规划把 Chief / Risk 消费方迁移放在 Phase D（7.10），本 change 按用户裁决提前到本阶段——先迁移才能真实验证读模型是否满足消费方需要，否则「只发布无人消费」等于没验证。
*迁移边界*：本阶段只迁**交易历史与绩效**这两段读取（`agents/chief/assemble.py` 的 `recent_trades` / `recent_fills`、`risk/assess.py` 的 `performance_history`、`channel/context.py` 的交易段）；Chief assembler 的整体重构（读六类分析投影）仍属 Phase D，本阶段不在同一文件内做结构改动，避免与 D 撞车。
*替代方案*：只发布接口不改消费方（被否，缺口不可见的现状不变，且无法验证接口是否满足消费方需要）；一次性删除旧直读（被否，违反 §12.4 的兼容迁移）。

**D11. LLM 边界用「字段白名单 + 架构守卫」固化，而不是靠模块自觉。**
`invalidation_triggered` 一类 LLM 判定归入标注类字段白名单并带来源与生成时点标记；新增守卫测试断言：LLM / critic 相关模块不得引入账本写路径，且 LLM 分支只允许更新白名单字段。
*理由*：§11.2 与 §16 第 3 条要求 LLM 无权回写券商与确定性结果；现在这条只靠 `journal/critic.py:46-53` 的注释约定维持，没有机器校验。`invalidation.py` 的实际写入是布尔判定，属允许范围，但必须被显式界定，否则后续很容易顺手多写一个字段。
*替代方案*：禁止 LLM 写任何字段（被否，会让既有的失效判定能力失效，超出本阶段目标）；维持现状靠约定（被否，无机器校验，是最容易腐化的一种）。

**D12. 全部 additive，新表登记归属，待退只登记不清除。**
新表加入 `store.py` 的 `_SCHEMA` 与 `_migrate()` 探测式写法，并同步登记 `data/stores/ownership.py` 的 `WORKFLOW_MEMORY_TABLES`（承载决策与交易后果，属 Workflow memory）；本阶段替换的旧行为按 §12.4 登记墓碑，`purge()` 保持默认干跑。
*理由*：§12.4 第 1、5 条；且漏登记 `ownership.py` 是 Phase A 已踩过的坑。

**D13. 双轨期由幂等键保证不重复产生事实。**
Clerk 编排入口上线后，scheduler 的既有 cron 在过渡期仍可运行；两者对同一窗口产生相同幂等键，因此不会重复入账。Phase E 统一调度时再关闭 cron 入口（届时按登记表的退出条件判定）。
*理由*：避免在 Phase C 里顺手做 Phase E 的调度收口，把两件事的回滚面混在一起。
*替代方案*：本阶段就切断 cron（被否，会让 marks / performance 在 Clerk 未验证前失去生产触发路径）。

**D14. 重放以 order attempt 序列为最小单位（纳入重试序列）。**
同一提交意图由 `client_order_id`（cycle + revision + 修订内序号 + symbol + action，见 `memory/store.py:906`）唯一标识；该意图的每次提交尝试不新开行，而是把 `trades.attempt` 递增并保留首次提交时点（`store.py:922-957` 的 upsert），`journal_entries.submit_attempts` 从 `trades.attempt` 同步（`:1059-1060`）。Clerk 的对账与补偿 SHALL 按「意图 + attempt 计数」处理：重试 SHALL 被识别为同一意图的新一次尝试，SHALL NOT 产生第二笔订单事实或重复计入成交与绩效；重放 SHALL 保留并核对 attempt 计数，本地计数与券商侧同一意图的回报次数不一致 SHALL 登记为审计异常。
*已知限制*：attempt 序列目前只有计数、不可逐次枚举（每次尝试的券商身份与费用没有独立行）。本阶段**不**新增 attempt 明细表——明细会让「一笔订单到底对应哪条事实」重新出现双源问题，且超出 §14.3 三条目的范围；若后续需要逐次尝试的费用与身份分析，应作为独立变更新增 `order_attempts` 并明确其与 `trades` 的关系。
*理由*：旧单体 design:339 已把 order attempt 列入 Clerk 消费清单；Phase B 让 `place_orders` 对不确定结局不再重提，但账本侧尚未把 attempt 序列纳入重放口径，导致「重试」与「重复成交」在重放时无法区分，执行质量分析（`journal/critic.py:99` 已在对比重试与未重试的胜率）也缺少可靠依据。
*替代方案*：只按 `client_order_id` 去重（被否，丢掉 attempt 计数这一事实，无法解释重试）；只按成交号去重（被否，无法表达「该意图曾重试 N 次」）；新增 attempt 明细表（被否，见上，留作后续独立变更）。

## 待退项登记（Phase C）

本阶段需在 `config/workflow/legacy_retirement.yaml` 追加（只登记，不清除）：

| 标识 | 替代实现 | 状态 | 退出条件 |
|---|---|---|---|
| `reconcile.origin_manual_fallback` | Clerk 三类归属 + 无法归因异常项 | 实施后 retired | 新分类写路径上线且 `origin` 不再出现无依据的 manual |
| `reconcile.partial_fill_as_filled` | 累计式部分成交与收口 | 实施后 retired | 订单状态不再由「见到价格」直接置 filled |
| `scheduler.adhoc_journal_jobs` | Clerk 幂等编排入口 | pending | Phase E 调度收口，cron 全部改由编排入口触发 |
| `store.direct_trade_reads` | Internal State API | pending | Chief / Risk / context 全部改读 API 且直读调用清零 |
| `journal.report.render_ledger` | 账本读模型 | pending | 报表与 CLI 改为读 `ledger_read_models` |
| `performance.legacy_snapshot` | `ledger_read_models`（带方法版本） | pending | 绩效读入口全部切换到可重建读模型 |

## Risks / Trade-offs

- [归属与部分成交的语义变更会同时打破 `test_reconcile.py`（15）、`test_marks.py`（20）、`test_episodes.py`（22）中的计数与状态断言] → 实施顺序上先落存储与异常项（不改行为），再逐个改写断言；每条改写都必须以「旧行为为什么错」为注释留痕，禁止直接改期望值。
- [scheduler 双轨期 Clerk 与 cron 同时运行，可能重复触发派生计算] → D13 的窗口幂等键覆盖两者；派生读模型按 `source_facts_hash` 命中即跳过重算。
- [无法归因成交量可能不小（账户级执行流含历史与手工单），异常项会很多] → 异常项只登记不阻断；Internal State 用计数与 `degraded` 呈现，不逐个抛给 Chief 上下文。
- [漏跑缺口一旦显性化，历史窗口会一次性出现大量 `incomplete`] → 只对可枚举的会话窗口登记，并在迁移时给出一次性盘点脚本（只读输出计数），不承诺回填规模。
- [Internal State 迁移面较大（Chief / Risk / context）] → 先并行双读并比对差异，再逐个切换消费方；未切换完成的消费方继续留在待退表，不假装已完成。
- [新增表漏登记 `ownership.py` 会打断架构守卫] → 列为显式任务，并在实施第一步就跑该守卫测试。
- [受限测试环境的删除配额会制造假失败] → 验收只用权威基线口径，并对本阶段触及的测试文件做 `git worktree` 基线对照，不以受限计数下结论。

## Migration Plan

1. **存储先行**：`ledger_exceptions`、`clerk_runs`、`ledger_read_models` 三表 additive 落库（含索引）、`ownership.py` 登记、一次性历史盘点脚本（只读）。此步不改行为，基线应无变化。
2. **关联与归属**：写路径的四字段强制校验与断链异常；`origin` 三类化与证据链复用；历史断链登记为缺口。此步改动 `reconcile.py`，同步改写相关断言。
3. **补偿语义**：部分成交累计与收口、迟到成交回填、撤单 / 拒单 / 推定的显式终态与依据、Clerk 运行留痕与窗口幂等键、漏跑窗口登记。
4. **重建与读模型**：`rebuild.py` 按方法版本与输入指纹重建绩效与归因；`ledger_read_models` 落库；`state_api.py` 发布带 as-of 与完整性的 `InternalState`。
5. **消费方迁移**：Chief / Risk / `channel/context.py` 改读 Internal State（先双读比对），CLI 增加重建与缺口查看入口；旧直读点登记待退。
6. **门禁与登记**：LLM 字段白名单与架构守卫；待退项写入 `legacy_retirement.yaml`；本阶段测试全绿 + 触及文件的基线对照无未归因失败；确认真实下单仍为 paper 且入口仍唯一。

**回滚策略**：schema 只增不减，因此回滚不需要数据回填——关闭 Clerk 编排入口即可让 scheduler 的既有 cron 与旧读入口继续服务；已登记的异常项、缺口与运行留痕属审计记录，任何情况下不得删除。若部分成交语义需要回退，只需把累计式收口关闭并恢复旧状态判定，但已登记的异常项保留。

## Open Questions

- **episodes / marks / predictions 是否也要「可重放的派生读模型」**（用户暂缓，不阻塞本阶段）：旧单体规划 5.5 与 design:342 要求 episode 可重建；本阶段只在编排层串联它们，`ledger_read_models` 仅承载 performance / attribution。若后续需要，应在不改原始事实的前提下把它们纳入读模型或新增方法版本。另注：`journal_entries` 属**领域记录**（意图在决策时写入，Phase B 已如此），不改作派生。
- **归因口径**：仓库当前没有独立归因实现（仅 `trader/analytics.py` 的确定性公式与 `journal/doctor.py:166` 的诊断 section）。本阶段以既有公式封装为首版口径并打上方法版本，若后续需要更细的归因维度，应在不改原始事实的前提下新增方法版本，而不是改写既有结果。
- **历史漏跑的补回方式（已裁决）**：受券商接口限制无法自动补回的窗口只登记为缺口，本阶段不建人工导入通道。唯一允许的「补回」是在券商仍能返回该窗口时走补偿重放；凡无法重放的窗口，账本一律以缺口呈现，绝不填充。
- **Internal State 是否包含实时持仓**：本阶段先以组合快照 + 对账差异项提供持仓口径，不引入实时持仓主数据；若 Chief / Risk 后续要求实时口径，应扩展组合快照而非新建持仓表。
