## Context（背景）

动机和范围见 `proposal.md`。权威目标文档是 `docs/TARGET_WORKFLOW_DATAFLOW.md`；本设计将其中 Phase A–F 的架构落到适合当前代码库的实现形态。

当前系统已经具备以下基础：

- Chief 和 PEAD 已使用 LangGraph，包括基于 interrupt 的 Boss 审批路径和可选 SQLite checkpoint。
- `src/ats/risk/` 已具备确定性的组合风险和边际风险计算。
- 已有 IBKR 下单、稳定券商引用、成交对账、绩效、journal 和 attribution 组件。
- 已有受治理的 Data Platform，包含持久化结构化/非结构化存储、Data Products、运行时行情访问、准入、隔离和血缘。
- 已有 Macro、Technical、Sector/Layer、PEAD、Chief 和 Risk Officer 实现。

### 数据层历史核查

`data_frame_rebuild` 对应的合并提交和归档 change 已经完成数据平台主体、统一目录/facade、数据迁移与对账，以及大量消费者的读侧切换。其设计先把“兼容 facade 完成”和“旧实现退役”拆成独立里程碑，后续任务又把 Evidence/Chain 消费方切换及 legacy retirement 标为完成。因此从该 change 的交付状态看，改造确实被声明为完成；但合并代码仍存在以下反证：

- `PlatformUnstructuredRepository` 的类说明明确为只读；writer schema 只覆盖来源、ingestion run 和 newsletter cursor，没有 `data_evidence_*` 写入 schema。
- `TradingMemory._retire_data_tables()` 会删除 `evidence_observations`、`evidence_facts`、`evidence_fact_projections` 和 `evidence_failures`。
- 同一版本中的 `TradingMemory.save_observation()`、`save_observation_failure()` 及同族读取仍直接访问上述旧表。
- 因此“数据平台架构已经建立”与“所有旧写路径已经收口并退役”不是同一结论；这里确实存在一条漏过原验收的写侧 cutover 遗留。它属于已完成 change 的实施/验收缺口，而不是要推翻或重做整个数据平台架构。

当前工作区已经有未提交的后续改动：Data Platform 增加了 `data_evidence_*` 建表和写入方法，`TradingMemory` 的多条证据方法也已改为委托 Data Platform。它们可以作为本 change 的既有实现候选，但不能仅凭代码存在即认定完成；repository 顶部仍保留“只读、writer 仍走 legacy”的过期声明。2026-09-22 的针对性测试结果为 5 passed / 2 failed；两项失败均发生在旧数据库迁移路径，因为 `TradingMemory.__init__` 在 `_migrate()` 之后才初始化 `_data`，而迁移过程已通过委托方法访问 `data_store()`。

因此，本 change 的 Phase A 采用“核验、接收、补齐”的方式：满足本 change specs 且测试通过的现有成果直接复用；只修复剩余缺口和边界不一致，不重新建设整个 Data Platform，也不依赖其他 OpenSpec change 先完成。

塑造实现方式的其他约束如下：

- 已确认的分析师依赖图只允许 Layer → Sector 和 Information → Fundamental；Chief 是唯一可以跨分析师汇总的角色。
- Workflow 观点必须留在共享事实层之外。
- 当前代码库使用 SQLite，`TradingMemory` 连接按进程缓存；现有 scheduler 为绕开这一限制而刻意串行运行。
- 当前 Chief 执行路径为 `chief_decide → risk_gate → persist_decision → boss_review → trader`；Risk 会在持久化前裁剪/过滤决策，Boss 可以返回修改后的决策。
- 现有 `cycles`、`decisions` 和组合 `risk_reviews` 表无法表达不可变 decision revision 或审批绑定。
- Agent 输出存储与数据存储是两个有意区分的所有权域。
- 现有领域表和 CLI 仍有消费者，不能在一次部署中全部删除。

本 change 自包含，不把其他 OpenSpec change 作为实施依赖，也不要求先应用其他 change。实施时如果代码库已经存在兼容类型、工具或部分实现，应先验证其满足本 change 的 specs，再决定复用或补齐。

## Goals / Non-Goals（目标与非目标）

