## Context

见 `proposal.md` 的 Why（已定位到 `graph/chief.py:78-158`、`risk/checks.py:80-141`、`runtime/server.py:15-41`、`trader/execute.py:33-35`、`store.py:25-35/690-697`）。此处只列约束本设计的现状事实。

- **决策链是单次通过图**：`graph/chief.py:231-250` 注册 7 个节点，`risk_gate` 与 `persist_decision` 是两个相邻节点，落库写 `cycles` + `decisions`。`ChiefDecisionState`（`graph/chief_state.py:19-53`）里与风险相关的字段只有 `decisions` / `risk_notes` / `approval_summary` / `approval` / `order_results` / `fills`，没有 revision、hash、轮次、快照标识。
- **LangGraph checkpointer 已就绪且已按渠道区分**：`graph/checkpoint.py:54-74` 提供 `MemorySaver` 与 `SqliteSaver` 两种，`runtime/cli.py:41` 仅异步渠道持久化，`runtime/cli.py:1620-1622` 以 `Command(resume=...)` 恢复；`thread_id == cycle_id`（`chief_state.py:20`）。设计文档 §5.4 第 5 条要求把它当运行恢复设施，审计真相另存。
- **风控是就地改写**：`risk/checks.py::pre_trade()` 返回 `(approved_decisions, notes, review)`（`checks.py:18-21`），其中 `_apply_order_caps()`（`92-117`）与 `_clip_event_notional()`（`120-141`）直接改写名义金额，未通过的订单在 `80-83` 被 `continue` 丢弃；`risk/assess.py::assess()`（`340-635`）本身对订单只读，产出 `RiskReview`（`schemas/risk.py:261-292`，含 `breaches` / `cautions` / `directive` / `notes`）与 `RiskDirective`（`200-209`，含 `blocked_entities` / `blocked_layers` / `risk_budget_remaining` / `required_repairs`）。
- **未接线的旧风控仍在仓库与测试中**：`agents/risk_validator.py::apply_guardrails()`（`22-109`）保留 drop / clip / cap / inject / scale 全套静默改写，`src/` 已无引用但 `tests/test_risk_validator.py` 仍断言它。
- **存储现状**：决策与交易相关表为 `cycles`、`decisions`（无常量键）、`trades`（承担订单职责，`store.py:32-35` + `483-504`）、`fills`（`73-76` + `505-510`）、`risk_reviews`（`as_of` 主键，`77-79`）、`journal_entries`（`116-137`）。**没有 `orders` 表**，文档 §12.3 的 `decision_cycles` / `decision_revisions` / `decision_risk_reviews` / `boss_approvals` / `cycle_events` 五张表均不存在。
- **迁移组织方式**：无 migrations 目录；`TradingMemory.__init__` 顺序执行 `executescript(_SCHEMA)` → `_migrate()` → `_retire_data_tables()` → `_verify_data_layer_write_targets()`（`store.py:401-404`）。additive 先例是 `PRAGMA table_info` 探测 + `ALTER TABLE ADD COLUMN`（`479-487`）；一次性迁移用 `data_migrations` 表加 `<domain>_v1` 键（`573-599`、`604-648`）；改主键用重建式迁移（`650-682`）。
- **可复用的 Phase A 契约**：`agent/task_projection.py` 的 `TaskProjectionEnvelope`（`projection_id` / `agent_role` / `scope` / `as_of` / `valid_until` / `input_refs` / `data_vintage_refs` / `content_hash` / `is_expired()`）与 `reuse_decision()` 的理由枚举；`workflow/run_contracts.py` 的 `TriggerContext.idempotency_key()`（**不含请求时刻**）与 `WorkflowTaskSpec.freshness_seconds` / `timeout_seconds`；`workflow/legacy_retirement.py` 的 `RetirementRegistry` / `read_gate()` / `purge()` 与 `config/workflow/legacy_retirement.yaml` 的条目格式（`identifier` 点分、`capability_domain` 斜杠路径、`target_phase` 单字母、`exit_condition`、`consumers`、`consumer_zero_criterion`）。
- **配置现状**：风险数值在 `config/risk.yaml` 的 `limits:`（`max_single_order_usd: 25000`、`max_position_pct: 0.25`、`max_event_loss_pct: 0.03` 等），代码侧的对应 dataclass 是 `RiskConfig`（`config.py:117-152`）。**当前没有任何新鲜度 / 超时 / staleness 字段**；最接近的先例是 Phase A 的 `freshness_seconds` 与 `config/data/*.yaml` 的 `stale_after_hours`。
- **会被本次改动触发的既有断言**：`tests/test_risk.py::test_pre_trade_event_clip`（`96-103`）、`tests/test_chief_graph.py::test_derisk_blocks_buys_before_review`（`76-86`）、`tests/test_server.py::test_duplicate_callback_executes_once`（`40-63`，通过清空进程内 `server._RESUMED` 实现）、`tests/test_risk_validator.py`。

