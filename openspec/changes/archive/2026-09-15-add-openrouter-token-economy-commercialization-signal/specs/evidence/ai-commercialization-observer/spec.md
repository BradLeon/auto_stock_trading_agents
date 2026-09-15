## MODIFIED Requirements

### Requirement: L1 商业化能力必须作为独立命题运行
系统 SHALL 为 `ai_hardware/L1_app` 注册独立于生产化与应用扩散的商业化能力 Observer，其总命题为：“模型公司能否把持续使用转化为高质量、可留存且具有合理单位经济的收入，并形成可持续商业模式？”Observer SHALL 实现两个相互独立的证据部分：`frontier_labs_revenue_scale_and_trend`，固定问题为“OpenAI、Anthropic 等 Frontier AI Labs 是否持续把模型使用转化为规模化收入增长？”；以及 `openrouter_routed_usage_and_competition`，固定问题为“第三方模型路由渠道的真实调用规模是否持续扩大，需求是否在模型厂商之间形成可持续且可解释的竞争格局？”

#### Scenario: 按层运行 L1 Evidence
- **WHEN** 用户独立运行 `ai_hardware/L1_app` Evidence workflow
- **THEN** 系统 SHALL 分别运行生产化 Observer 与商业化 Observer，并产出独立 claim packets 和报告段落
- **AND** 商业化 Observer SHALL 分别展示 Frontier Labs 收入和 OpenRouter 路由用量子结论
- **AND** SHALL NOT 将 BTOS、RPS、Ramp 或 Anthropic Economic Index 改归入商业化收入证据

### Requirement: 正式报告必须提供收入结论、曲线、表格和方法注释
商业化报告 SHALL 依次展示：总命题与当前证据边界、Frontier Labs 最新收入水平与历史变化、OpenAI/Anthropic 收入曲线、OpenRouter 公共路由用量与竞争格局结论、OpenRouter 规模/份额/榜单/浓度可视化、跨证据印证或冲突、来源与可比性说明、指标公式、数据缺口及尚未覆盖的商业化维度。图表、表格、CSV/JSON、sidecar、Agent context 和 packet SHALL 使用各自同一 observation set 与 rows hash；中文图表 SHALL 使用可验证字体并通过渲染检查。

#### Scenario: 正式报告生成成功
- **WHEN** 至少一个实验室具有通过质量门的最新值和历史值
- **THEN** 报告 SHALL 内嵌可读的收入趋势图并列出每个点的来源、身份、口径和期间
- **AND** 读者 SHALL 能区分 Sacra 最新数据与 TickerTrends 2026 年上半年补充点

#### Scenario: 两类正式证据均可用
- **WHEN** 至少一个实验室收入序列和 OpenRouter 一个完整周均通过质量门
- **THEN** 报告 SHALL 内嵌可读的收入趋势图与 OpenRouter 四类可视化，并列出每个结论的来源、身份、口径和期间
- **AND** 读者 SHALL 能区分公司收入、OpenRouter token 用量、厂商 token 份额和模型榜单

#### Scenario: OpenRouter 不可用
- **WHEN** OpenRouter 凭据缺失、接口失败或最新 revision 未通过质量门
- **THEN** 报告 SHALL 使用最近有效 vintage 或返回 `openrouter_unavailable/stale` warning
- **AND** Frontier Labs 收入段落 SHALL 继续生成且不得把缺失解释为零需求

## ADDED Requirements

### Requirement: OpenRouter 必须作为商业化能力的第二个独立证据部分
Observer SHALL 为 `openrouter_routed_usage_and_competition` 返回 `expanding`、`stable`、`contracting`、`mixed`、`insufficient_history` 或 `unavailable`，并分别陈述规模状态、厂商结构状态与覆盖限制。该部分 SHALL 被解释为第三方路由渠道的使用需求及竞争证据，不得被解释为 Labs 收入、利润、企业采用率或全行业市场份额。

#### Scenario: 总量扩大且竞争结构变化
- **WHEN** OpenRouter 公共路由 token 的完整周趋势扩大，同时作者份额和浓度发生显著变化
- **THEN** 报告 SHALL 判定用量规模状态并单独说明份额迁移、绝对量和集中度
- **AND** SHALL NOT 将作者 token share 命名为收入 market share

#### Scenario: 历史不足
- **WHEN** 只有少于八个完整可比周
- **THEN** OpenRouter 子结论 SHALL 返回 `insufficient_history`
- **AND** MAY 展示最新水平和榜单，但不得宣称持续扩大、加速或份额趋势确立