**目标：**

- 通过带门禁的 Phase A–F 迁移交付完整 Target 架构。
- 使工作流依赖、投影新鲜度、幂等性和完整性可被机器验证。
- 保留并复用现有风险计算、券商集成、journal、PEAD 证据控制和受治理数据存储。
- 建立从研究输入到决策 revision、审批、订单、成交和绩效的持久审计链。
- 支持单分析师独立运行、依赖子流程、全量并行分析、定时任务和事件触发任务。
- 允许在读取、调度和交易切换边界回滚，但不恢复已经退役的数据所有权。
- 对已有数据层实现执行验收式接收：已经完成且通过契约的部分不重做，遗留写路径、迁移错误和旁路访问必须收口。

**非目标：**

- 本 change 不把 SQLite 替换为网络数据库。
- 没有失败的目标需求时，不重写确定性风险模型、IBKR 传输或现有绩效公式。
- 不把每个分析师工作流都改造成 LangGraph。
- 不允许 Information、Layer、Sector、Fundamental、Macro 或 Technical Analyst 提交订单。
- 不把 LLM 作为风险通过/拒绝、审批、券商事实、PnL 或归因的权威。
- 首期日历不扩展到财报、FOMC、CPI、PCE、非农、GDP 和人工事件之外。
- 在消费方清零、对账、回滚和墓碑条件满足前，不删除 legacy 数据或入口。
- 不重新实现已经通过本 change 验收标准的数据平台能力。

## Decisions（设计决策）

### 1. 一个 change，六个带门禁的交付阶段

实施 SHALL 将 Phase A–F 保持在同一 change 和同一任务图中，但每个阶段都有明确质量门禁和回滚点。后续阶段代码可以放在默认关闭的开关之后提前开发，但其前序不变量满足前，不得成为生产活动路径。

这样既保持目标一致，又避免一次性切换。拒绝不分阶段的整体实施，因为审计 schema、角色边界、scheduler 并发和实盘执行会同时变化，无法可靠隔离故障。

Phase A 的门禁按行为和证据判断，不按“代码是否来自哪个 change”判断。已经存在的实现通过相同测试即可被接收；未完成或失败的部分继续修复。

### 2. 普通工作流使用 Dispatcher，只有有状态图使用 LangGraph

新增工作流运行时，公开契约如下：

```text
WorkflowTaskSpec
  workflow_id
  agent_role
  dependencies
  trigger_modes
  input_contract
  output_schema
  freshness_policy
  timeout
  retry_policy
  resource_group

TriggerContext
  trigger_id
  trigger_type          # manual | schedule | event
  requested_at
  actor
  schedule_or_event_ref
  idempotency_key

WorkflowRunRequest
  run_id
  trigger_context
  requested_workflows
  scope
  as_of
  enter_decision_cycle

WorkflowRunResult
  run_id
  task_results
  projection_refs
  missing_requirements
  terminal_status
```

Dispatcher 将任务注册表验证为 DAG，只展开已声明依赖，并把 ready 节点调度到有界资源组。Phase 0 的 Layer、Information 与独立的 Macro、Technical 可以并发开始；Sector 只等待 Layer，Fundamental 只等待 Information。除非 `enter_decision_cycle` 为 true，纯分析运行在投影发布后结束。

LangGraph 保留给：

- PEAD 事件运行：需要管理事件前状态、初始财报、延迟电话会/指引升级和恢复。
- Chief—Risk—Boss—Trader：需要有界修订循环、人工 interrupt 和崩溃恢复。

拒绝用一个大型 LangGraph 包含所有分析师，因为这会耦合本可独立的调度、使局部运行复杂化，并把普通重试变成图状态迁移。

### 3. 调度前统一归一所有触发器

手动命令、cron schedule 和日历事件在创建 workflow run 前统一转换为 `TriggerContext`。稳定逻辑键如下：

- Manual：调用方提供幂等键；若未提供，则系统生成并返回，调用方重试必须复用。
- Schedule：schedule ID + 计划触发时间 + workflow ID。
- Event：event ID + event version + workflow ID。

