## Why

`ai_work_adoption` 已能保存 Anthropic Economic Index 的职业与任务原始指标，但 L1 Evidence Observer 目前只陈述 Usage Share，无法以稳定、可复算的方式回答“前沿 AI 有多少进入核心生产流程，以及渗透的广度和流量权重如何变化”。现在需要把已经确认的生产化代理标准、双口径渗透指标和趋势证据边界固化为受治理的数据产品与 Observer 命题，避免把 Claude 使用份额误写成员工采用率或把两个月变化误写成长期趋势。

## What Changes

- 新增版本化的 `core_production_workflow_proxy_v1`：仅使用 1P API，要求 `Usage Share > 0`、`Work Use Share >= 80%`、`Automation Share >= 80%`、`Directive Share >= 50%`。
- 在职业与任务两个 grain 上新增两组核心派生指标：可见单元生产化率和生产化流量份额，并公开分子、分母、公式、阈值版本和完整 observation lineage。
- 增加“职业内生产化任务覆盖率”：对每个职业，以其在指定 O*NET taxonomy 中的全部关联任务为分母，以其中能映射且达到生产化代理标准的任务为分子；由于 taxonomy 滞后和隐私过滤，该指标明确标记为覆盖下限。
- 增加职业覆盖率分布统计，复刻论文 Figure 4 的互补累计分布（CCDF），回答有多少职业至少达到指定比例的生产化任务覆盖；该分布作为补充证据，不进入原有四条核心序列的趋势状态。
- 将 taxonomy 映射覆盖、未映射任务和未发布/隐私过滤 cell 作为质量与覆盖说明，并公开其对职业内任务覆盖下限的影响；不把该下限冒充员工采用率或无偏的真实任务渗透率。
- 新增固定 Observer 命题，分别判断生产化广度与深度；少于三个可比月份返回 `insufficient_history`，不得生成趋势性结论。
- 仅为本 change 新增的 L1 AI 应用层生产化渗透 Observer 增加固定“方法卡”，展示来源产品、期间、release、地理范围、可见样本、隐私/缺失口径、taxonomy 与映射覆盖、threshold/methodology/derivation versions、历史可比性、血缘和不可推断事项；方法卡不是通用 Observer 契约，不适用于芯片设计、WFE、Macro、Sector 或其他现有 Observer。
- 新增结构化 Pandas 输出及可复现的 Seaborn 报告图：两个月仅展示月度比较，至少三个同 methodology 的连续月份才展示趋势。
- 新增职业和任务 TOP10，按最新生产化 Usage Share 排名，并同时展示 Usage、Work、Automation、Directive 及其月度百分点变化。
- 保持 Claude.ai 与 1P API 隔离。Claude.ai 可作为互动需求背景，但不得进入核心生产流程代理指标的分子或分母。
- 不把该 Observer 结果赋予交易信号、仓位、PEAD、Chief 或其他决策 Workflow 的证据权重。
- 审阅输出 SHALL 以方法卡、核心四指标、职业任务组合覆盖分布、分布尾部的完整披露、TOP10 和文末指标注释的顺序呈现；图表标题 SHALL 与文字指标名一致。达到 50% 覆盖的职业是分布尾部披露，不得称为典型职业或员工/岗位生产化证据。
- Evidence SHALL 支持按 `sector` 和 `layer` 单独运行已注册的只读 Observer，并输出该层独立更新的报告。注册声明 SHALL 写在 sector layer 的 `evidence_observers`，而不是 Python scope 白名单或 Chain `claims`；AI 生产化命题注册在 `ai_hardware/L1_app`，但不将这套命题或指标泛化为其他层；无注册 Observer 的层返回明确状态而不是运行全部 L1–L8。

## Capabilities

### New Capabilities

- `evidence/ai-production-penetration-observer`: 定义固定生产化命题、方法卡、确定性状态判读、职业内任务覆盖与分布、TOP10 事实、结构化表格、趋势图与报告语义边界。

### Modified Capabilities

- `data/anthropic-economic-index`: 扩展领域 DataProducts，提供核心生产流程资格、职业/任务双口径渗透指标、流量份额、职业内生产化任务覆盖下限、职业分布、覆盖诊断和可复现趋势数据。

## Impact

- 主要影响 `src/ats/data/products/ai_work_adoption.py`、`src/ats/agents/evidence/work_adoption.py` 中本命题的专属入口、sector layer schema/`config/sectors/ai_hardware.yaml`、按层分发的 Evidence CLI、AI 应用层报告章节与相应文档；不修改其他领域 Observer 的数据契约、Chain `claims` 或结论。
- 新增 Pandas DataFrame 契约和 Figure 4 风格 CCDF；Seaborn/Matplotlib 只用于确定性渲染，不参与指标计算或状态判读。
- 现有原始 observations、ingestion、source-product series 和已归档 Anthropic change 不变；新增派生结果保持 query-time、版本化且可回放。
- 新增单元测试、真实受治理数据验收、图表快照/结构检查及 Consumer 边界测试。
