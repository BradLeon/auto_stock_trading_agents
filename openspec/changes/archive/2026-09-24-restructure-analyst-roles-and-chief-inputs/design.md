## Context

目标架构见 `docs/TARGET_WORKFLOW_DATAFLOW.md` §7.2 / §9 / §10.1 / §14.4，动机见 proposal.md。本节只记录实现路径必须知道现状。

已具备的地基（Phase A–C 已落地）：

- 投影契约齐备但**未接线**：`agent/task_projection.py` 有 `build_envelope()` / `save_task_projection_envelopes()` 与七类 payload schema，`task_projection_envelopes` 表已建（唯一索引 `(agent_role, scope_kind, scope_id, content_hash)`）；当前没有任何分析角色调用它。
- 研究快照已实现但**未接线**：`decision/snapshot.py` 的 `build_research_snapshot()` / `open_decision_cycle()`（缺口即拒绝写周期）已完整，`chief_state.py:55` 有 `research_snapshot` 字段，`graph/chief.py:214-216` 传入的永远是空 dict。
- 运行契约齐备：`workflow/run_contracts.py:404-436` 注册了七类任务与依赖，`should_enter_decision_cycle()` (`:338`) 已有阻断逻辑。
- 守卫可机器校验：`workflow/architecture_guards.py` 的 `ROLE_BY_PATH_PREFIX`、`ALLOWED_CROSS_ROLE_READS`、`PROVIDER_MODULES`、`FIRST_BATCH_EXCEPTIONS`（27 条以「Phase D」为收敛阶段）。

必须处理的现状冲突（实测位置）：

| 冲突 | 位置 |
|---|---|
| Layer 直接产出配置结论 | `agents/sector/layer_review.py:213-220`、`schemas/sector.py:277` |
| Sector 读宏观报告 | `agents/sector/review.py:229-241` |
| Sector 读 PEAD dossier | `agents/sector/assemble.py:257-279` |
| Fundamental 读 Sector/Macro | `agents/pead/monitor.py:85-95`、`graph/pead.py:125-144` |
| Fundamental 内部风控与 sizing | `graph/pead.py:473-519`、`agents/pead/score.py:189-231` |
| Risk memo 注入宏观 | `agents/risk_officer/review.py:29-39` |
| Chief 直读旧表且缺失静默降级 | `agents/chief/assemble.py:90-92, 136-137, 262, 324` |

## Goals / Non-Goals

**Goals:**

- 让六类分析各自产出经 schema 校验的投影，并使跨角色读取只发生在两条显式依赖上。
- 把配置权从层级分析师完整移到行业分析师，且不丢失原有的预算与护栏语义。
- 让主理人在六类齐备时才进入决策周期，缺口可报告但必须阻断自动交易。
- 清退守卫里全部 27 条「Phase D」例外，使边界约束可执行而非纸面。

**Non-Goals:**

- 不做 Dispatcher 与并发执行改造（Phase E）。
- 不做财报 / FOMC / 宏观事件日历与 trigger ledger（Phase E）；本阶段只定义双模式的触发契约与显式入口。
- 不做影子运行与读路径 / 调度路径 / 交易路径切流（Phase F）；本阶段保留旧读入口并登记待退。
- 不改造 `sector/chain-layers`（产业链领域知识）与 `agent/action-vocabulary`；词表沿用既有单一声明。

## Decisions

### 1. 能力迁移用「REMOVED + ADDED」而非原地 MODIFIED

`sector/layer-analyst` 12 条需求全部退役，新契约落在 `agent/layer-analyst`。

理由：OpenSpec 1.13 的 MODIFIED 块整体替换 requirement 且要求保留原场景名，而本次有 4 条需求需要改名或换语义（配置结论 → 状态判断、选股 → 排序、注回 → 投影注回），原地改必然触发场景名校验失败。同时这满足既定的归域约定：角色契约在 `agent/`，`sector/` 只留领域知识。

备选：原地 MODIFIED 只改「层级配置结论」「配置结论绑定预算使用率」「护栏不变式」三条 —— 放弃，因为「同层选股」与「留痕与注回」的语义也要收窄，改到一半会留下混合形态的能力。