`trigger_runs` 负责唯一性并记录 `claimed`、`dispatched`、`completed`、`skipped` 或 `failed`。它是重启与 misfire 账本；APScheduler 或未来 scheduler 只负责唤醒。

拒绝把 scheduler 内存任务状态作为去重权威，因为它无法跨进程重启或多 worker 存活。

### 4. 使用新的不可变 projection envelope 存储 Agent 输出

新建 `task_projection_envelopes`，不复用现有 `task_projections`。旧表表示不同的证据投影抽象，键语义不兼容。新表保存：

```text
projection_id PK
workflow_run_id FK
agent_run_id FK
agent_role
scope_json
as_of
valid_until
schema_name
schema_version
input_refs_json
data_vintage_refs_json
model_version
prompt_version
payload_json
content_hash
status
created_at
supersedes_projection_id NULL
```

`projection_id` 强制唯一；规范化内容身份另设唯一约束，避免重试产生重复。已发布记录不可变，更新必须创建新行并引用被替代投影。

发布前按角色校验 payload：

- `LayerAnalysis`
- `InformationBrief`
- `SectorAllocation`
- `FundamentalExpectationUpdate`
- `FundamentalEventReview`
- `MacroReview`
- `TechnicalReview`

迁移期保留 `pead_dossier`、`sector_reviews`、`macro_reviews` 和 `technical_reviews` 作为兼容读模型。adapter 发布新 envelope，仅在仍有活动消费方时更新旧读模型；所有兼容写入必须登记退役条件。

拒绝单一无类型 JSON 报告表，因为下游的新鲜度、schema 兼容、血缘和依赖验证会变得不确定。

### 5. 将投影复用作为确定性策略判断

投影选择依次检查：

1. `agent_role` 精确匹配；
2. scope 兼容；
3. 输出 schema 兼容；
4. `as_of` / `valid_until` 新鲜度；
5. 必需输入投影身份；
6. 必需 data vintage 身份；
7. 终态。

selector 要么返回投影及结构化 `reused` 原因，要么返回结构化失效原因；不得静默回退到最新一行。Dispatcher 在 `agent_runs` 中记录复用判断。

### 6. Chief 决策前固定研究快照

在 `decision_cycles` 中增加不可变 `research_snapshot` payload，包含必需角色集合，以及每个被选投影的 ID、content hash、as-of 和新鲜度判断。只有所有必需投影均有效时，完整性门禁才创建 cycle。

Chief—Risk 修订循环期间快照保持不变。如果重要输入失效，cycle 进入 `superseded` 或 `manual_review`；新 cycle 才可使用新研究快照。这样可避免比较来源于不同研究状态的 revision。

### 7. 通过角色专用组装 API 和架构测试共同强制角色隔离

每个分析师使用角色专用 input assembler，不直接访问通用 Memory store：

- Layer：只接收层级、证据、截面 Data Products。
- Information：只接收已准入文档、chunk 和中性事实。
- Sector：共享行业 Data Products + `LayerAnalysis`。
- Fundamental：公司/Consensus/产业链 Data Products + `InformationBrief`。
- Macro：宏观、政策、地缘政治 Data Products。
- Technical：Runtime Data Gateway 的行情/期权输入。
- Risk：proposal + 风险规则 + 组合/市场快照。
- Chief：全部被请求分析师投影 + Internal State。

静态架构测试扫描禁止 import 和角色投影读取；运行时 assembler 同时验证 input reference。拒绝只做静态检查，因为动态 store 访问可能绕过 import；也拒绝只做运行时检查，因为发现违规会太晚。

### 8. 将 Layer 研究与 Sector 配置拆分

把当前 `agents/sector/layer_review.py` 及相关截面/证据逻辑重构为 Layer workflow。它发布层级结构、层内共同条件、逐标的比较、证据强度、冲突和可证伪触发器，但不得包含配置枚举、budget use、stance 或 target weight。

Sector 消费有效 Layer 输出和共享行业事实，并独占行业/层级/标的配置。Sector assembler 移除现有 Macro、PEAD、研究观点和直接跨 Agent 读取。legacy Layer 配置字段只允许迁移 reader 接收，不得注入新 Sector 运行。

