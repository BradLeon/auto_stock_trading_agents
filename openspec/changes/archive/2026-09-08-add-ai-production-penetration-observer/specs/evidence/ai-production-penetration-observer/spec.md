## Purpose

定义 L1 Evidence Observer 如何以 Anthropic 1P API 为前沿 AI 生产部署代理，形成可证伪、可重放且不会被误读为员工采用率的生产流程渗透命题、事实表和趋势图。

## ADDED Requirements

### Requirement: Observer 使用固定的生产流程渗透命题

Observer SHALL 使用命题 ID `ai_core_production_workflow_penetration`，命题文本为：“以 Anthropic 1P API 作为前沿 AI 生产部署的代理，满足核心生产流程标准的职业和任务是否持续扩大，且这些单元所承载的 API 使用量是否持续提高？”命题 SHALL 只消费受治理 DataProducts 的 `core_production_workflow_proxy_v1` 结果，不得读取 Provider、物理表或自行重算阈值。

#### Scenario: 运行固定命题
- **WHEN** L1 Observer 运行 AI 生产流程渗透观察
- **THEN** 输出 SHALL 包含固定 claim ID、命题文本、阈值定义、1P API source product、分析期间和 snapshot manifest
- **AND** SHALL NOT 把 Claude.ai 与 1P API 合并为一个渗透率

### Requirement: Observer 分开陈述广度与深度

Observer SHALL 将 occupation/task 可见单元生产化率定义为生产化广度，将 occupation/task 生产化流量份额定义为生产化深度。输出 SHALL 保留四条独立序列，不得以未经定义的加权平均生成单一分数。

#### Scenario: 广度和深度方向不同
- **WHEN** 职业生产化率上升、任务生产化率下降，而两类流量份额均上升
- **THEN** Observer SHALL 陈述“深度提高、广度表现混合”
- **AND** SHALL NOT 概括为“渗透率全面上升”

### Requirement: 趋势判读要求至少三个可比月份

Observer SHALL 只在同一 source product、methodology 和 threshold version 下存在至少三个连续自然月时生成趋势标签。对每条序列，全部相邻变化非负且至少一项为正 SHALL 标记 `directional_up`；全部相邻变化非正且至少一项为负 SHALL 标记 `directional_down`；全部相邻变化为零 SHALL 标记 `flat`；其余 SHALL 标记 `mixed`。不足三个可比月份 SHALL 返回 `insufficient_history`，但 MAY 展示明确标为月度比较的最近两期变化。

#### Scenario: 当前只有两个可比月份
- **WHEN** 数据仅包含 2026-04 和 2026-05
- **THEN** Observer SHALL 返回 `trend_status=insufficient_history`
- **AND** MAY 陈述“2026-04 至 2026-05 月度变化”，但 SHALL NOT 使用“持续”“趋势”“加速”或“放缓”描述

#### Scenario: 三个月单调上升
- **WHEN** 一条序列具有三个连续可比月份且两个相邻变化均不小于零、至少一个大于零
- **THEN** Observer SHALL 将该序列标记为 `directional_up`
- **AND** SHALL 返回每期水平值、变化值和判读依据

### Requirement: 命题状态由四条透明序列确定

当趋势历史充分时，occupation 与 task 两条广度序列均为 `directional_up` 才 SHALL 标记 `breadth_expanding`，两条深度序列均为 `directional_up` 才 SHALL 标记 `depth_deepening`；二者同时成立才 SHALL 标记总体 `penetration_expanding`。任一维度内部方向不一致 SHALL 标记相应维度 `mixed`，不得通过多数投票隐藏反向序列。

#### Scenario: 三条上升一条下降
- **WHEN** 四条序列中三条为 `directional_up` 而一条为 `directional_down`
- **THEN** 总体状态 SHALL NOT 为 `penetration_expanding`
- **AND** 输出 SHALL 指明下降的是 occupation/task 及 breadth/depth 中的哪一条序列

### Requirement: Observer 输出结构化事实表和 TOP10

