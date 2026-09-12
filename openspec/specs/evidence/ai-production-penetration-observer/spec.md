# evidence/ai-production-penetration-observer Specification

## Purpose

定义 AI 应用层如何以 Anthropic 1P API 的受治理数据形成可证伪、可重放、不会被误读为员工采用率的生产化与应用扩散 Evidence 结论。

## Requirements

### Requirement: Observer 使用固定且隔离的生产化命题
Observer SHALL 保留稳定 claim identity，并以 `claim_definition_version=v2` 使用命题：“AI 的企业采用广度、员工持续使用和任务生产化深度是否同步扩大，从局部试验走向可重复的生产工作流？”它 SHALL 只消费受治理的 AI adoption evidence bundle，分别读取 BTOS 企业广度、RPS 员工持续使用和 Anthropic 1P API 任务生产化，输出 snapshot manifest 与 lineage；不得直接读取 Provider、物理表或自行拼接跨源数值。ONS BICS SHALL NOT 进入该 Observer。

#### Scenario: 运行固定命题
- **WHEN** L1 AI 应用层运行该 Observer
- **THEN** 输出 SHALL 具有固定命题和可重放的受治理输入清单
- **AND** SHALL NOT 声称 Usage Share 是员工或企业采用率

#### Scenario: 运行扩展后的固定命题
- **WHEN** L1 AI 应用层运行该 Observer
- **THEN** 输出 SHALL 具有 v2 命题、三条证据轴及可重放的受治理输入清单
- **AND** SHALL NOT 将企业比例、就业人口比例或 Claude Usage Share 互相改写或融合

### Requirement: Observer 保留四项透明指标与审慎趋势状态
Observer SHALL 在任务生产化轴继续分别陈述 occupation/task 可见单元生产化率与生产化流量份额，同时分别陈述企业采用广度、员工持续使用和组织嵌入深度的原始及透明派生指标；不得以不透明综合分数替代。每条轴 SHALL 按其自身 cadence、连续期间和 methodology regime 判定趋势，整体判断 SHALL 基于方向性印证和冲突矩阵，不要求跨源具有同一期间，也不得将低频快照插值。历史不足时 SHALL 返回 `insufficient_history`，可展示可比期间差异但不得称为持续、加速或放缓。

#### Scenario: 两月数据且广度与深度不同
- **WHEN** 只有两个可比月且职业/任务广度方向不同、流量份额提高
- **THEN** 输出 SHALL 陈述月度比较中的“深度提高、广度表现混合”
- **AND** SHALL NOT 判定 `penetration_expanding`

#### Scenario: 企业广度上升但使用深度未上升
- **WHEN** BTOS 可比趋势上升，而 RPS daily/assisted-hours 和 Anthropic production metrics 未同步改善
- **THEN** 输出 SHALL 判定或描述为 `breadth_without_confirmed_depth`
- **AND** SHALL NOT 判定整体生产化同步扩大

#### Scenario: 三轴具有不同最新期间
- **WHEN** 双周、季度、条件快照和 release-event 数据的 latest periods 不一致
- **THEN** Observer SHALL 按来源显示真实期间和 asynchronous-period warning
- **AND** SHALL NOT 前向填充或把它们伪装为同月观测

### Requirement: Observer 提供专属方法卡、覆盖分布和结构化审阅
本 Observer 的成功或部分成功 packet SHALL 提供专属中文 `methodology_card`，按来源列出统计主体、分母、地区、技术范围、期间/release、参与统计的层级/维度/单元数量、问题或 taxonomy regime、质量、derivation、manifest 和不可推断事项；其他 Observer SHALL 不被要求提供该字段。Markdown SHALL 依次显示命题判断、三轴总览、跨源印证/冲突、企业广度、员工持续使用、Anthropic 职业任务组合覆盖分布与四项生产化指标，最后展示经过名录映射的 TOPN、公式、限制和来源。图表与表格 SHALL 使用一致的中文指标名、数据和 observation identities；算法判定轨迹保留在 packet/lineage，SHALL NOT 逐项倾倒到人类正文。

#### Scenario: 50% 覆盖职业
- **WHEN** 最新月份有职业的已确认任务组合覆盖至少为 50%
- **THEN** 输出 SHALL 在分布后列出职业、分子、分母与覆盖率，并标为 taxonomy-dependent 尾部披露
- **AND** SHALL NOT 将其称为典型职业、员工覆盖、岗位替代或完整工作流证据

#### Scenario: 人类审阅多来源报告
- **WHEN** Observer 成功生成 review 输出
- **THEN** 读者 SHALL 能从总览进入每条轴，看到值、变化、分母、期间、来源和方法限制
- **AND** TOP occupations/tasks SHALL 位于总体分布和覆盖之后，且不得替代总体判断

#### Scenario: ONS 只有单次深度快照
- **WHEN** 最新 ONS AI 深度问题没有足够同 regime 历史
- **THEN** 方法卡和正文 SHALL 将其显示为 UK supplemental snapshot 与 `insufficient_history`
- **AND** SHALL NOT 将其加入连续趋势判断或外推美国企业

