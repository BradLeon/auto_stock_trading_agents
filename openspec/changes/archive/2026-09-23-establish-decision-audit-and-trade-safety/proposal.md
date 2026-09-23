## Why

`docs/TARGET_WORKFLOW_DATAFLOW.md` §14.2 把阶段 B 定为「决策审计与交易安全」：新增 decision cycle / revision / risk review / Boss approval / cycle event 模型，实现 Chief—Risk 多轮 Loop 并移除 Risk 静默裁剪，把 Boss 入口限定为绑定 revision hash 的批准/拒绝，在 Trader 入口强制校验 `ExecutionAuthorization` 与新鲜度，并让手动与自动交易指令进入同一审批链。现在做这一阶段，原因有四条，全部可在当前仓库定位。

**第一，批准的对象和执行的对象不是同一个东西。** 当前 `src/ats/graph/chief.py` 是单次通过图（`assemble_context → chief_decide → risk_gate → persist_decision → boss_review → trader`，`chief.py:231-250`）。`risk_gate`（`chief.py:78-115`）先做 Hold 过滤（`chief.py:82`）与 PEAD 隔夜限价改写（`chief.py:102-109`），再交给 `risk/checks.py::pre_trade()`，最终把**已被改写过的** `state.decisions` 交给 Boss 与 Trader。落库时 `persist_decision`（`chief.py:118-158`）写 `cycles` + `decisions` 两张表（`store.py:1026-1033`），而 `decisions` 表没有主键、没有 revision 序号、没有内容 hash（`store.py:28-31`），`cycles` 也没有风险审查与审批的关联列（`store.py:25-27`）。因此「Boss 批准的是哪一份提案、Trader 执行的是不是同一份」在当前 schema 下**无法回答**，§5.2 第 7 条禁止的「批准的是 A、执行的是 B」没有被结构性阻止。

**第二，风控在静默修改主理人的订单，而这正是 §5.2 第 3 条、§10.2 第 4 条明令禁止的行为。** `risk/checks.py` 的 `_apply_order_caps()`（`92-117`）会把超限订单的名义金额改写为上限、由 `target_weight` 反推名义金额、并为无名义金额的卖单补全；`_clip_event_notional()`（`120-141`）按事件损失预算改写 `notional_usd`；`pre_trade()` 主循环在 `verdict.allowed=False` 时直接 `continue` 丢弃该决策（`80-83`）。风控的返回值是 `(approved_decisions, notes, initial_review)`（`checks.py:21`）——即**裁剪后的决策本身**，被审的原提案不进入任何持久记录。此外 `src/ats/agents/risk_validator.py::apply_guardrails()` 仍保留 drop / clip / cap weight / inject forced trim / scale sector / scale book 一整套静默改写（`22-109`），虽已无 `src/` 引用但仍有独立测试在断言其裁剪行为（`tests/test_risk_validator.py:28-32`）。结果是：事后复盘无法区分「投资判断改变」与「订单仅因风险预算被压缩」，而这正是 §5.3 要求结构化驳回格式的原因。

**第三，Boss 审批的去重活不过一次进程重启。** `src/ats/runtime/server.py` 用进程内字典加锁去重（`_RESUMED: dict[str, str] = {}`，`server.py:24`；`_resume_once`，`27-41`），注释也自述这是进程内实现。§10.3 明确要求「重复回调使用 approval idempotency key 返回原结果」「不得依赖进程内集合去重」。同时 Boss 的输入面是 `BossApproval`（含 `effective_decisions()` 这类可改写决策的通道，`chief.py:182`），并未绑定到某个被风控批准的 hash。

**第四，Trader 入口没有授权对象、也没有时间概念。** `trader/execute.py::execute()` 接收裸 `list[TradeDecision]`（`execute.py:33-35`），全仓库不存在 `ExecutionAuthorization`。`client_order_id` 由 `cycle_id + symbol + action` 派生（`store.py:690-697`），不含 revision 与订单序号，因此同一 cycle 内跨 revision 的同标的同向订单会撞在同一个幂等键上；`order_ref()` 同理（`broker/ibkr.py:373-379`）。组合快照（`chief.py:90` → `trader/portfolio.py:19-25`）与行情（`execute.py:239-253`）都不带 age，§10.4 要求的「默认超过 60 秒即失效，需重新风控并重新人工批准」没有任何代码路径。