### Requirement: 收入与路由用量只能方向性综合
商业化 Observer SHALL 保留收入和 OpenRouter 两类证据的独立状态、期间、分母及来源，并使用规则化矩阵生成总证据说明。收入扩大且 OpenRouter 用量扩大时 MAY 标记 `revenue_and_routed_demand_expanding`；方向冲突时 SHALL 标记 `mixed_commercialization_evidence`；任一类不可用时 SHALL 返回另一类的部分证据结论。系统 SHALL NOT 对收入和 token 建立无依据的换算、相关性因果或综合分数。

#### Scenario: 收入与路由需求同步扩大
- **WHEN** Frontier Labs 收入和 OpenRouter 路由用量均在自身可比历史中为 `expanding`
- **THEN** 总证据说明 MAY 判定收入兑现与第三方路由需求方向一致
- **AND** SHALL 继续注明留存、单位经济和全市场覆盖未验证

#### Scenario: 收入扩大但 OpenRouter 用量收缩
- **WHEN** 收入部分为 `expanding` 而 OpenRouter 部分为 `contracting`
- **THEN** 总证据说明 SHALL 返回 `mixed_commercialization_evidence`
- **AND** SHALL 提示渠道迁移、直连 API、价格、免费流量和样本构成等候选解释，而不得断言收入数据错误

### Requirement: OpenRouter 正式报告必须提供四类直观可视化
报告 SHALL 以相同 accepted observation set 输出：（1）完整周公共路由总 token 柱线图与 4 周均线；（2）相同作者集合的 100% token 份额堆叠图；（3）默认从 2026-01-01 起、按累计 token 选取稳定模型集合的 token 量堆叠与逐周排名变化图（同一模型版本的发布日期后缀合并）；（4）Top-3、Top-5 与 HHI 浓度趋势。图表 SHALL 使用稀疏日期刻度、可辨识配色、Top-N+Other 规则、真实单位和可见来源注释，并附 CSV/JSON 与 sidecar。

#### Scenario: 生成 OpenRouter 可视化
- **WHEN** 至少八个完整周和最新榜单通过质量门
- **THEN** 报告 SHALL 内嵌四类图表并在图前给出文字化规模、份额、模型排名/绝对量与浓度摘要
- **AND** 模型图 SHALL 同时表达每周 token 量和相对全部模型的排名变化

#### Scenario: 中文字体不可用
- **WHEN** renderer 无法验证选定中文字体或图中出现缺字占位符
- **THEN** 图表 SHALL 质量失败并降级为表格
- **AND** SHALL NOT 发布乱码图片

### Requirement: OpenRouter 方法卡必须披露渠道和计量限制
商业化方法卡 SHALL 披露 OpenRouter 统计范围仅为其路由的公共请求、private requests 排除、数据起点、UTC 日/周定义、Top 50+Other 结构、token 为 prompt 与 completion 合计、不同上游 tokenizer 不完全可比、免费模型与促销流量影响、直连厂商 API 和其他路由平台缺失。若使用 token share，方法卡 SHALL 明确其不是 request share、spend share 或 revenue share。

#### Scenario: 读者查看厂商份额
- **WHEN** 报告显示某作者的 token share
- **THEN** 同段 SHALL 显示 OpenRouter 渠道分母、`Other` 占比、完整周期间和 token 口径
- **AND** SHALL 禁止将其外推为全球模型厂商收入份额

### Requirement: Agent context 必须包含紧凑的 OpenRouter 证据
商业化 compact Agent context SHALL 在收入摘要之外包含 OpenRouter claim/version、最新完整周、总 token、4 周变化、Top 作者绝对量与份额、Top 模型、浓度、`Other` 比例、状态、facts、warnings、observation IDs、manifest 和 lineage pointer。它 SHALL NOT 平铺逐日模型明细或原始 API payload。

#### Scenario: Agent 消费扩展后的商业化结论
- **WHEN** Agent 请求 L1 商业化能力 context
- **THEN** 它 SHALL 获得足以区分收入证据与路由使用证据的结构化摘要
- **AND** SHALL 能通过 lineage 追溯到官方 API artifact 和派生版本

### Requirement: OpenRouter 通过验收后必须直接进入 platform 且故障隔离
OpenRouter 数据源和报告扩展 SHALL 在采集、解析、质量、查询、派生、报告、四类图表、lineage、manifest replay、中文字体和既有收入回归全部通过后发布为 `platform`；不得保留需额外 release-check 才可见的 shadow 模式。OpenRouter 的缺失、陈旧或质量失败 SHALL 只影响其自身子结论，不得破坏最近有效商业化收入报告或生产化 Observer。

#### Scenario: 首次端到端验收通过
- **WHEN** 所有规定测试和离线重放通过且运行时凭据已配置
- **THEN** `ats evidence layer --sector ai_hardware --layer L1_app` SHALL 默认包含 OpenRouter 正式段落
- **AND** 无需额外 shadow promotion 命令