### 2. 配置权迁移：字段退役但列保留

`LayerVerdict.allocation` 与其派生的 `utilization_for` / `budget_for` / `budgets_for` 调用链从层级路径移出；层级改为 `layer_status: expanding|steady|contracting|unclear`。

理由：直接删列会让旧 `sector_reviews` 行与既有报表读取失败，违反 §12.4「additive migration，先保留旧读入口」。字段退役采用**墓碑式**：保留列、新写路径不再写入、消费方清零后登记待退。

映射关系（超配 100% / 标配 60% / 低配 30% / 清仓 0%）与 `risk.yaml` 的 `layer_utilization` 配置**不变**，只换执行主体。

### 3. 新角色落位与守卫角色映射调整

新建 `src/ats/agents/information/`；同时把 `ROLE_BY_PATH_PREFIX` 里 `src/ats/agents/evidence` 的映射由 `information_analyst` 改为 `evidence_observer`（非分析角色）。

理由：`agents/evidence/` 是 L1 证据观察器（observer / adjudicator / proposer），不是信息分析师；当前映射是占位，会让新角色的守卫判定指向错误的目录。不改映射则「信息分析师读文档」这条路径无法被正确校验。

备选：让信息分析师复用 `agents/evidence` —— 放弃，会把采集侧与观点侧混在同一目录，与「采集侧迁出 agents/」直接冲突。

### 4. 基本面事件模式：保留观点、剥离可执行性

保留 `direction / magnitude / 信心 / 理由 / 可证伪条件`（对齐现有 `FundamentalEventReviewPayload`）；删除 `score.py:decide()` 里按 `portfolio` / `net_liquidation` 推导数量的分支，删除 `graph/pead.py:473-519` 的 `risk_agent.assess` / `review_guardrails` / `pre_trade` 调用与 `PeadRecommendation → TradeDecision` 转换。

理由（用户裁决）：判断现实与预期的偏离是基本面职责；可执行性与风控结论只能由风控主管与主理人在审批链内产生，否则同一笔交易会有两个风控出口。

### 5. 六类必齐的判定单位：按角色而非按 task_id

`run_contracts.py` 的 `required_for_decision` 目前按 task_id 判定（七类任务，`technical_review` 为 `False`）。改为按**角色**判定：层级 / 信息 / 行业 / 基本面 / 宏观 / 技术六类，其中基本面由「例行更新或事件评审」二者之一满足。

理由：基本面两种模式在同一周期内互斥，按 task_id 判定会永远缺一类，导致决策周期进不去。技术上以「角色 → 可满足它的 task_id 集合」表达，任一命中即算满足，并把命中的 task_id 记进快照。

同时把 `technical_review` 的 `required_for_decision` 置为 `True`——六类包含技术面。

### 6. payload 扩展走 additive，schema_version 不变

`InformationBriefPayload` 增 `fact_changes` / `impact_candidates` / `entities` / `confidence` / `freshness` / `unverified`；`FundamentalEventReviewPayload` 增 `scorecard` / `guidance` / `narrative` / `confidence` / `falsifiable_conditions`。全部设为可选，`schema_version` 保持 `v1`。

理由：`_Payload` 是 `extra="forbid"` 的严格模型，加必填字段会让既有合法 payload 全部校验失败。可选新增使旧 payload 仍可通过，且新写路径能逐步填满。

### 7. 主理人读路径：新增投影读，与旧读并存，切流留 Phase F

`agents/chief/assemble.py` 新增从 `task_projection_envelopes` 读取六类投影的路径，与现有直读旧表路径**双读比对**；本阶段以旧表为准渲染上下文，投影路径负责校验齐备性与产出快照，差异记录进日志。

理由：§12.4 要求「在新写路径稳定前保留旧读入口」，且 Phase F 才是切流阶段。双读可以在不改动决策文本的前提下先验证快照与阻断逻辑。

### 8. 触发契约先行，日历留后

暴露两个显式入口（`ats analyst information`、`ats analyst fundamental --mode event`）并让现有 scheduler 的 pead 任务调用新入口；财报窗口的自动发现与 `event_id + event_version + workflow_id` 幂等触发留 Phase E。