**为什么是现在。** Phase A 已交付三件本阶段直接依赖的东西：`agent/task-projection`（带 `input_refs` / `data_vintage_refs` / `content_hash` / `is_expired()` 的 envelope）、`workflow/run-contracts`（带 `freshness_seconds` / `timeout_seconds` 与 `TriggerContext.idempotency_key()` 的运行与触发契约）、`workflow/legacy-retirement`（墓碑登记 + fail-closed 读取门 + 两段式清除），并有可重复的绿色基线与测量纪律。Phase C 的 Clerk 要以 cycle / revision / approval 作为订单与成交的强关联外键，Phase D 要让 Chief 读取六类分析投影并固定 research snapshot，Phase F 的影子运行要以「同一 revision 的风控结论 + Boss 批准」作为对照基准——三者都以本阶段的不变量为前提。若本阶段不先把**不可变 revision**与**唯一的执行授权网关**立起来，C、D、F 都会建立在可变决策之上并二次返工。

本阶段不改动任何风控硬规则数值、不改动任何分析结论、不改变 LangGraph 只用于 PEAD 事件流与 Chief 审批链的边界（§16 第 10 条）。

## What Changes

- **新增决策审计存储（additive）**：`decision_cycles`、`decision_revisions`、`decision_risk_reviews`、`boss_approvals`、`cycle_events` 五张表（§12.3）。全部走 additive migration，不删旧表旧列（§12.4）；现有按 `as_of` 主键的组合快照表 `risk_reviews`（`store.py:77-79`）**保留原语义**，不与新的 `decision_risk_reviews` 混用（§12.3）。为 `trades` 与 `fills` 增加可空的 `cycle_id` / `revision_no` / `decision_hash` / `approval_id` 关联列与索引——文档 §12.3 写的是「现有 `orders` 和 `fills`」，本仓库没有 `orders` 表，等价物是承担订单职责的 `trades`（`store.py:32-35` + 迁移列 `store.py:483-504`），本 change 按实际表名落地并记录该差异。
- **决策内容不可变且可校验**：`decision_revisions` 每行承载一次完整提案，含 `parent_revision`、修订原因、Chief 理由、输入引用、模型与 prompt 版本以及规范化 `decision_hash`；旧 revision 不被覆盖（§10.2 第 1、2 条）。`decision_hash` 的规范化规则与 Phase A 的 `content_hash()` 同构——对规范化后的指令、理由与关键输入引用取摘要，不随无关序列化差异变化。
- **研究快照必须完整才允许开 cycle**：Chief 创建 cycle 时固定 `research_snapshot`，逐项记录被要求角色的 projection ID、content hash、as-of 与新鲜度（直接复用 `TaskProjectionEnvelope` 与 `reuse_decision()` 的理由枚举）；缺失或过期时**不创建 cycle 并阻断自动交易**（§10.1、§16 第 7 条）。快照本身失效时 cycle 转 `superseded`，不在原 revision 内偷换研究输入。
- **Chief—Risk 多轮 Loop 有界且有终态**：按 §5.4 的演进建议改造现有图，而非另起一套——扩展 `ChiefDecisionState`（`graph/chief_state.py:19-53`）加入 revision 序号/hash、risk round、research snapshot、portfolio snapshot、max rounds、authorization、cycle status；把 `persist_decision` 拆为「按轮持久化不可变 revision」与「持久化 risk review」；新增 `chief_revise` 与 `risk_gate` 后的条件边：approve → Boss、reject → `chief_revise`、轮次用尽 → `manual_review`。默认上限 3 轮（§5.2 第 6 条、§16 第 8 条）。No Action 是带理由的正式终态，不进入 Risk / Boss / Trader（§15.2）。一次性副作用（消费 PEAD 信号、生成终态报告）只在进入终态时执行，不每轮重复。
- **移除 Risk 静默裁剪（**BREAKING**：改变现有返回值契约）**：确定性风险引擎只输出判定与前后指标，Risk 返回结构化 `verdict` / `violations` / `allowed_boundary`（§5.3 的 JSON 形状），任何数量裁剪、标的替代或行为调整都以**结构化 counterproposal** 返回 Chief，由 Chief 生成新 revision 后重新风控；被审 revision 的订单数量在任何路径下不得被就地改写（§5.2 第 3 条、§10.2 第 4 条）。审查以**整条修订**为评估单元，覆盖逐单限额、行业层与相关簇集中度以及依赖组合状态方可判定的规则（§3 的风控规则包），不得按单笔独立判定后逐笔放行；每次审查的结果记录须含被审修订执行前后的风险指标（§5.3、§7.3 的 `DecisionRiskReview`、§10.2 第 3 条、§12.3），使复盘能区分「投资判断改变」与「订单仅因风险预算被压缩」。LLM 文字解释不得覆盖硬规则 verdict（§5.2 第 4 条）。`pre_trade()` 现有的裁剪式返回值被替换为不修改提案的审查结果，因此依赖旧契约的调用点与测试同步改写。
- **Boss 入口收窄为绑定 hash 的批准/拒绝，并用持久化幂等键去重**：审批对象绑定已通过风控的 exact `decision_hash`，界面只提供批准与拒绝；Boss 的修改意见以拒绝备注保存，由 Chief 创建新 revision 后重新风控，不得产生可直接执行的新订单（§10.3、§16 第 5 条）。重复回调、针对旧 revision 的回调、针对已终结 cycle 的回调必须幂等返回或拒绝，去重状态落在 `boss_approvals` 的 approval idempotency key 上，不依赖 `server.py:24` 的进程内字典（§10.3）。
- **Trader 只接受完整且新鲜的 `ExecutionAuthorization`（**BREAKING**：改变入口签名）**：授权结构含 §10.4 列出的全部字段（`cycle_id`、`revision_no`、`decision_hash`、`risk_review_id`、`risk_approved_at`、`boss_approval_id`、`boss_approved_at`、`ruleset_version`、`portfolio_snapshot_id`、`market_as_of`）。执行前重新取时间，市场或账户快照默认超过 60 秒即失效，失效后必须用新快照重新风控**并重新获得 Boss 批准**；阈值置于风控配置（`config/risk.yaml` 的 `limits:`，现有 `RiskConfig` 无此类字段），不允许运行时静默放宽（§10.4、§16 第 9 条）。未批准、hash 不匹配、字段被修改或过期的指令一律拒绝。手动与自动指令进入同一审批链（§14.2）。
- **client order ID 稳定派生并幂等重试**：由 cycle + revision + 订单序号稳定生成，同一授权与订单序号的重试先查本地记录与券商状态，不得生成新的逻辑订单（§10.4）；与 Phase C 的「同一 client order ID 不重复下单」验收共用同一键。
- **登记本阶段待退旧实现**：按 §12.4 要求，在 `config/workflow/legacy_retirement.yaml` 登记本阶段引入的替代关系与退出条件——`risk/checks.py` 的就地裁剪路径、`agents/risk_validator.py::apply_guardrails` 的全套静默改写、`runtime/server.py` 的进程内回调去重、`cycles`/`decisions` 的旧决策写路径、以及 `trades.client_order_id` 的旧派生式。**本阶段只登记，不执行任何清除。**
- **不包含（属后续阶段）**：Clerk 编排、对账、绩效与归因的读模型（Phase C）；六个分析角色的职责重构（Phase D）；Dispatcher 运行时与自动事件日历（Phase E）；影子运行与切流（Phase F）。
- **与既有规划文档的一处刻意对齐**：设计文档 §14.4（阶段 D）把「移除 Risk Officer 对 Macro 观点的依赖」列为 Phase D 的工作。并行存在的规划 change `refactor-workflow-dataflow-architecture` 的 Phase B 任务 3.8 把同一件事放进了 Phase B。本 change 以设计文档为准，**不把该重构纳入本阶段**，只在其触及风控返回值契约时保持现状并在 design.md 中标注移交。