拒绝保留 Layer 配置再让 Sector 覆盖，因为同一决策会有两个 owner，责任不可追踪。

### 9. 将 Information 建成独立工作流，而不是 PEAD 子程序

新建 Information package 和角色 schema。复用 PEAD research、triage、monitor 和 digest 中纯粹的抽取/重要性逻辑，但移除直接修改 dossier 和直接获取 Provider 的行为。输入为已准入 document/version/chunk 引用；输出将事实变化与推断性影响候选分离，并保留来源、时间和置信度元数据。

Information 可以手动、周期性或在文档准入时运行。它不得输出 action、target weight 或 order。Fundamental 是其消费方，但 Information 可以不运行 Fundamental 而独立完成。

拒绝继续把 Information 嵌在 PEAD 内，因为这会阻止独立调度，并把共享研究能力变成公司事件内部状态。

### 10. 将 Fundamental 拆为例行和事件模式

例行模式加载上一份有效预期基线、新 Information 投影、Consensus/公司 Data Products 和中性产业链信号。它把新证据分类为 confirming、contradicting、additive 或 unresolved，并发布 `FundamentalExpectationUpdate`。

事件模式冻结截止时间前最后一份有效基线，校验实体、报告期和文档完整性，将 actuals 与 baseline/Consensus/implied expectations 比较，发布包含 Surprise Scorecard、指引评估、叙事变化和不可执行投资建议的 `FundamentalEventReview`。迟到电话会或完整指引到达时，使用同一冻结基线发布新版本。

Fundamental input assembler 移除 Sector/Macro 观点和其他 Fundamental 结论。Peer read-through 必须表示为中性共享事实或 Data Product。Fundamental 移除最终组合风控；只有 Chief 之后的 Risk 生命周期能批准执行。

### 11. 将确定性风控 verdict 与叙事解释分离

把当前 `pre_trade` 行为拆成两层：

1. 纯 review 操作：针对固定 ruleset、组合快照、市场快照和事件数据评估不可变 proposal，返回前后风险向量、违规、verdict 和允许边界，不修改 proposal。
2. 可选 Risk Agent 解释：消费确定性结果，可以组织冲突说明或 counterproposal，但不能改变 verdict 或指标。

现有 `_apply_order_caps` 和 `_clip_event_notional` 改成 proposal 验证 finding/counterproposal builder。任何被接受的数量修改都必须由 Chief 创建新 revision。

拒绝保留静默裁剪，因为持久化和审批对象会在没有 revision 边界的情况下偏离 Chief 原提案。

### 12. 将决策生命周期持久化为领域记录

新增以下表，不替换现有组合 `risk_reviews`：

```text
decision_cycles
  cycle_id PK
  trigger_run_id
  research_snapshot_json
  required_roles_json
  status
  current_revision_no
  final_outcome
  created_at / updated_at

decision_revisions
  cycle_id + revision_no PK
  parent_revision_no NULL
  proposal_json
  rationale
  input_refs_json
  model_version / prompt_version
  decision_hash UNIQUE
  created_at

decision_risk_reviews
  risk_review_id PK
  cycle_id / revision_no / decision_hash
  ruleset_version
  portfolio_snapshot_id
  market_as_of
  verdict
  violations_json
  boundaries_json
  before_metrics_json / after_metrics_json
  created_at

boss_approvals
  approval_id PK
  cycle_id / revision_no / decision_hash
  risk_review_id
  decision                 # approved | rejected
  actor
  note
  idempotency_key UNIQUE
  created_at

cycle_events
  event_id PK
  cycle_id
  event_type
  actor_type / actor_id
  idempotency_key UNIQUE
  payload_json
  created_at
```

`orders` 和 `fills` 增加可空审计关联列以兼容 legacy。新系统订单必须具有非空 cycle/revision/hash/approval 关联。只有来源字段能证明关系时才回填历史记录，否则标记为 `legacy_unknown`。

拒绝原地复用 `cycles`、`decisions` 或组合 `risk_reviews`，因为其覆盖/键语义不能保留不可变 revision，也会把组合监控与交易审批混在一起。

### 13. 将审批图实现为有界状态机