### Requirement: Evidence 支持由层配置驱动的独立运行

系统 SHALL 提供 `ats evidence layer --sector <sector> --layer <layer>`，只运行该 layer 的已启用
`evidence_observers` 声明，默认持久化 Markdown 和图表并打印路径。该声明 SHALL 与 Chain `claims` 分离：
前者选择受治理只读数据 Observer，后者仍服务公司证人、归因与 Chain。有效但无声明 layer SHALL 返回
`no_registered_observers`；未知 runner、重复 claim 或不匹配的 claim/runner SHALL 返回明确配置错误；命令
不得运行其他 L1–L8 层来替代。

#### Scenario: L1 配置声明启用或停用
- **WHEN** `ai_hardware/L1_app.evidence_observers` 启用或停用
  `ai_core_production_workflow_penetration / ai_production_penetration`
- **THEN** `ats evidence layer` SHALL 相应运行该命题或返回 `no_registered_observers`
- **AND** SHALL NOT 自动把该声明加入 Chain、PEAD、Chief、组合、风控或交易 workflow

### Requirement: 趋势状态必须表达中期方向而非逐期单调性
各来源趋势 SHALL 按来源原生时间顺序计算，并至少披露起点、终点、净变化、线性斜率、相邻变化方向一致率、可比期间数、实质变化阈值和判断步骤。单个非实质或不改变整体方向的小幅回撤 SHALL NOT 单独导致 `mixed`；只有净变化不明确、线性方向与端点方向冲突或正负实质变化均占显著比例时才可判为 `mixed`。BTOS SHALL 在可用时同时披露 standard error，状态 SHALL 明确是描述性趋势而非统计显著性结论。

#### Scenario: RPS 序列总体上升且中间一次小幅回撤
- **WHEN** 起点至终点显著上升、线性斜率为正且多数相邻变化为上升
- **THEN** 趋势 SHALL 判为 `expanding`
- **AND** 判断说明 SHALL 披露该次回撤而不是将整个序列标为 `mixed`

### Requirement: 人类报告必须内嵌受治理图表并解释来源期间
Markdown SHALL 在相关正文段落内嵌有分析价值的 PNG，并提供对应 CSV、JSON 和 sidecar 链接。BTOS 波次 SHALL 按数值或真实参考期排序，横轴 SHALL 显示四位年份的调查参考期日期且不显示内部波次号；不得按字符串将 wave 100–107 排在 wave 88–99 之前。状态矩阵图 SHALL NOT 输出。机器状态码 SHALL 同时提供中文解释、变量定义、数据点和方法说明。

#### Scenario: 生成 BTOS 历史图
- **WHEN** wave 88–107 均存在且 renderer 成功
- **THEN** 图表 SHALL 按真实调查时间递增且正文 SHALL 显示该图
- **AND** 读者 SHALL 能从报告访问生成图所用表格与 sidecar，且图中不显示内部 W 波次号

### Requirement: 三来源方法与公式完整披露
人类报告 SHALL 分别披露 BTOS、RPS/FRED 和 Anthropic 的发布机构/入口、统计主体、分母、地区、期间频率、参与统计维度和单元数量、headline 指标定义与公式。Anthropic 部分 SHALL 明确 occupation/task 两种分类粒度、可见与达标单元数、Usage Share 分母及生产化阈值。TOP occupation SHALL 只展示能够由受治理名录映射出职业名称的实体；仅有 SOC code 的单元 SHALL 从 TOPN 展示中剔除但不得从底层数据和总体计算中删除。

#### Scenario: 生成三来源人类报告
- **WHEN** BTOS、RPS 和 Anthropic 数据均可用
- **THEN** 报告 SHALL 为三个来源分别披露指标、公式、发布来源、统计主体、分母、维度和数量
- **AND** SHALL NOT 输出 ONS、状态矩阵或仅含 SOC code 的 TOP occupation

### Requirement: Anthropic 四指标与职业任务覆盖必须可视化
人类报告 SHALL 使用同一组受治理派生行展示职业/任务可见单元生产化率、职业/任务生产化流量份额的最近可比月、百分点变化和双面板图。报告 SHALL 同时展示职业内已确认生产化任务覆盖 CCDF，并在覆盖分布统计之后、TOPN 示例之前嵌入。图表 SHALL NOT 将两种粒度相加或将两个月历史描述为持续趋势。

#### Scenario: Anthropic 只有两个可比月份
- **WHEN** 2026-04 与 2026-05 的 occupation/task 派生行可用
- **THEN** 报告 SHALL 展示四指标两个月数值与百分点变化，并绘制职业/任务双面板月度比较
- **AND** SHALL 绘制最新月职业内任务覆盖 CCDF，且标注历史不足以判断持续趋势

### Requirement: Observer 以规则化状态表达跨源印证和矛盾
Observer SHALL 为 enterprise breadth、worker persistence、task production 三轴分别返回 `expanding`、`stable`、`contracting`、`mixed`、`insufficient_history` 或 `unavailable`，并以公开规则生成整体状态。三轴均具有可比历史且方向同为扩大时，整体 MAY 为 `broadening_and_deepening`；只有广度改善时 SHALL 为 `breadth_without_confirmed_depth`；只有 Provider 遥测改善时 SHALL 为 `provider_telemetry_only`；证据相反 SHALL 为 `mixed_evidence`。