## Goals / Non-Goals

**Goals:**

- 让「被批准的提案」与「被执行的提案」在存储层面成为同一个不可变对象，且该对象的内容哈希可独立复核。
- 让风险审查成为**只读判定 + 结构化回退**：任何裁剪、替代、拒绝都以记录表达，不再改写提案或静默丢弃。
- 让执行入口成为唯一网关：无完整授权不能提交，授权过期必须重新审查并重新审批。
- 让审批与状态转换的幂等性在进程重启与多进程部署下成立。
- 保持每一步都可回滚：schema 只增不减，旧读入口在切换期保持可用，真实下单仍为 paper / dry-run 默认。

**Non-Goals:**

- Clerk 编排、券商对账、绩效与归因读模型（Phase C）。本阶段只把 cycle / revision / approval 关联列加进 `trades` / `fills`，**不**强制其在新系统订单上非空。
- 六个分析角色的职责重构，**包括「移除 Risk Officer 对 Macro 观点的依赖」**——设计文档 §14.4 把它列为 Phase D，本阶段不动（见 Decisions D11）。
- Dispatcher 运行时与自动事件日历（Phase E）。本阶段不引入任务注册驱动的调度，也不把 `cycle_events` 当作 trigger ledger。
- 影子运行、路径切流与回滚演练本身（Phase F）。本阶段只保证「任一时刻只有一条可执行真实交易的入口」这一前提成立。
- 不改动任何风险硬规则数值、不改动任何分析结论、不改变 `langgraph` 的使用边界（§16 第 10 条）。

## Decisions

**D1. 审计记录落在既有 Workflow memory 库，不新建存储域。**
`decision_cycles` 等五张表加入 `store.py` 的 `_SCHEMA` 与 `_migrate()`，与 `decisions` / `trades` / `fills` / `risk_reviews` 同库。
*理由*：审计记录承载含观点的决策，按 §12.1 第 4、5 条属 Workflow memory；且执行授权与订单关联需要在同一库内做一致性读取，跨库无法用事务与连接级约束。
*替代方案*：为决策审计新建独立 sqlite 文件（被否，跨库关联与事务不可用，Phase C 的订单对账会立刻需要它们同库）；复用 `data/` 数据层存储（被否，决策是观点而非中性事实，违反 §12.1 第 4 条）。

**D2. 新增的修订记录是权威，既有 `decisions` 表降级为兼容快照并双写过渡。**
`decisions`（`store.py:28-31`）没有常量键与哈希，无法表达「同一次决策的第几版」，但其读入口（决策历史、报告）数量未知。本阶段让新写路径同时写 `decision_revisions` 与 `decisions`，读路径逐步改指前者；`decisions` 作为待退项登记，退出条件是读入口全部切换且旧表零写入。
*理由*：满足 §12.4「在新写路径稳定前保留旧读入口，并使用读模型或双写适配层过渡」，避免为审计模型一次性打断既有报表与 CLI。
*替代方案*：直接以新表替换 `decisions`（被否，破坏面不可控，且无法保持可运行中间态）；在 `decisions` 上原地加列（被否，该表无主键，加 `revision_no` 后仍无法保证不可变与唯一性，且会让既有查询语义漂移）。

**D3. `decision_hash` 的规范化复用 Phase A 的规范化做法。**
哈希输入为 `canonical_json` 后的（交易指令、决策理由、关键输入引用）三元组，用 sha256 前 32 位十六进制。
*理由*：与 `task_projection.content_hash()`（`task_projection.py:385-406`）同构，避免仓库内出现两套「内容哈希」语义；排序键 + 去空白可消除无关序列化差异。
*替代方案*：直接 `model_dump_json()`（被否，键序依赖字段声明顺序、浮点表示不稳定，会让同一提案产生不同哈希）；用整表行的 hash（被否，时间戳等审计字段变化会导致哈希抖动）。

**D4. 表名遵循 §12.3，并对「文档有 `orders` 表、仓库没有」这一差异显式记录。**
五张表用 §12.3 的名称；订单关联列加在 `trades` 与 `fills` 上。文档 §12.3 的「现有 `orders` 和 `fills`」在仓库中的等价物是承担订单职责的 `trades`。
*理由*：命名与文档一致才能让 Phase C/F 直接对齐；擅自改名会制造第二套术语。
*替代方案*：按文档新建 `orders` 表并迁移 `trades`（被否，§12.4 明确不删旧表，且 `trades` 已被 journal、fills 关联与对账逻辑消费）。