## Capabilities

### New Capabilities

- `decision/decision-audit-store`：决策与交易审计的不可变存储契约——`decision_cycles` / `decision_revisions` / `decision_risk_reviews` / `boss_approvals` / `cycle_events` 五张表的 additive schema 与职责边界、revision 的不可变性与 `parent_revision` 链、`decision_hash` 的规范化规则、`cycle_events` 的追加式与幂等键、历史决策的 `legacy_unknown` 标记原则（不为不可还原的历史伪造 hash 或快照关联），以及「运行检查点不是审计真相」的权威来源规则。
- `decision/approval-lifecycle`：从研究快照到执行的完整生命周期与状态机——cycle 创建必须绑定完整研究快照、状态转换与终态集合（含 No Action、`manual_review`、`superseded`、快照过期回到 Risk）、Chief—Risk 自动 Loop 的有界性（默认 3 轮）、Boss 只能对同一被风控批准的 revision hash 批准或拒绝、回调的持久化幂等与旧 revision / 已终结 cycle 的拒绝、以及终态副作用的单次执行。
- `risk/deterministic-review`：确定性风险审查与结构化回退契约——pass/fail 与交易前后指标由确定性引擎决定、LLM 解释不得覆盖硬规则结果、审查以整条修订为评估单元并覆盖跨单规则（行业层、相关簇与依赖组合状态的规则）、审查结果绑定 revision hash 与 ruleset 版本与组合/行情快照时点、审查记录保存执行前后风险指标、任何裁剪或替代方案只能作为结构化 counterproposal 返回 Chief、以及 decision 级审查与既有组合快照审查的存储隔离。
- `execution/authorization-gate`：Trader 的执行授权网关——`ExecutionAuthorization` 的完整字段与其与同一 revision 的绑定、未批准/hash 不匹配/字段被修改指令的拒绝、执行前的新鲜度校验与阈值来源（风控配置，默认 60 秒，不得静默放宽）、失效后重新风控与重新取得 Boss 批准、由 cycle + revision + 订单序号稳定派生的 client order ID 与幂等重试、以及手动与自动指令共用同一审批链。