#### Scenario: 三轴同步扩大
- **WHEN** 企业、员工和任务三轴均在自身可比历史中扩大且无反向证据
- **THEN** 整体 MAY 返回 `broadening_and_deepening`
- **AND** SHALL NOT 将三种不同分母合成为统一渗透率

### Requirement: Observer 同时交付 Agent context 和人类可视化
同一次 Evidence 运行 SHALL 可持久化机器可读 packet、compact Agent context、中文 Markdown、表格数据、PNG 或等价静态图表及 sidecar metadata。所有产物 SHALL 共享 claim version、manifest ID、facts、warnings 和底层 observation set；可视化失败 SHALL 降级为表格而不破坏 Agent context 或事实结论。

#### Scenario: 按层独立运行 L1 Observer
- **WHEN** 用户执行 `ats evidence layer --sector ai_hardware --layer L1_app`
- **THEN** 系统 SHALL 生成可供 Agent 消费和人类审阅的同源产物并打印路径
- **AND** SHALL NOT 运行其他 layer、修改其他 Observer 方法卡或注入交易决策 workflow

### Requirement: L1 Observer 提供 Ramp 付费企业采用补充段落
现有 L1 固定命题及 BTOS/RPS/Anthropic 三轴 SHALL 保持不变。若 Ramp slice 可用，Observer SHALL 在三轴总览之后增加独立的 Ramp supplemental signal，覆盖已注册的 `adoption_overall`、`adoption_overall_models`、`adoption_sector`、`spend_per_employee_overall` 和 `model_market_share_overall`；企业规模与地理 slice 首版不发布。该段落 SHALL 不参与既有三轴整体状态计算，除非另一个独立变更显式修改 claim contract。

#### Scenario: Ramp 可用
- **WHEN** Ramp Overall 和至少一个历史月份通过质量门
- **THEN** L1 报告 SHALL 展示 Ramp 最新值、月变化、真实期间和 source scope
- **AND** 三轴 judgement SHALL 与未接入 Ramp 时保持一致

#### Scenario: Ramp 不可用
- **WHEN** API 未授权且网页导出不可读或质量门失败
- **THEN** Observer SHALL 保留三轴输出并增加 `ramp_unavailable` warning
- **AND** SHALL 不把缺失 Ramp 当作零采用或 evidence conflict

### Requirement: Ramp 方法卡必须披露网络样本和付费交易定义
L1 专属方法卡 SHALL 说明 Ramp 统计主体为 Ramp 网络中的企业 cohort，采用判定为当月 AI 产品/服务正向交易，覆盖 corporate card、invoice 和 ACH 等 Ramp 处理的付款；并披露 NAICS 分组、Token Spend Management 独立 cohort、免费工具/个人账户漏计和 Ramp 客户选择偏差，同时注明企业规模与地理 slice 未纳入首版。Ramp 公司收入、客户数或估值只能作为来源背景，不能进入 adoption 指标。

#### Scenario: 读者查看 Ramp 采用率
- **WHEN** 报告显示 Ramp 56.13% adoption
- **THEN** 方法卡 SHALL 同时显示月份、Ramp 企业分母定义和 paid-transaction 规则
- **AND** SHALL 禁止读者将其解读为全美国企业或员工采用率

### Requirement: Ramp 固定第四个追踪命题与五个可视化 scope
L1 Observer SHALL 将 Ramp 固定为独立的第四个补充命题：“AI 是否从自报使用和试验，转向真实的企业付费采购，并在行业、企业规模和模型供应商之间扩散？”该命题 SHALL 有独立的 claim id、方法卡、可用性状态和文字化结论；不得只在图表标题或 Agent context 中隐含表达。
首版 SHALL 逐一输出且一一对应以下五个 scope 的历史序列和图表：`adoption_overall`、`adoption_overall_models`、`adoption_sector`、`spend_per_employee_overall`、`model_market_share_overall`。若企业规模 slice 尚未进入采集白名单，报告 SHALL 明确将“企业规模”标为待观测缺口，不得用 Overall 或行业值替代。

#### Scenario: Ramp 五个 scope 可用
- **WHEN** 五个 scope 均有至少一个通过质量门的官方导出
- **THEN** 报告 SHALL 生成五张同名 scope 图表、五张数据表和五个 sidecar
- **AND** adoption、spend 与 model share 图表 SHALL 优先绘制所有可见历史期间，而不是只绘制最新月份
- **AND** 报告 SHALL 在图表前给出 Ramp adoption 的最新值、历史期数、变化和 source-native 统计含义

#### Scenario: Ramp 历史不足或企业规模未发布
- **WHEN** 某 scope 只有一个期间，或企业规模不在首版白名单
- **THEN** 报告 SHALL 标记 `insufficient_history`/`not_published`，不得伪造趋势或补值