Chief graph 改为：

```text
assemble_context
  → chief_decide
  → persist_revision
  → risk_review
      ├─ reject → persist_risk_review → chief_revise → persist_revision → risk_review
      ├─ approve → persist_risk_review → boss_review
      └─ exhausted → manual_review → END
  → execution_freshness_check
      ├─ stale → risk_review
      └─ fresh → trader
  → terminal_persist
```

自动风控修订默认最多三轮。Boss 只能批准或拒绝当前已通过 Risk review 的精确 decision hash。修改意见作为拒绝 note 保存，Chief 必须创建新 revision。

`ExecutionAuthorization` 绑定 cycle、revision、decision hash、risk review、Boss approval、ruleset version、portfolio snapshot 和 market as-of。默认新鲜度阈值为 60 秒，只能在 risk policy 中配置。过期授权返回 Risk，并要求新的 Boss approval。

LangGraph checkpointer 只承担运行恢复。每个有领域副作用的节点先使用稳定幂等键查询领域 store，使 checkpoint 重放不能重复创建 revision、approval、终态事件或订单。

### 14. 从授权派生稳定券商订单身份

client-order identity 为 `cycle_id + revision_no + order_sequence`，券商 `orderRef` 保存紧凑、稳定的编码形式。不确定提交重试前，Trader 按 client/order reference 和 `perm_id` 查询本地尝试记录及券商状态。

扩展而非替换现有 IBKR reference 与 reconciliation 代码。Trader 只接受授权 revision 中已规范化的订单，审批后不得引入新 symbol、quantity、price、order type 或 direction。

### 15. 让书记员成为现有账本组件之上的确定性编排器

新建 Clerk service：

- 消费 cycle event、revision、Risk review、Boss approval、order attempt、券商 order/fill、mark 和 fee；
- 把券商活动分类为 system、manual 或 unattributed；
- 使用券商稳定标识幂等重放对账；
- 将 episode、performance 和 attribution 重建为派生读模型；
- 向 Chief/Risk 发布带 as-of 的 Internal State；
- 将关联不完整和错过对账窗口显式暴露为异常。

现有 journal/reconcile/performance/marks/predictions 模块保留为计算组件。可选 critic 输出以 commentary 保存，并链接不可变账本事实。

拒绝使用 LLM Clerk 作为账本权威，因为会计事实、重放和 exactly-once 归因需要确定性身份和公式。

### 16. 事件发现归 Data Platform，触发归 Workflow runtime

在数据层创建持久化 `schedule_events`：

```text
event_id
event_version
event_type
entity_or_symbol
scheduled_at
timezone
session
status
source_id / source_lineage
announced_at / fetched_at / revised_at
payload_hash
```

refresh controller 采集并对账财报、FOMC、CPI、PCE、非农和 GDP 日历。`config/events.yaml` 变成人工覆盖/自定义事件 overlay。人工覆盖创建可追踪的新 event version，不改写来源历史。

Workflow runtime 读取已发布日历事件，并在 `trigger_runs` 中认领 `event_id + event_version + workflow_id`。事件修订使尚未执行的旧 trigger 失效；已经完成的旧 trigger 保留为历史。每个 workflow 明确时区、session 和 misfire 策略。日历 payload 只含元数据，不含研究结论。

拒绝继续以人工编辑 YAML 作为主日历，因为它不能发现修订，也无法提供持久幂等性和血缘。

### 17. SQLite 使用每操作连接、WAL 和短事务

本 change 保留 SQLite，但移除对单个进程缓存连接的正确性依赖。repository 每次操作/工作单元获取连接，设置 WAL 和 busy timeout，并把远程/LLM 调用放在事务之外。唯一约束和 insert-or-return-existing 操作强制幂等。

resource group 限制会写入同一逻辑 aggregate 的并发工作。decision-cycle 转移对当前 status/revision 使用 compare-and-set，避免两个 worker 同时推进同一 cycle。

拒绝立即迁移 PostgreSQL，因为现有部署约束下即可实现目标行为，数据库迁移反而会掩盖 workflow 正确性。如果未来 SQLite 并发不足，当前 schema 和 repository 边界仍保持可迁移性。