### Modified Capabilities

无。本阶段新增的四项能力此前在 `openspec/specs/` 下均不存在，而既有能力的需求文本无需改动：`workflow/legacy-retirement` 已经规定「每个阶段必须登记其待退旧实现」，本阶段只是按该规定提交登记项，不改变登记机制本身；`workflow/run-contracts` 与 `agent/task-projection` 是被本阶段**消费**（研究快照复用 `TaskProjectionEnvelope` 与 `reuse_decision()` 的理由枚举），其字段与语义不变；`agent/action-vocabulary`、`data/data-layer-architecture`、`execution/option-survival`（Phase A 修正的到期桶边界）在本阶段均不触碰。

## Impact

**新增模块**

- `src/ats/decision/`（或与既有分层一致的等价位置）：cycle / revision / risk review / approval / cycle event 的领域模型、`decision_hash` 规范化、追加式仓库与 compare-and-set 状态转换、research snapshot builder。
- 决策状态机的图改造落在既有 `src/ats/graph/chief.py` 与 `src/ats/graph/chief_state.py`，不新建平行的审批图（§5.4 的演进路径），并继续使用 `graph/checkpoint.py` 的 checkpointer 作为**运行恢复**设施。

**被修改的既有模块**

- `src/ats/graph/chief.py`：`risk_gate` 与 `persist_decision` 拆分（按轮保存 revision、保存 risk review）、新增 `chief_revise` 节点与条件边、`boss_review` 输入收窄、`trader` 改为消费 `ExecutionAuthorization`。
- `src/ats/graph/chief_state.py`：新增 revision / hash / round / snapshot / authorization / cycle status 字段（`chief_state.py:19-53`）。
- `src/ats/risk/checks.py`：`_apply_order_caps()`（`92-117`）与 `_clip_event_notional()`（`120-141`）的静默改写行为被移除或改写为返回 counterproposal；`pre_trade()` 返回值契约变更（`checks.py:18-21`）。`risk/assess.py` 的确定性计算保持只读与数值不变，仅新增审查结果的结构化输出。
- `src/ats/trader/execute.py`：`execute()`（`33-35`）入口改为只接受完整授权；`client order ID` 派生引入 revision 与序号；换取行情与账户快照的路径带上 as-of 与新鲜度判定。
- `src/ats/broker/ibkr.py`：`order_ref()`（`373-379`）的派生式与长度约束随 client order ID 变更同步。
- `src/ats/runtime/server.py`：回调去重从进程内 `_RESUMED`（`server.py:15-41`）改为基于持久化 approval idempotency key。
- `src/ats/memory/store.py`：五张新表 DDL 与 additive 迁移方法（沿用 `_migrate()` 的 `PRAGMA table_info` 探测式写法与 `data_migrations` 键控一次性迁移先例，`store.py:477-682`）；`trades` / `fills` 增加关联列与索引。
- `config/risk.yaml`：新增新鲜度阈值配置项（文档要求阈值置于风控配置；当前 `config/risk.yaml` 与 `RiskConfig`（`config.py:117-152`）均无此类字段）。
- `config/workflow/legacy_retirement.yaml`：按既有条目格式追加本阶段待退项与退出条件。