**D5. 状态机在既有 `graph/chief.py` 上演进，不新建平行图。**
按 §5.4 的建议扩展 `ChiefDecisionState`，把 `persist_decision` 拆为「按轮持久化 revision」与「持久化 risk review」，新增 `chief_revise` 节点与 `risk_gate` 后的条件边；`thread_id == cycle_id` 与既有 checkpointer 继续用于中断恢复与跨进程恢复。
*理由*：§5.4 已明确给出演进路径；且新建平行图会在切换期同时存在两条可下真单的路径，直接违反 §15.5 第 5 条与 §16 第 4 条。
*替代方案*：新建 `decision/` 独立图并保留旧图（被否，双重入口）；把状态机做成非 LangGraph 的纯函数编排（被否，会丢掉 `interrupt` / `Command(resume=...)` 这条已实测可用的审批恢复通道，`tests/test_chief_graph.py::test_trader_thread_resumes_on_chief_graph` 依赖它）。

**D6. 风控返回值契约变更，但保留一个薄适配层作为过渡。**
`risk/checks.py` 新增不修改提案的审查入口（逐单返回判定 + 结构化 `violations` / `allowed_boundary`），`pre_trade()` 保留为 deprecated 适配层（内部改为「审查结果 → 旧的裁剪式输出」），使未及改造的调用点在切换期仍可运行。
*理由*：`pre_trade()` 的调用点分布在 `graph/chief.py`、`runtime/scheduler.py`、`runtime/cli.py` 与多组测试；一次改签名会让工作区长期处于不可运行的中间态，无法逐步验证。适配层本身进待退登记，退出条件是调用点全部切换。
*替代方案*：一次性替换返回契约（被否，无法保持可运行的中间态）；保留裁剪行为只加日志（被否，违反 §5.2 第 3 条与 §10.2 第 4 条这条硬约束）。

**D7. 风控不再丢弃提案，改为「带原因的驳回结果」进入审查记录。**
原 `80-83` 的 `continue`、以及无法归一动作、无法推导交易后仓位的情形，全部改为产生一条 `reject` 审查明细（含原因码），该提案因此不出现在批准集合中。
*理由*：`tests/test_action_vocabulary.py` 已确立「未知动作必须拒绝而非静默降级」的先例（Phase A 的 `agent/action-vocabulary`）；同一原则适用于「无法判定风险」。
*替代方案*：保留丢弃并加警告日志（被否，日志不是审计记录，§10.2 要求审查结果可复核）。

**D8. 新鲜度阈值放在 `config/risk.yaml` 的 `limits` 段，读入 `RiskConfig`。**
新增 `max_snapshot_age_seconds: 60`，由 `RiskConfig` 承载并作为默认值传入校验；执行前的判定使用它，且**只从配置读**。
*理由*：§10.4 要求「阈值应置于风控配置中，不允许运行时静默放宽」；`config/risk.yaml` + `RiskConfig` 是现成的、唯一的风险数值入口（`config.py:117-152`）。
*替代方案*：放进 `config/settings.yaml`（被否，那里是调度与运行设置，混入风控阈值会让「风控配置」出现两个来源）；作为常量写死在代码里（被否，文档明确要求可配置）。

**D9. 执行授权是派生视图，不新建持久表。**
授权由 `decision_cycles` 当前修订 + 通过的 `decision_risk_reviews` + 有效的 `boss_approvals` 在运行期构造（含 `portfolio_snapshot_id` 与 `market_as_of`，两者已存在于审查记录中），并通过图的状态传给执行节点。
*理由*：§12.3 的审计表清单未列授权表；授权是可从三张记录唯一确定的派生事实，独立表会造成第二处真相（复用 Phase A 对 `task_projections` 的同类判断）。
*替代方案*：新建 `execution_authorizations` 表（被否，与 §12.3 清单冲突且引入可被改写的第三份真相）。

**D10. 人工审批的幂等键持久化在审批记录上，键不含请求时刻。**
`boss_approvals` 增加 `idempotency_key` 唯一索引，键由过程、修订、哈希与审批渠道派生；重复回调先查该键并返回原记录。针对非当前修订、已终结过程、已失效审查的回调直接拒绝。
*理由*：§10.3 的两条要求（幂等返回原结果、不得依赖进程内集合）；「不含请求时刻」沿用 Phase A `TriggerContext.idempotency_key()` 已确立的原则，否则重试会得到不同键。
*替代方案*：用请求中的时间戳或消息 ID 作键（被否，前者不可重放，后者把幂等性交给上游）；保留进程内字典并加持久化落盘（被否，多进程下仍会各自处理）。

