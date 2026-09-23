## Why

目标架构（`docs/TARGET_WORKFLOW_DATAFLOW.md` §1、§7.2、§14.4）要求分析师之间除「层次 → 行业」与「信息 → 基本面」两条依赖外互不读取彼此观点，且只有主理人可以汇总全部结论。当前实现与该约束有多处实测冲突：层级分析师直接产出超配/低配/清仓并绑定预算使用率（`agents/sector/layer_review.py:213-220`、`schemas/sector.py:277`），行业分析师在轮动里读取宏观报告（`agents/sector/review.py:229-241`）与 PEAD dossier（`agents/sector/assemble.py:257-279`），基本面在 monitor 与 graph 注入里读取 Sector 与 Macro 观点（`agents/pead/monitor.py:85-95`、`graph/pead.py:125-144`）并自行跑风险引擎产出带数量的订单（`graph/pead.py:473-519`、`agents/pead/score.py:189-231`），风控 memo 仍注入宏观 regime（`agents/risk_officer/review.py:29-39`），而主理人 `agents/chief/assemble.py` 直读旧表且任一分析师缺失时静默降级为空串（`:90-92`、`:136-137`）——既不固定研究快照，也不阻断自动交易。`decision/snapshot.py` 已实现快照与阻断，但从未被接线。

同时，架构守卫里 27 条例外以「Phase D」为由挂起（采集侧直连 Provider、分析师直连取数），若不随本阶段清退，边界约束就是纸面上的。

## What Changes

- **BREAKING**：层级分析师不再拥有配置权。超配/标配/低配/清仓与目标权重、预算使用率从层级产出中移除，配置权收口到行业分析师（行业 / 层次 / 标的三级）。
- **BREAKING**：`sector/layer-analyst` 能力退役，契约迁入 `agent/layer-analyst`（角色契约归 `agent/` 域，`sector/` 只留产业链领域知识）。
- 新增行业分析师配置权契约：只消费 `LayerAnalysis` 投影与共享事实，禁止读取 Macro、Fundamental、Information、Technical 观点；承接层级预算与护栏不变式、跨层轮动。
- 新增信息分析师角色：输入只能是已准入文档与中性证据，输出 `InformationBrief`（事实变化、影响候选、关联实体、置信度、时效、待核验项），不输出买卖/仓位/组合建议；迁移 `pead/research.py`、`pead/triage.py`、`runtime/digest.py`，并移除 `pead/monitor.py` 直写 dossier 的副作用。
- 基本面拆为例行与事件两种模式：例行更新预期基线，事件在 cutoff 冻结基线并计算对基线 / Consensus / 市场隐含预期的三分差异；**保留非可执行投资观点**（方向、信心、理由、可证伪条件），剥离数量 sizing 与 `risk assess` / `review_guardrails` / `pre_trade` 调用；不再读取 Sector / Macro 观点。
- 风控输入收口：只消费交易提案、组合快照、市场快照与规则包，移除 memo 对宏观 regime 的读取。
- 主理人读取六类分析投影并固定 research snapshot：六类齐备才进入决策周期，缺失/过期/失败即判 `incomplete` 并阻断自动交易（可发布缺口报告）；快照失效时 cycle 转 `superseded` 而非原地换输入。
- 架构守卫：采集侧确定性组件迁出 `agents/`，分析师取数改经数据产品入口，清退全部标注「Phase D」的 27 条例外；新增「例外必须声明收敛阶段且不得跨阶段挂起」的约束。
- Phase D 只定义双模式的触发契约与显式入口（CLI + 显式 `WorkflowRunRequest`），财报日历自动发现与触发留 Phase E。

## Capabilities

### New Capabilities
- `agent/layer-analyst`：层级分析师只产出层级状态、证据与层内相对排序，不产出配置结论；产出以 `LayerAnalysis` 投影发布（承接自 `sector/layer-analyst` 并重迁至 `agent/` 域）。
- `agent/sector-allocation`：行业分析师独占行业 / 层次 / 标的三级配置权，只依赖 `LayerAnalysis` 与共享事实，承接预算使用率与护栏不变式、跨层轮动。
- `agent/information-analyst`：信息分析师只消费已准入文档，产出 `InformationBrief`，禁止投资建议与直连 Provider。
- `agent/fundamental-pead`：基本面分析师例行 / 事件双模式，剥离越界依赖与内部交易风控，保留非可执行投资观点。
- `agent/risk-officer-inputs`：风控只消费提案、组合快照、市场快照与规则包，不消费分析师观点。
- `decision/research-snapshot`：主理人固定研究快照、六类投影齐备校验、缺口阻断自动交易。

### Modified Capabilities
- `workflow/architecture-guards`：追加「采集侧确定性组件不得位于 `agents/`」与「守卫例外必须声明收敛阶段且不跨阶段挂起」两条需求。
- `sector/layer-analyst`：能力退役（全部需求 REMOVED），契约迁入 `agent/layer-analyst`。

## Impact

- 代码：`src/ats/agents/sector/`（layer_review / review / cross_section / rotation / assemble / report）、`src/ats/agents/pead/`、`src/ats/agents/information/`（新建）、`src/ats/agents/risk_officer/review.py`、`src/ats/agents/chief/assemble.py`、`src/ats/graph/pead.py`、`src/ats/graph/chief.py`、`src/ats/agent/task_projection.py`（payload 扩字段）、`src/ats/workflow/architecture_guards.py`、`src/ats/workflow/run_contracts.py`（technical 改为必需）、`src/ats/decision/snapshot.py`（接线）。
- 数据：`task_projection_envelopes` 成为六类分析的唯一发布路径；旧表 `sector_reviews` / `macro_reviews` / `pead_dossier` 在迁移期保留为兼容读模型。
- 契约：`LayerAnalysisPayload` / `InformationBriefPayload` / `FundamentalExpectationUpdatePayload` / `FundamentalEventReviewPayload` 按 §7.3 补齐字段（additive，旧字段保留）。
- 退役登记：`config/workflow/legacy_retirement.yaml` 新增 Phase D 段（层级配置字段、PEAD 内部风控、旧读入口等）。
- 外部：无 API / 依赖变更；架构守卫例外清零后 `tests/test_architecture_guards.py` 的例外清单随之收窄。
