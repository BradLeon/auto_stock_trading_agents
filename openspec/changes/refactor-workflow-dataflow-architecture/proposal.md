## Why（为什么）

`docs/TARGET_WORKFLOW_DATAFLOW.md` 定义了从当前硬编码、局部耦合的系统迁移到依赖感知工作流、受治理数据流、不可变审批生命周期和确定性交易账本的完整 Phase A–F 路线。当前实现已经具备有价值的分析师、风控、券商、日志和数据平台组件，但运行时边界尚未系统性强制执行已确认的角色隔离、失败门禁、审计性、幂等性和事件驱动调度模型。

本 change 是直接依据 Target 文档和当前代码库形成的自包含实施提案，覆盖达到目标状态所需的全部迁移阶段，不以任何其他 OpenSpec change 作为规划或实施前置依赖。已经存在的实现必须按本 change 的契约和验收标准核验；通过者直接复用，未完成或不满足契约者再补齐，不重复建设已经验证完成的能力。

## What Changes（变更内容）

- **Phase A——基线与契约：** 建立标准 `uv` 测试环境和可复现基线；核验并收口数据层中性证据写入切换及旧路径退役状态；统一 action 词表；修正互斥且完备的期权生存资金桶；定义 WorkflowRun、TriggerContext、Task Projection、新鲜度、幂等、架构边界和旧实现退役契约。
- **Phase B——决策审计与执行安全：** 用不可变决策 revision、结构化确定性风控审查、有界 Chief—Risk 修订循环、绑定 hash 的 Boss 审批、过期授权复审和幂等 Trader 提交，替换单次 Chief/Risk 路径。
- **Phase C——书记员与账本：** 新增确定性书记员工作流，连接研究快照、决策、审批、订单、成交、盯市、绩效和归因；支持重放与对账；LLM 复盘文字不进入规范事实。
- **Phase D——分析师职责重构：** 将 Layer Analyst 收窄为层级、截面和证据研究；将行业/层级/标的配置权转移给 Sector Analyst；新增 Information Analyst；把 Fundamental/PEAD 拆为例行与事件模式；移除未授权的分析师观点依赖；由 Chief 独占跨分析师汇总权。
- **Phase E——Dispatcher 与日历：** 引入依赖感知异步调度，支持手动、定时、事件触发、局部和完整运行；增加投影复用与完整性门禁；用受治理、可版本化的 Schedule Calendar 和 Trigger Ledger 替换静态事件列表。
- **Phase F——影子运行与切流：** 在不开放新路径实盘下单的前提下并行运行新旧路径，对比输出和审计关联；分别切换读取、调度和交易路径；验证回滚；确保任一时刻只有一条实盘下单路径生效。
- **BREAKING：** Layer Analyst 不再拥有配置或预算使用决策权；Boss 不再能修改已经风控批准的订单；Fundamental 不再读取 Sector/Macro 观点或执行最终组合风控；未声明的跨分析师读取和 Agent 直连 Provider 将 fail closed。
- 在 additive 迁移期间保留现有领域审查表、CLI 和读模型，直到登记的退出条件满足。无法恢复提案、快照或 hash 的历史记录标记为 `legacy_unknown`，不得伪造审计数据。

## Capabilities（能力）

### New Capabilities（新增能力）

- `workflow/test-baseline`：标准 `uv` 环境、可复现全量测试测量、失败分类和阶段质量门禁。
- `workflow/architecture-guards`：对分析师依赖隔离、Agent/Data Product 边界以及禁止将 Agent 观点写为共享事实的可执行约束。
- `workflow/legacy-retirement`：墓碑登记、fail-closed 读取、默认 dry-run 清除、消费方清零退出条件和逐阶段退役清单。
- `workflow/dispatcher-runtime`：工作流/任务契约、触发归一、依赖展开、并发、投影复用、失败隔离、完整性门禁和幂等执行。
- `workflow/projection-store`：类型化 Task Projection envelope、数据/输入血缘、新鲜度与兼容性判断、内容 hash、不可变研究快照和 additive 持久化。
- `agent/action-vocabulary`：统一的五值分析/决策 action 词表及显式券商侧映射。
- `execution/option-survival`：互斥且完备的期权到期资金桶，以及可审计的概率和名义金额语义。
- `analysis/information-analyst`：受治理的信息抽取、来源追溯、重要性、影响候选、独立调度，以及禁止输出投资/订单决策。
- `analysis/fundamental-pead`：使用 Information 输出、共享产业链事实、冻结基线、Scorecard、业绩指引和迟到文档版本升级的例行预期更新与事件型 PEAD 复盘。
- `sector/sector-allocation`：行业/层级/标的配置权、消费 `LayerAnalysis`、显式缺失输入行为，以及排除其他分析师观点。
- `decision/approval-lifecycle`：不可变 Chief revision、结构化 Risk 审查与 counterproposal、有界修订循环、Boss 审批绑定、授权过期和 Trader 幂等。
- `trading/clerk-ledger`：确定性的决策到成交审计关联、券商对账、重放、绩效、归因，以及可选 LLM 文字与账本事实分离。
- `scheduling/event-calendar`：版本化事件日历、人工覆盖、触发幂等、改期、取消、时区/交易时段语义及重启/misfire 补偿。

### Modified Capabilities（修改能力）

- `data/data-layer-architecture`：核验并完成中性证据写入由 Data Platform 独占，Workflow Memory 仅保存观点与血缘引用，并维持 additive、读兼容的迁移行为。
- `sector/layer-analyst`：移除层级配置、预算使用和跨层配置职责；保留层级结构、证据、截面比较、可证伪条件和逐层报告，作为 Sector Analyst 的上游研究。

## Impact（影响）

- **环境与质量：** 项目安装/测试命令、全量测试基线记录、失败分类、阶段验收门禁和架构测试。
- **数据与 Memory：** 证据写入器和 repository、旧库迁移初始化顺序、Workflow Memory 连接生命周期、`task_projection_envelopes`、workflow/agent run 记录、trigger 记录和兼容读模型。
- **工作流运行时：** `src/ats/runtime/scheduler.py`、新增 Dispatcher/Calendar runtime、任务注册表、重试/幂等逻辑，以及 LangGraph Chief/PEAD 状态和 checkpoint。
- **分析师角色：** Sector/Layer 模块、PEAD research/monitor/prep/score 模块、新 Information Analyst、Chief 上下文组装和 Risk Officer 输入边界。
- **Schema 与审计：** additive 的 decision cycle/revision/risk review/Boss approval/event 表，以及 orders/fills 上可空的审计关联；现有组合 `risk_reviews` 与 `decision_risk_reviews` 保持区分。
- **执行与记账：** Boss 回调、Trader 授权校验、稳定 client-order ID、券商对账、journal/performance/attribution 整合和书记员读模型。
- **兼容与上线：** additive migration、legacy tombstone、`legacy_unknown` 历史、影子模式、可分别切换的读取/调度/交易路径，以及单一实盘下单路径约束。
- **外部行为：** 手动和自动工作流会返回明确的完整性/新鲜度错误；风控后 Boss 修改会被拒绝；过期审批必须重新风控和人工批准；不完整的全流程不得自动交易。
