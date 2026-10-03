# Workflow–Dataflow 集成验收交接矩阵

状态：供后续独立 Workflow/Agent 集成验收 change 使用。本文冻结数据侧契约边界，并区分代码实现与运行资格；不代表任何消费者已获得 Phase F 读取资格，也不要求当前 change 修复旧 Workflow 路径。

权威角色契约见 [`target_dataflow_coverage.yaml`](../../config/data/target_dataflow_coverage.yaml)，规范见 [`TARGET_WORKFLOW_DATAFLOW.md`](../TARGET_WORKFLOW_DATAFLOW.md) §4。后续集成 change 可以验证时序、Task Projection 内容和恢复语义，但不得更改本表的数据 owner、允许投影边、来源治理、持久化队列和 runtime/internal-state 边界。

| 角色 | 冻结的数据输入契约 | 已实现/代码级边界 | 后续完整 Workflow 必须验证 |
|---|---|---|---|
| Layer Analyst | `HIER_DATA + DOC_DATA`；`sector_inputs.industry_named`、`unstructured.admitted_documents` | 通过 Data Products 读取；不消费其他 Agent 投影。来源注册、版本/as-of 暴露和回退仍未完整验收 | Workflow 只传实体/层次范围和 as-of；材料版本及缺口进入 LayerAnalysis 引用；并行运行时不改写共享事实 |
| Information Analyst | `DOC_DATA`；`unstructured.admitted_documents` | 新读取入口使用已准入文档产品；不从 Memory、Provider 或底层表作为正式材料 API 读取 | 信息简报保留 source/version/published-at/completeness；不得把仅发现、quarantine 或待人工审核候选作为已准入材料 |
| Sector Analyst | `HIER_DATA`；`sector_inputs`；仅允许 `LayerAnalysis` 投影 | manifest 只允许 Layer→Sector；数据读取和层次分析输入分别引用 | Dispatcher 等待 Layer 完成/复用兼容快照；Layer 缺失、过期时阻断完整交易流程但允许独立 Sector 诊断 |
| Fundamental Analyst | `COMPANY_DATA + HIER_DATA`；`fundamentals.fetch`；仅允许 `InformationBrief` 投影 | manifest 只允许 Information→Fundamental；routine/event 双模式边界仍需完整数据回放验收 | routine 更新预期基线；event 冻结基线并生成 surprise/guidance 输出；事件恢复后不重复生成发布或交易副作用 |
| Macro Analyst | `MACRO_DATA`；`macro_inputs.regional_monthly` | 不允许读取其他分析观点；真实来源/as-of/缺口仍逐域未验收 | 可单独异步运行；完整流程中与 Sector/Fundamental/Technical 并行，彼此不传观点 |
| Technical Analyst | Runtime `MARKET_DATA`；`runtime.market_data.fetch_close_history_many` | 实时行情走 Runtime Data Gateway，不进入持久化刷新队列；逐标的状态和时间戳已有定向测试 | 验证查询时间/as-of 传入投影、超时/缺标的保持显式状态；不得等待持久化 worker 或把 runtime 查询伪装为研究 vintage |
| Chief | `PORTFOLIO_DATA + HISTORY_DATA`；`execution.state_api`；唯一允许汇总六类分析投影 | manifest 约束唯一汇总角色及六类 projection 输入 | 决策 revision 固定引用所有必需分析 run/projection 和内部状态快照；失败或过期分析阻断自动交易；No Action 也形成可审计终态 |
| Risk Officer | `PORTFOLIO_DATA + MARKET_DATA + RISK_RULES`；允许读取决策提案，不读分析师观点 | 数据/意见隔离写入覆盖清单；Risk 多轮时序属于后续 Chief–Risk Workflow 验收 | 每轮风险审查绑定同一 cycle 与 decision hash；拒绝/counterproposal → Chief 新 revision → 再审；超轮次 fail closed；审查事件顺序可恢复 |
| Trader | `APPROVED_EXECUTION_AUTHORIZATION`；`approval.require_authorized_revision` | 只允许消费同一 revision 的有效审批授权；没有授权必须拒绝 | 风控批准、Boss 人工批准和 decision hash 三者精确绑定；中断恢复/重复 dispatch 不造成重复 broker submit |
| Clerk | `BROKER_STATE + DECISION_APPROVAL_CONTEXT`；`clerk.reconcile_and_ledger` | 确定性账本/对账边界与 Agent 观点分离 | 订单、部分/迟到成交、撤单、拒单及人工单可重放；事件引用到 cycle/revision/approval；重启补偿幂等且绩效可重建 |

## 全流程集成必须保留的禁止边

- 除 `LayerAnalysis → Sector`、`InformationBrief → Fundamental` 外，分析师之间不得互读投影；Chief 是唯一可汇总六类分析观点的角色。
- Agent 不直接调用 Provider、数据 adapter 或底层持久化表。持久化数据只能经 Data Products；实时行情/期权只能经 Runtime Data Gateway；组合与交易状态只能经 Internal State API/审批执行契约。
- 所有持久化写入由受管队列 worker 在有效 lease 下执行。手动、定时、事件和允许的 cache miss 只能 enqueue；实时查询不得入持久化队列。
- Dataflow qualification 是逐 `domain + consumer + contract_version` 的只读门禁；没有资格不得被 Workflow 总开关绕过。资格查询不等于切流。
- 旧 scheduler/Agent/Workflow/adapter 路径的已知问题只进入差距清单；本交接不授权其修复或迁移。目标新路径不得依赖这些旧路径作为隐式 fallback。

## 后续集成 change 的验收用例

1. Dispatcher：单 Agent、依赖子流程、完整流程的手动/异步运行；确定性依赖补齐、过期/失败阻断及必要投影的 lineage。
2. 并发：Macro、Technical 与其他无依赖分析任务并行；只 Layer→Sector、Information→Fundamental 存在等待边，互不覆盖 memory/context。
3. 事件模式：财报事件使用冻结 Fundamental 基线；宏观事件触发按事件范围执行；重复、改期和并发 trigger 不重复产生持久化副作用。
4. Chief–Risk–Boss：多轮 revision、counterproposal、人工批准、拒绝、最大轮次及超时/崩溃 checkpoint 恢复；所有审核绑定精确 revision hash。
5. Trader/Clerk：同一 revision 单次授权执行；重复恢复不重复下单；券商迟到/部分成交经过 Clerk 账本补偿和绩效重建。
6. 跨进程恢复：任务投影、数据版本、审批事件、queue task ID 和内部状态 snapshot 在进程重启后可解析；无 lease/无审批时没有 provider 写入或 broker 副作用。
7. 缺口语义：必要输入缺失、过期、quarantine、partial、runtime unavailable 时，单 Agent 诊断可返回显式降级结果，完整自动交易流程 fail closed。

以上测试应使用隔离数据库/券商模拟接口及可重放来源 fixture；生产来源新鲜度、授权、launchd owner 交接和逐消费者回退资格仍由本 Dataflow change 的独立逐源门禁决定，不能由 Workflow mock 测试替代。