### 18. 使用 additive migration 和显式退役墓碑

所有新表/列都采用 additive 方式。legacy 表保持可读，直到：

1. 所有 writer 已迁移；
2. 所有 consumer 已迁移或使用兼容 view；
3. 历史对账完成；
4. 回滚窗口结束；
5. 退役登记记录 consumer-zero 和证据。

命中已退役标识时 fail closed。物理清除默认 dry-run，需要显式确认，记录 actor/scope/note，并在适用时保留可恢复导出。已经归 Data Platform 所有的数据表不得以兼容为由恢复到 Workflow Memory。

数据层收口的验收以“唯一 writer、所有调用路径、旧库迁移、读回血缘、幂等和失败语义”组成。当前未提交实现只有全部通过这些条件后，才可标记为已完成；不能以“新 repository 已经有写方法”替代验收。

### 19. 影子执行可以比较决策，但不能实盘下单

Phase F 为 projection read、Dispatcher scheduling、approval lifecycle、Clerk publication 和 live Trader routing 分别设置 feature switch。影子运行可以经过 Risk 并生成合成执行结果，但所有门禁通过前，新路径没有券商写权限。

切流顺序：

1. projection/read path；
2. 分析师角色输出；
3. Dispatcher/calendar 调度；
4. paper/shadow 模式审批生命周期；
5. Clerk/Internal State 读取；
6. 单一 live Trader 路径。

新旧实盘订单开关互斥。回滚必须先关闭新 live route，再启用旧 route；同一配置下两者绝不能同时活动。

## Risks / Trade-offs（风险与取舍）

- **[变更面很大]** 六个阶段覆盖多数 runtime 领域。→ 每阶段独立门禁，后续路径默认关闭，激活前必须通过阶段专项测试。
- **[现有基线非全绿]** 已知失败可能掩盖新回归。→ Phase A 固定标准环境、聚类现有失败，并在行为切换前清除系统性数据写入/action/资金桶缺陷。
- **[把架构完成误当成 cutover 完成]** `data_frame_rebuild` 已完成平台主体，但合并版本明确保留部分 legacy 写路径；当前后续修复也仍有迁移测试失败。→ Phase A 对每条 writer、consumer 和旧库迁移路径逐项验收，按测试接收既有实现，不按 change 名称或代码存在判断完成度。
- **[SQLite writer 争用]** 并行分析师和 append-only 审计事件会增加锁争用。→ 使用 WAL、每操作连接、短事务、有界 writer resource group、busy timeout 和基于唯一键的重试；记录未来数据库迁移阈值。
- **[崩溃后重复副作用]** graph 或 scheduler 重放可能重复审批或券商提交。→ 所有领域写入和外部提交使用稳定键幂等；重试不确定券商结果前先对账。
- **[兼容双写漂移]** 新 envelope 与旧领域表可能在迁移期不一致。→ 新路径以 envelope/audit store 为规范事实，通过单一 adapter 生成兼容读取并比较输出，所有兼容 writer 都登记退役。
- **[角色重构改变投资输出]** 从 Sector 移除 Macro/PEAD 输入、把配置权移出 Layer，可能实质改变建议。→ 在相同 data vintage 上影子运行新旧报告，比较原因级差异，消费方切换前人工审阅。
- **[日历来源修订]** 外部事件来源可能冲突或临时改期。→ 保留来源血缘和版本历史，显式记录人工覆盖，只使未执行 trigger 失效，暴露冲突而不静默选结论。
- **[Boss 审批延迟]** 60 秒默认新鲜度可能频繁要求重新审批。→ 阈值在 risk policy 中可配置，明显展示过期时间，在提交审批前刷新快照，绝不静默延长。
- **[历史审计缺口]** 旧订单可能缺少足够字段来重建 revision hash。→ 标记 `legacy_unknown`，保留原记录，不把无法验证的关系计入端到端可追溯声明。
- **[券商历史限制]** 日内执行 API 可能无法恢复错过的历史成交。→ 持久调度对账，对错过窗口告警，在可用时支持替代历史 statement 导入，并持续暴露未解决缺口。