理由（用户裁决）：避免本阶段为了触发而先造一个残缺的日历。信息分析师同样只暴露入口与触发契约（手动 / 定时 / 文档准入事件），并保证它可以不依赖其他角色独立终结。

### 9. 跨标的信号链：按载体形态设禁，不按标的范围一刀切

信号链（上下游 / 同业）是基本面最有效的领先信号来源，予以保留；但界线画在**取数入口**上而非字段语义上：经 `ats.data.*` 数据产品（`fundamentals` / `consensus` / 产业链数据包）或 `information_brief` 角色投影读取的为中性事实，经其他标的 dossier 或 `fundamental_*` 角色投影读取的为观点。

现状 `graph/pead.py:180-206` 的 `_peer_report()` 读另一个标的的 scored dossier，取 `guidance`（LLM 摘录）、`band`（LLM 逐维打分后代码分档）与 `decision_summary`（纯结论）。改造后只保留可溯源的事实字段（已报实际值、指引区间、财报日期、是否已报），定性信号（如上游产能表述）改由该标的的 `InformationBrief` 承载。

理由：文档 §7.2 把「产业链事实」列为基本面的合法输入，数据流图 :307 明确 `HIER_DATA --> FUND`；旧规划 design 决策 10 也只要求 peer read-through 以「中性共享事实或 Data Product」形态进入。按入口判的好处是它与架构守卫已有的 `PROVIDER_MODULES` / `ALLOWED_CROSS_ROLE_READS` 机制同构，不需要新增判别逻辑。

备选：① 一刀切禁止跨标的读取 —— 放弃，信号链会退化为 20 日价格动量（`price_chg_pct` 就是现在的 fallback）。② 在守卫许可表新增「fundamental → fundamental 跨标的」—— 放弃，会引入 A↔B 循环依赖与结论沿链放大。

### 10. 简报携带三类时间并做同源聚类

每条事实变化落 `event_time`（事件发生）/ `published_at`（文档对外可见）/ `extracted_at`（入库抽取）三个时间，时效与 cutoff 判定以事件时间或发布时间为准；同一事件的多份文档归入同一 `cluster_key`，记录文档数与独立信源数。

理由：现有载体只有 `published_at`（`data/news.py:84,144`、`data/admission.py:32`）。只靠入库时间会让"凌晨入库的盘后材料"被误判成次日新材料，直接破坏事件模式 cutoff 与迟到材料识别（任务 5.5）；缺聚类则同一事实被多篇同源报道重复计入，置信度被虚假放大。

备选：只补时间字段、把聚类留到后续阶段 —— 放弃，因为"独立信源数"直接影响置信度语义，晚做会让已发布的简报置信度口径前后不一致。

### 11. 层级分析师独立成包

`layer_review.py` 从 `src/ats/agents/sector/` 迁到新建的 `src/ats/agents/layer/`，守卫前缀同步改为 `src/ats/agents/layer`。

理由：不拆包时守卫只能按文件名前缀区分角色，新增 Layer 模块若落进 `agents/sector/` 会被判成 `sector_analyst`，反而获得"读 layer 投影"的许可（自读）。目录自解释比文件名约定更稳。

代价可控：src 侧引用 4 处（`sector/review.py:74,126`、`cli.py:784,812`），测试侧 5 个文件约 47 处引用，且迁移与本次职责改造同批提交。

### 12. 事件模式保留方向判断，但不携带动作与数量

保留 `direction(-1/0/1)`、幅度、信心、理由与可证伪条件；删除 `action`、`qty` / `notional` 提示与风控调用；并额外约束 payload 不得出现 action 词表取值，主理人不得把 `direction` 直接映射为交易动作。

理由（用户裁决）：判断现实与预期的偏离属于基本面职责，动作与数量的产生属于主理人综合判断 + 风控审查。`direction` 是预期差方向而非动作，两者必须分开表达，否则会出现事实上的"第二个动作出口"。

## Risks / Trade-offs