Observer SHALL 输出期间汇总表、逐职业“任务组合已确认生产化覆盖”（兼容机器字段为 `occupation_production_task_coverage_lower_bound`）、职业覆盖率分布、occupation TOP10 和 task TOP10。TOP10 SHALL 使用 DataProducts 返回的 qualified 排名，展示最新 Usage、Work、Automation、Directive 及各自月度百分点变化；每个事实 SHALL 带单位、来源期间、质量状态和 lineage。默认 N 为 10，调用者 MAY 指定其他正整数。逐职业覆盖和职业分布 SHALL 标记为 taxonomy-dependent supplementary evidence，不得改变四条核心序列状态。

#### Scenario: 生成默认报告
- **WHEN** Observer 对最新可用月份运行且未指定 N
- **THEN** 输出 SHALL 包含四个核心指标的期间表、逐职业任务覆盖表、职业覆盖率分布及 occupation/task 各 10 行
- **AND** 非达标单元 SHALL NOT 混入 production TOP10

### Requirement: Observer 每次输出固定方法卡

仅 `claim_id=ai_core_production_workflow_penetration` 的 L1 AI 应用层生产化渗透 Observer SHALL 在每次成功或部分成功运行中输出 `methodology_card`。方法卡 SHALL 至少包含：Provider 与 source product、地理范围、数据期间、最新期间、上游 release/published_at、可见职业数与任务数、qualified counts、公开/隐私缺失语义、taxonomy version、mapped/unmapped task counts、threshold version 与四项阈值、Anthropic methodology version、平台 derivation version、可比 regime、历史是否足够判断趋势、质量状态、lineage/manifest 标识以及不可推断事项。方法卡 SHALL 使用中文解释和稳定机器字段，且不得复用不属于当前 release/methodology 的历史论文验证率作为当前准确率。

`methodology_card` SHALL 是该领域 Observer packet 的专属字段，而不是通用 Observer interface、基类、protocol 或所有 L1 Observer 的必填字段。系统 SHALL NOT 因本 requirement 改变芯片设计、WFE、Macro、Sector 或其他现有 Observer 的 schema、返回字段、渲染、依赖、失败语义或运行路径。

#### Scenario: 当前只有两个可比月份
- **WHEN** Observer 使用 2026-04 和 2026-05 的 1P API 数据运行
- **THEN** 方法卡 SHALL 显示 `history_status=insufficient_history`、两个期间及当前 methodology/taxonomy/threshold/derivation versions
- **AND** SHALL 明确说明 Usage 是产品流量份额、生产化是代理、隐私过滤缺失不等于零且无法判断持续工作流、员工采用率、生产率或 ROI

#### Scenario: 部分 taxonomy 映射
- **WHEN** 部分可见或 qualified tasks 无法映射到当前 taxonomy
- **THEN** 方法卡 SHALL 显示 mapped/unmapped 数量、映射覆盖率和 lower-bound 影响
- **AND** SHALL NOT 因核心四序列仍可计算而隐藏 taxonomy warning

#### Scenario: 运行其他领域 Observer
- **WHEN** 系统运行芯片设计、WFE、Macro、Sector 或任何不属于 `ai_core_production_workflow_penetration` 的现有 Observer
- **THEN** 本 change SHALL NOT 要求其生成 `methodology_card` 或接受 AI 应用层专属字段
- **AND** 其既有输出契约、报告内容、依赖和行为 SHALL 保持不变

### Requirement: Observer 展示职业内任务覆盖及其职业分布

Observer SHALL 展示最新期间逐职业“任务组合已确认生产化覆盖”（机器兼容字段 `occupation_production_task_coverage_lower_bound`）的可排序表，以及 Figure 4 风格的职业互补累计分布。逐职业表 SHALL 至少包含 occupation ID/名称、qualified mapped task count、taxonomy task count、coverage percent、版本、质量状态和 lineage。分布 SHALL 显示有多少 eligible occupations 至少达到 `10%`、`25%`、`50%`、`75%` 和 `100%` 的已确认覆盖，并可返回完整曲线数据。

