## Purpose

定义 L1 AI 应用层“商业化能力”的独立、可证伪 Evidence Observer，并以 Frontier AI Labs 收入水平与趋势作为首个证据部分，同时明确尚未覆盖留存、单位经济和完整商业模式的边界。

## ADDED Requirements

### Requirement: L1 商业化能力必须作为独立命题运行
系统 SHALL 为 `ai_hardware/L1_app` 注册独立于生产化与应用扩散的商业化能力 Observer，其总命题为：“模型公司能否把持续使用转化为高质量、可留存且具有合理单位经济的收入，并形成可持续商业模式？”首版 SHALL 只实现 `frontier_labs_revenue_scale_and_trend` 证据部分，固定追踪问题为：“OpenAI、Anthropic 等 Frontier AI Labs 是否持续把模型使用转化为规模化收入增长？”

#### Scenario: 按层运行 L1 Evidence
- **WHEN** 用户独立运行 `ai_hardware/L1_app` Evidence workflow
- **THEN** 系统 SHALL 分别运行生产化 Observer 与商业化 Observer，并产出独立 claim packets 和报告段落
- **AND** SHALL NOT 将 BTOS、RPS、Ramp 或 Anthropic Economic Index 改归入商业化收入证据

### Requirement: 首版结论必须区分收入兑现与完整商业模式
Observer SHALL 对收入证据返回 `expanding`、`stable`、`contracting`、`mixed`、`insufficient_history` 或 `unavailable`，并披露使用的可比点数、期间、公司和计量口径。由于首版没有完整留存、毛利、推理成本、客户集中度和获客效率，商业化总命题 SHALL 返回明确的部分证据结论，例如 `revenue_monetization_expanding_but_economics_unverified`，不得仅凭 ARR/run-rate 增长宣称商业模式可持续或单位经济成立。

#### Scenario: 两家公司年化收入持续增长
- **WHEN** OpenAI 与 Anthropic 的可比 annualized run-rate 序列均满足扩大规则
- **THEN** 收入部分 MAY 判定为 `expanding`
- **AND** 商业化总命题 SHALL 同时标明 retention 与 unit economics 尚未验证

#### Scenario: 公司口径不可比
- **WHEN** 最新数值分别为 reported ARR 与 annualized run rate，或观察身份不同且无法完成可比化
- **THEN** Observer SHALL 并列展示但不得计算公司间绝对差额、倍数或排名
- **AND** 状态 SHALL 标记 comparability warning

### Requirement: 趋势计算只使用可比离散观察且不得填补历史
趋势 SHALL 按公司、计量口径、观察身份和 methodology regime 分组，只使用来源明确的实际离散期间；不得插值、前向填充或把披露日当成收入参考期。历史不足时 SHALL 返回 `insufficient_history`，但可展示最新水平和离散历史点。

#### Scenario: 2026 年上半年存在月份缺口
- **WHEN** TickerTrends 仅明确披露 1 月、4 月和 6 月等离散点
- **THEN** 图表 SHALL 仅绘制这些点并使线段/标记明确表达非连续披露
- **AND** 趋势说明 SHALL 披露缺口，不得声称拥有完整月度收入数据

### Requirement: 正式报告必须提供收入结论、曲线、表格和方法注释
商业化报告 SHALL 依次展示：命题与当前判断、最新收入水平、历史变化、OpenAI/Anthropic 收入曲线、来源与可比性说明、指标公式、数据缺口及尚未覆盖的商业化维度。图表、表格、CSV/JSON、sidecar、Agent context 和 packet SHALL 使用同一 observation set 与 rows hash；中文图表 SHALL 使用可验证字体并通过渲染检查。

#### Scenario: 正式报告生成成功
- **WHEN** 至少一个实验室具有通过质量门的最新值和历史值
- **THEN** 报告 SHALL 内嵌可读的收入趋势图并列出每个点的来源、身份、口径和期间
- **AND** 读者 SHALL 能区分 Sacra 最新数据与 TickerTrends 2026 年上半年补充点

### Requirement: Agent context 必须紧凑但保留结论所需证据
Observer SHALL 输出独立机器 packet 和 compact Agent context，包含 claim/version、收入轴状态、每家公司 latest value/period/metric identity、可比历史摘要、facts、warnings、observation IDs、manifest 和 lineage pointer。原始网页全文 SHALL NOT 平铺到 Agent context。

#### Scenario: Agent 消费商业化结论
- **WHEN** Agent 请求 L1 商业化能力 context
- **THEN** 它 SHALL 获得足以复述结论及限制的结构化摘要
- **AND** SHALL 能通过 lineage 追溯到具体来源快照和引用

### Requirement: 测试通过后直接进入平台正式报告
本 Observer 和数据源 SHALL 在采集、解析、质量、查询、趋势、报告、图表、lineage、manifest replay 和隔离回归全部通过后发布为 `platform` 并加入正式 L1 报告；不得保留需要额外 release-check 才可见的 `shadow` 或 `experimental` 模式。任何未通过质量门的新 revision SHALL 被隔离，且不得破坏已发布的最近有效报告。

#### Scenario: 首次端到端验收通过
- **WHEN** 所有规定测试和离线重放通过
- **THEN** `ats evidence layer --sector ai_hardware --layer L1_app` SHALL 默认产出商业化正式报告
- **AND** 无需额外 shadow promotion 命令

### Requirement: 新 Observer 不得影响其他层和既有生产化判断
商业化 Observer SHALL 只挂载到 `ai_hardware/L1_app`，不修改其他 sector/layer Observer，也不进入 Chain、评分、组合、风控或交易 workflow。它的不可用、冲突或趋势状态 SHALL NOT 改写现有生产化与应用扩散 Observer 的 claim/version/overall status。

#### Scenario: 商业化数据源不可用
- **WHEN** Sacra 与历史补充均无法提供有效数据
- **THEN** 商业化 Observer SHALL 返回 `unavailable` 及原因
- **AND** 现有生产化报告 SHALL 继续按原数据和规则生成