**D11. 与并行规划 change 的冲突以设计文档为准：Risk 对 Macro 观点的依赖不在本阶段移除。**
`refactor-workflow-dataflow-architecture` 的任务 3.8 把「移除 Macro 观点输入」放在 Phase B，而设计文档 §14.4 把它列为 Phase D。
*理由*：用户指令明确要求「不要偏离需求」指向 `docs/TARGET_WORKFLOW_DATAFLOW.md`；且该重构会同时改动风险角色的输入契约，与 Phase D 的角色职责重构属同一批变更，拆开做等于做两次。
*替代方案*：把 3.8 并入本阶段（被否，超出 §14.2 的清单）。本阶段仅在风险返回值契约变更时保持 Macro 输入的现有接线不动，并保证 LLM 文字不得覆盖硬规则判定。

**D12. 循环终态副作用只在进入终态时执行。**
PEAD 信号消费（`graph/chief.py` 现有的一次性副作用路径）与终态报告生成改为在条件边判定终态后触发，中间轮次只写修订、审查与事件。
*理由*：§5.4 第 4 条明确要求，否则多轮修订会重复消费同一批上游信号。
*替代方案*：依赖幂等键去重副作用（被否，会掩盖重复执行而非阻止它，且上游消费点未必都幂等）。

**D13. 人工补单也开决策过程。**
`trader/execute.py::manual()` 一类的入口改为走「创建过程 → 单轮修订 → 审查 → 审批 → 授权」，并在记录中标注来源为人工。
*理由*：§14.2 要求「所有手动和自动交易指令进入同一审批链」；若允许人工路径直接构造授权，等于保留一条绕过审查的通道（§15.5 第 5 条）。
*替代方案*：允许人工单跳过审查但强制 dry-run（被否，人工单恰恰是最需要留痕与限额约束的一类）。

**D14. 本阶段完成后真实下单仍保持 paper / dry-run 默认。**
新路径全部就绪但切真单属 Phase F；本阶段的门禁是「新路径在 paper 模式下全绿且任一时刻只有一条可下真单入口」。
*理由*：§15.5 的上线门禁第 3 条要求先完成影子运行。
*替代方案*：随本阶段切换真实交易（被否，未经影子运行与回滚演练）。

**D15. 审查的评估单元是「整条修订」，且审查记录必须含执行前后的风险指标。**
新的只读审查入口接收一条完整修订，先合并该修订全部指令的后果，再评估逐单限额、行业层与相关簇集中度以及依赖组合状态方可判定的规则；审查记录在 `violations` / `allowed_boundary` 之外同时保存 `before_metrics_json` 与 `after_metrics_json`（由 `risk/assess.py` 的既有确定性计算产出，数值不变）。
*理由*：§3 的风控规则包明确包含「实体、单票、行业层、相关簇、回撤与事件限额」——只有以整条修订为单元才能评估行业层与相关簇；§5.3 表、§7.3 的 `DecisionRiskReview`、§10.2 第 3 条与 §12.3 都要求保存交易前后指标，且 §5.3 说明了它的用途：让书记员与复盘能区分「投资判断改变」与「订单仅因风险预算被压缩」。旧实现虽然也是批量入参，但返回值只给裁剪后的决策；若不显式要求「整体评估」与「前后指标」，改造时极易退化为逐笔校验并丢掉指标。
*替代方案*：逐笔独立判定后逐笔放行（被否，行业层与相关簇规则会被绕过，等于放宽硬限额）；只存 `violations` 不存指标（被否，复盘无法量化风险变化幅度，也无法与 Phase C 的归因对齐）。

## Risks / Trade-offs