#### Scenario: 展示职业覆盖分布
- **WHEN** Observer 获得指定月份的职业覆盖率分布
- **THEN** 输出 SHALL 同时提供 landmark 表和可复算的完整 CCDF 点集
- **AND** 面向读者的文本 SHALL 使用“至少达到某已确认覆盖的 eligible occupations 占比”，不得称为职业员工采用率；机器兼容字段可保留 lower_bound 命名

#### Scenario: 补充指标与核心状态方向冲突
- **WHEN** 职业内任务覆盖分布向更高覆盖移动但四条核心序列不满足 `penetration_expanding`
- **THEN** Observer SHALL 分开陈述补充证据和核心命题状态
- **AND** SHALL NOT 使用补充指标覆盖、投票或改写核心总体状态

### Requirement: 趋势可视化与结构化数据保持一致

Observer 报告 SHALL 基于同一受治理 DataFrame 生成图表与表格。只有两个可比月份时 SHALL 使用月度比较图并明确标注非趋势；至少三个可比月份时 MAY 使用时间序列图。图表 SHALL 标注 source product、threshold version、period range、单位和生成数据哈希，且图表数据 SHALL 可由 snapshot manifest 离线重放。

#### Scenario: 两个月数据渲染
- **WHEN** 只有两个可比月份
- **THEN** 报告 SHALL 使用 slope/dumbbell 或等价月度比较图展示四项指标
- **AND** SHALL NOT 以无说明的趋势线暗示长期方向

#### Scenario: 图表渲染依赖不可用
- **WHEN** 可视化运行时不可用但结构化派生结果有效
- **THEN** Observer SHALL 保留表格、事实、状态和 manifest，并返回明确的 visualization warning
- **AND** SHALL NOT 因图表失败丢弃或改变核心结论

#### Scenario: 渲染 Figure 4 风格职业覆盖曲线
- **WHEN** 指定期间存在有效的职业生产化任务覆盖分布
- **THEN** 报告 SHALL 以 x 轴“职业内生产化任务覆盖下限”、y 轴“达到或超过该覆盖的职业占比”渲染单调不增的 CCDF
- **AND** SHALL 标注 eligible occupation 数量、taxonomy version、期间、10/25/50/75/100 landmarks、“公开数据可确认”语义说明和生成数据哈希

### Requirement: Observer 保持语义与决策边界

Observer SHALL 明确说明 Usage 是 Claude 产品流量份额、SOC 是职业分类而非企业行业、qualified 是生产化代理而非已确认部署，且缺失 cell 不等于零。Observer 输出 SHALL NOT 推断员工采用率、企业席位渗透率、岗位替代数量、持续运行率、生产率、ROI 或交易方向；结果 SHALL NOT 自动注入 PEAD、Chief、仓位或下单路径。

#### Scenario: 生产化流量份额上升
- **WHEN** task production traffic share 在可比月份上升
- **THEN** Observer MAY 陈述“达到核心代理标准的任务承载了更高比例的 1P API 流量”
- **AND** SHALL NOT 改写为“更多企业或员工已经采用 AI”或“AI 已替代更多工作”

#### Scenario: 下游交易工作流请求直接消费
- **WHEN** 交易决策 Workflow 请求将命题状态作为评分或仓位输入
- **THEN** 系统 SHALL 拒绝默认接入并要求独立 change 定义证据权重与决策边界
- **AND** L1 Observer 本身 SHALL 保持只读、非交易消费者

### Requirement: 审阅输出使用指标注释、可理解的任务组合覆盖和固定顺序

Observer 的 Markdown 审阅输出 SHALL 依次呈现：方法卡、命题状态和四条核心指标、职业任务组合
覆盖分布、分布尾部的完整披露、TOP10 职业、TOP10 任务、指标注释和语义限制。四个核心指标 SHALL 在文末
说明其物理含义、分子、分母或求和公式，且明确不是员工采用率。图表标题与坐标 SHALL 使用同一
中文指标名称，不得以英文内部字段替代读者可见名称。