## Migration Plan（迁移计划）

### Phase A——基线、数据边界与契约

1. 记录标准 `uv sync --all-extras` / `uv run pytest` 测量并聚类失败。
2. 盘点当前提交和工作区中的中性证据写入实现，先运行写入、读回、旧库迁移和四条调用路径测试；满足契约者直接接收。
3. 补齐剩余 Data Platform writer、委托初始化顺序、migration/rollback 和旁路清理，不恢复已退役的 Workflow Memory 数据表。
4. 收敛五值 action 词表和显式券商映射。
5. 修正 option-survival 资金桶边界及对账不变量。
6. 新增 workflow/projection/trigger schema、架构测试、每操作 SQLite 基础设施和退役登记。
7. 门禁：Phase A 系统性失败解决；现有/新增数据层实现通过唯一 writer、旧库迁移、读回血缘和幂等测试；新契约与架构守卫通过；其余无关失败登记 owner。

回滚：证据 writer 只能切回最后一个有效的 Data Platform writer adapter/version，不得重建已退役的 Workflow Memory 数据表。契约表为 additive，可保持未使用状态。

### Phase B——决策审计与执行安全

1. 新增 decision-cycle 审计 schema 和 repository。
2. 实现不可变 revision 和纯确定性 Risk review。
3. 新增 Chief 修订循环、有界轮次、领域幂等和正式终态。
4. 将 Boss 限制为批准/拒绝精确 hash，并增加过期授权复审。
5. 在 paper 模式强制 Trader 授权和稳定订单 ID。
6. 门禁：崩溃/重放、过期/重复审批、No Action、轮次用尽和券商幂等测试通过。

回滚：关闭新 decision 入口并保留审计记录；不执行破坏性 schema 回滚。

### Phase C——书记员与账本

1. 在可证明时将 order/fill 关联到 cycle/revision/approval。
2. 在 reconcile/journal/performance/attribution 之上构建确定性 Clerk 编排。
3. 增加重放、迟到/部分成交、manual/unattributed order 和错过窗口处理。
4. 发布带 as-of 和完整性标记的 Internal State。
5. 门禁：账本重建和对账幂等，每个新系统订单都有完整审批关联。

回滚：停止 Clerk 发布，让消费方回到现有读模型；保留原始/审计记录。

### Phase D——分析师角色

1. 发布不含配置字段的 `LayerAnalysis`，将 `SectorAllocation` 建为唯一配置输出。
2. 从可复用纯抽取组件构建独立 Information Analyst。
3. 将 Fundamental 拆为例行/事件模式并冻结事件基线。
4. 从 Sector、Fundamental 和 Risk 移除禁止的分析师观点依赖。
5. 将 Chief assembler 扩展到全部六类角色投影和固定研究快照。
6. 门禁：架构守卫通过；角色 schema 拒绝越界字段/输入；新旧输出比较已审阅。

回滚：让 projection consumer 切回旧读模型；不得在新 workflow 中重新启用禁止依赖。

### Phase E——Dispatcher 与日历

1. 登记 workflow 和声明式依赖 DAG。
2. 启用有界异步 dispatch、投影复用、完整性门禁和触发幂等。
3. 发布版本化 Schedule Calendar 并对账人工 overlay。
4. 增量迁移现有定时任务；已迁移 workflow 对应的旧 scheduler 保持关闭。
5. 门禁：并发、失败隔离、重启、misfire、事件修订和重复触发测试通过。

回滚：停止受影响 workflow ID 的 Dispatcher claim；确认没有在途 trigger 持有它们后，才能重新启用对应旧任务。

### Phase F——影子运行与切流

1. 在相同快照上运行新旧研究和调度路径。
2. 在关闭券商写入时运行新审批/Clerk 路径。
3. 比较投影、漏任务、风险结果、审计关联和绩效归因。
4. 按顺序切换 read、schedule 和 trade 开关；验证单一 live path 不变量。
5. 演练回滚，完成 consumer-zero 检查，并把满足条件的 legacy path 标记为 retired。

回滚：先关闭新 live Trader，确认无在途授权，再恢复旧 live route。保留全部审计和影子记录。