- [风控返回值契约变更会同时打破 4 组测试与 3 个调用点，容易长期停在不可运行状态] → 按 D6 保留 deprecated 适配层，并规定实施顺序：先落存储与哈希 → 再改风控返回契约（适配层保证旧调用点仍可运行）→ 再改状态机 → 最后改执行入口；每一步都在可运行的中间态上验证。
- [`decisions` 双写期会出现两份「当前提案」，读者可能读到旧的一份] → 规定 `decisions` 在过渡期只作为兼容读模型且**不含**新路径的批准语义；任何授权判定只读 `decision_revisions` 与审查/审批记录；退出条件写进退役登记（读入口全切 + 旧表零写入）。
- [内容哈希规范化若与 Phase A 的 `content_hash()` 实现漂移，会出现两套不可互比的哈希] → D3 明确复用同一规范化函数与同一摘要长度；设计上要求哈希计算只有一处实现，测试以「同一语义不同序列化必须同哈希」与「实质字段变化必须换哈希」两侧夹住。
- [把五张表加进 `TradingMemory` 会与数据层边界守卫冲突或被 `_retire_data_tables()` 误伤] → 新表属 Workflow memory，须同步登记到 `data/stores/ownership.py` 的 `WORKFLOW_MEMORY_TABLES`；这是 Phase A 已踩过的坑（漏登记即 `test_legacy_sqlite_tables_have_explicit_data_or_memory_ownership` 失败），本阶段把它列为显式任务。
- [Boss 幂等从内存迁到存储后，`tests/test_server.py` 依赖的「清空 `_RESUMED` 即可重放」手法失效，测试可能被改成伪造通过] → 测试改写方向明确为「跨进程 / 重启后可复现的幂等」，并补一条「同一幂等键第二次回调不产生第二次执行」的存储级断言，不用内部字段清空来构造前提。
- [60 秒阈值对慢速人工审批流过于严格，可能导致反复重新审查] → 阈值可配置（D8），且失效后的处理是「重新审查 + 重新批准」这一语义明确的路径，而不是放宽阈值；本阶段只保证默认值与「不得运行时放宽」两个性质。
- [`trades.client_order_id` 从 cycle+symbol+action 改为含 revision 与序号，会改变既有幂等键的形态] → 沿用 `trades` 上已有的唯一索引与 `_insert_trades` 的「先查后插 + attempt 累加」语义（`store.py:699-736`），旧行为键留在历史行中不动；`journal_entries.entry_id` 与 client order ID 的同一性必须一并复核，避免 journal 与新键脱钩。
- [图改造期间审批恢复通道可能被破坏（`Command(resume=...)` 依赖稳定的 interrupt 位置）] → 以 `tests/test_chief_graph.py::test_trader_thread_resumes_on_chief_graph` 作为回归门禁，规定每改一个节点都重跑该测试；崩溃恢复测试按 §15.2 在节点前后注入中断。
- [本阶段范围内仍存在未归因的既有失败（Phase A 记录 19 项业务性失败），可能掩盖本阶段引入的回归] → 以 Phase A 建立的判据区分环境性失败与业务回归，并对本阶段触及的测试文件做基线对照（`git worktree` 同条件对照）。

## Migration Plan

1. **存储先行**：五张表 additive 落库（`_SCHEMA` + `_migrate()` 探测式写法）、`trades` / `fills` 关联列与索引、`ownership.py` 登记、历史决策的 legacy / `legacy_unknown` 标记。此步不改任何行为，基线应无变化。
2. **哈希与领域模型**：`decision_hash` 规范化、状态枚举、追加式仓库与 compare-and-set 转换。此步仍不改行为，只新增可测单元。
3. **风控返回契约**：新增以整条修订为评估单元、覆盖跨单规则并产出执行前后指标的只读审查入口；`pre_trade()` 退化为适配层；改写 `tests/test_risk.py`、`tests/test_chief_graph.py` 中断言裁剪的用例；未接线的 `agents/risk_validator.py` 与其测试一并进入待退登记。
4. **状态机**：扩展 state、拆分持久化节点、新增 `chief_revise` 与条件边、有界轮次与终态、终态副作用单次执行。
5. **审批与执行网关**：Boss 输入收窄 + 持久化幂等键 + `runtime/server.py` 改造；执行入口改为只接受授权、加新鲜度校验、订单标识改派生式。
6. **门禁**：新路径在 paper / dry-run 默认下全部审批链测试通过；确认任一时刻只有一条可下真单入口；把本阶段待退项登记进 `config/workflow/legacy_retirement.yaml`。

**回滚策略**：schema 只增不减，因此回滚不需要数据回填——回滚动作是把新路径写入口关掉并让旧读入口继续服务；`pre_trade()` 的适配层保证风控回滚到旧行为只需把适配层设为默认路径（该开关只用于回滚，不用于生产放行）。任何情况下不得删除已写入的修订、审查、审批与事件记录。

## Open Questions

- `cycle_events` 是否在 Phase E 与 trigger ledger 合并为同一张触发台账，本阶段按「只记录决策过程内的状态转换」实现，不预置触发语义；合并与否不影响本阶段的表结构与任务拆分。
- 历史决策迁移的可映射比例需要在实施时以真实库盘点确定（脚本先只读扫描并输出分类计数），本阶段不承诺回填规模，只承诺「不可还原者标 `legacy_unknown` 且不参与授权判定」这一语义。