逐职业补充指标 SHALL 命名为“职业任务组合的已确认生产化覆盖”，并在首次出现处说明：它是
指定 O*NET 任务组合中、公开 1P API cell 已满足代理标准的任务占比；未公开或无法映射的任务
留在分母，故结果只是当前公开数据可确认的覆盖，不声明存在一个可观测的“上限”。报告 SHALL
先展示职业覆盖分布，再在最新月份列出所有达到 50% 或更高覆盖的职业及其已确认任务数和 O*NET 任务总数。
该清单 SHALL 标作分布尾部的完整披露，不得称为典型职业、职业采用率或岗位生产化证据；典型使用单元仅由独立的
Usage Share 排名 TOP10 表呈现。

#### Scenario: 输出四个指标的注释
- **WHEN** 用户请求 AI 生产化审阅 Markdown
- **THEN** 文末 SHALL 逐项解释职业/任务可见单元生产化率及职业/任务生产化流量份额的计算公式
- **AND** SHALL 说明职业与任务是同一产品流量的两种分类视角，不得相加

#### Scenario: 高覆盖职业清单
- **WHEN** 最新月份存在职业任务组合已确认生产化覆盖不低于 50% 的职业
- **THEN** 输出 SHALL 列出职业名称、覆盖率、已确认生产化任务数和 O*NET 任务总数
- **AND** SHALL 标明该表属于 taxonomy-dependent 补充证据，不改变核心命题状态

### Requirement: Evidence 支持按层单独运行注册的只读 Observer

系统 SHALL 提供只读 `ats evidence layer --sector <sector> --layer <layer>` 入口，要求显式给出
sector 与 layer。命令 SHALL 从 sector 配置验证 layer 存在，并只运行该 layer 的
`evidence_observers` 中已启用的 Observer，
将 scope 写入 packet、Markdown、输出路径和 snapshot purpose。它 SHALL NOT 为了方便而运行同一 sector
的全部 L1–L8 命题。

`evidence_observers` SHALL 与现有 Chain `claims` 分离：前者只声明受治理、只读的 data Observer，后者
仍只服务公司证人、归因与 Chain workflow。当前 `ai_core_production_workflow_penetration` 以 runner
`ai_production_penetration` 声明在 `ai_hardware/L1_app`，仅在该层运行时调用
`observe_ai_production_penetration` 和受治理 DataProducts。`ats evidence ai-production` SHALL 作为
该 claim 的兼容快捷入口，但必须先经同一配置声明解析。未注册的有效 layer SHALL 返回
`no_registered_observers`；请求不属于该 layer 的 claim SHALL 返回 `claim_not_registered_for_scope`。
所有路径不接入 corroboration、评分、PEAD、Chief、组合、风险或执行，也不得改变现有
`ats evidence observe`、`ats evidence report` 或其他 Observer 的行为。默认 Markdown 运行 SHALL
持久化一份层级审阅文档，并打印其路径；调用者 MAY 用 `--output` 和 `--chart-dir` 覆盖路径。

#### Scenario: 单独运行 L1 应用层观察
- **WHEN** 用户运行 `ats evidence layer --sector ai_hardware --layer L1_app`
- **THEN** 系统 SHALL 只返回该层注册的专属方法卡、结构化 packet 或 Markdown 审阅报告及该 scope
- **AND** SHALL 持久化报告路径，且结果 SHALL 可由 manifest 重放

#### Scenario: 层配置是 Observer 注册真源
- **WHEN** `config/sectors/ai_hardware.yaml` 的 `L1_app.evidence_observers` 声明
  `ai_core_production_workflow_penetration` / `ai_production_penetration`
- **THEN** `ats evidence layer --sector ai_hardware --layer L1_app` SHALL 运行该 runner
- **AND** 删除或禁用该声明后 SHALL 返回 `no_registered_observers`，不得因 Python 中残留的 scope 映射继续运行
- **AND** 该声明 SHALL NOT 自动进入 `L1_app.claims`、Chain corroboration 或 sector analyst 命题

#### Scenario: 已配置但暂未注册的层
- **WHEN** 用户运行 `ats evidence layer --sector ai_hardware --layer L2_compute`
- **THEN** 系统 SHALL 返回 `no_registered_observers`
- **AND** SHALL NOT 运行 L1 或其他层的命题来替代