- **[双读期间口径漂移]** → 旧表读与投影读可能给出不同内容。缓解：双读差异按类别计数并进日志，任务里显式断言「同一实体同源数据的 as-of 一致」，切流前不一致即报告而非静默取一侧。
- **[27 条例外一次清退体量大]** → 采集侧迁出 `agents/` 涉及 `agents/evidence/observer.py` 八个来源。缓解：拆为「先迁采集、再改读取、最后清例外」三步，每步可独立验收；若某条确实无法在本阶段收敛，必须在同一 change 内改标为具体阶段并写明理由，不允许静默保留。
- **[层报告结构变化影响 CLI 与报表]** → 报告首节由「配置结论 + 权重」变为「状态判断 + 相对排序」。缓解：报告渲染改 additive，预算与权重章节改由行业报告承载；旧报告读取方在迁移期仍可读到历史文件。
- **[守卫角色映射改动牵连既有测试]** → `tests/test_architecture_guards.py:148` 断言当前树通过。缓解：映射调整与例外清退同批提交，例外清单只减不增。
- **[缺口阻断过早生效]** → 六类必齐会让当前大量既有运行进不去决策周期。缓解：先接线快照与阻断但保留「显式声明必需类别」的能力，默认六类；验收用完整 fixture 构造六类齐备场景。
- **[payload 可选字段被留空]** → 新写路径可能长期不填新字段。缓解：`agent/information-analyst` 与 `agent/fundamental-pead` 的校验把关键字段列为必需，缺字段即判该次产出失败。
- **[跨标的信号改走中性事实后定性信号减弱]** → 数据产品（一致预期 / 财务）不提供管理层在电话会里的产能表述这类定性内容。缓解：定性部分改由该标的的 `InformationBrief` 承载（Information→Fundamental 本就是许可读取）；上游简报未覆盖时退化为「已报财务事实 + 价格动量」，并在输出中标注该退化为证据不足，而非静默沿用旧口径。
- **[同源聚类引入判定复杂度]** → 聚类键取错会把不同事件误并或漏并。缓解：聚类键限定为「同一实体集合 + 事件时间窗 + 内容指纹」三要素，误并的代价高于漏并，故阈值取保守；簇内仍保留逐文档来源，可人工拆分。
- **[层级拆包与职责改造同批提交]** → 移动文件叠加语义改动会放大 review 面。缓解：拆包放在同组最后一项（2.10），先完成语义改造与测试转绿，再做纯移动；移动后守卫前缀与测试导入同批改。

## Migration Plan

1. **地基**：payload 扩展（additive）+ 守卫角色映射调整 + 六类判定改为按角色。
2. **层级**：`agent/layer-analyst` 产出状态判断与投影；`allocation` 字段退役登记；层级模块迁出 `agents/sector/` 独立成包（本组最后一步，纯移动）。
3. **行业**：`agent/sector-allocation` 承接配置权、预算与护栏；移除宏观与 PEAD 读取；补证据冲突展示与 schema 不兼容判定。
4. **信息**：新建 `agents/information/`，迁移 research / triage / digest，移除 dossier 直写副作用；暴露独立入口；落三类时间与同源聚类。
5. **基本面**：双模式拆分，剥离 sizing 与内部风控，移除 Sector / Macro 注入；跨标的信号链改走中性事实与信息简报。
6. **风控**：移除 memo 的宏观读取。
7. **主理人**：接线 `build_research_snapshot` + `open_decision_cycle`，新增投影双读，缺口阻断；基本面方向只作研究输入，不映射为动作。
8. **边界收敛**：采集侧迁出 `agents/`，分析师读取改经数据产品，清退 27 条例外。
9. **登记与验收**：`config/workflow/legacy_retirement.yaml` Phase D 段、文档同步、保真性对照。

**回滚**：全部 schema 变更 additive，旧读入口保留；若需回退，只需把主理人的阻断开关与投影双读关闭，旧路径立即恢复。真实下单路径不受本阶段影响（Phase B 的授权网关不变）。

## Open Questions

- 采集侧迁出 `agents/` 后的目标包位置（`data/` 采集层还是独立采集服务）在本阶段按「移入数据层取数入口」实现，具体包路径可后续统一，不影响本 change 的需求与任务拆分。
- 六类之外是否把「L1 证据观察器产出」也纳入必需类别：目标架构未把它列为决策输入，本阶段按非必需处理；若后续需要，应另开 change 增补而非在本 change 内改动快照定义。