**被修改的测试（断言了即将改变的行为）**

- `tests/test_risk.py::test_pre_trade_event_clip`（`96-103`）与 `::test_pre_trade_blocks_buy_in_derisk`（`86-93`）当前断言风控裁剪订单/清空 slate，需改写为断言结构化 counterproposal 与被审 revision 不变。
- `tests/test_chief_graph.py::test_derisk_blocks_buys_before_review`（`76-86`）同上。
- `tests/test_risk_validator.py` 整组断言未接线模块 `agents/risk_validator.py::apply_guardrails` 的静默裁剪（`28-32` 等），随该模块进入待退登记而一并处置。
- `tests/test_server.py::test_duplicate_callback_executes_once`（`40-63`）当前通过清空进程内 `server._RESUMED` 达成，需改为跨进程/重启后可复现的持久化幂等断言。
- **新增测试（不改既有断言）**：跨单规则的整条修订判定（同一行业层合计超限、同一相关簇集中度超限）与执行前后风险指标（同一修订重复审查指标稳定、修订内容变化后指标改变）两类用例，随新的只读审查入口一并加入。

**依赖与外部接口**

- 无新增第三方依赖。执行侧仍通过既有 `IBKRBroker`，本阶段不改变券商接口本身，只改变进入它的前置条件。
- 与 Phase A 契约的消费关系：research snapshot 读取 `TaskProjectionEnvelope`；幂等键风格沿用 `TriggerContext.idempotency_key()` 的「不含请求时刻」原则；hash 规范化沿用 `task_projection.content_hash()` 的做法；待退登记沿用 `RetirementRegistry` 与既有 YAML 条目格式。

**风险与回滚**

- 图与风控返回值契约的变更面较大，`tests/test_risk.py`、`tests/test_chief_graph.py`、`tests/test_trader.py`、`tests/test_server.py` 都直接断言现状。执行顺序须先落存储与 hash，再改风控返回值，最后改 Trader 入口，使每一步都在可运行的中间态上验证。
- 真实下单入口在切换期间必须保持唯一可执行路径，且默认仍为 paper / dry-run；任何时刻不得出现两个可下真单的入口（§15.5 第 5 条、§16 第 4 条）。回滚以「旧写路径保留 + additive schema 不删列」为前提，不依赖数据回填。
