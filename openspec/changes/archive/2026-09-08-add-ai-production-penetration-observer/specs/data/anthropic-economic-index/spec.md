## ADDED Requirements

### Requirement: 核心生产流程资格使用固定且版本化的代理标准

系统 SHALL 提供 `core_production_workflow_proxy_v1`，仅对 `source_product=1p_api` 的月度 SOC detailed occupation 与 O*NET task cell 判定资格。一个 cell 只有在同产品、同期间、同 methodology 下同时具有 `Usage Share > 0`、`Work Use Share >= 80%`、`Automation Share >= 80%` 和 `Directive Share >= 50%` 时才 SHALL 标记为 qualified。资格是分析代理而非 Anthropic 官方指标，SHALL NOT 被称为已确认生产环境、持续运行工作流或员工采用。

#### Scenario: 任务满足全部阈值
- **WHEN** 一个 1P API task cell 的 Usage、Work、Automation 和 Directive 分别为 `1.25%`、`90%`、`95%` 和 `70%`
- **THEN** 系统 SHALL 将其标记为 `qualified` 并返回 `threshold_version=core_production_workflow_proxy_v1`
- **AND** SHALL 返回四个输入 observation IDs 与逐项阈值比较

#### Scenario: 单项指标未达阈值
- **WHEN** 一个 cell 的 Work 和 Automation 达标但 Directive 为 `49.99%`
- **THEN** 系统 SHALL 将其标记为 `not_qualified` 并指出 `directive_below_threshold`
- **AND** SHALL NOT 对缺失或不达标输入进行补值、四舍五入后再判定或跨产品替代

#### Scenario: Claude.ai 请求核心资格
- **WHEN** 消费者请求以 Claude.ai 计算核心生产流程资格
- **THEN** 系统 SHALL 返回 `unsupported_source_product` 或等价明确状态
- **AND** SHALL NOT 将 Claude.ai observation 纳入核心指标分子或分母

### Requirement: 数据产品同时提供职业与任务的可见单元生产化率

系统 SHALL 分别在 detailed occupation 和 O*NET task grain 计算可见单元生产化率：分子为当月达到核心生产流程资格且具有公开 cell 的单元数，分母为当月同 grain 具有公开 Usage cell 的单元数。结果 SHALL 返回 grain、qualified count、visible count、百分比、期间、阈值版本、methodology、质量状态和所有输入 observation IDs。

#### Scenario: 计算职业可见单元生产化率
- **WHEN** 指定月份有 690 个公开 detailed occupation cell，其中 322 个达到核心资格
- **THEN** 系统 SHALL 返回 `322 / 690 = 46.666...%`
- **AND** SHALL 将该值命名为可见职业单元生产化率，而非职业员工采用率

#### Scenario: 隐私过滤的单元不进入可见分母
- **WHEN** taxonomy 中存在一个职业或任务但当月没有公开 Usage cell
- **THEN** 系统 SHALL 将其记入覆盖诊断而非可见分母
- **AND** SHALL NOT 将其视为零 Usage、未采用或不达标

### Requirement: 数据产品同时提供职业与任务的生产化流量份额

系统 SHALL 分别在 detailed occupation 和 O*NET task grain 计算生产化流量份额，即对当月达到核心资格的公开 cell 的 Provider `Usage Share` 求和。结果 SHALL 返回 qualified cell count、可见 Usage Share 合计、qualified Usage Share 合计、占全部产品流量的原始份额、占已发布 cell 流量的条件份额及完整 lineage；不同 grain 的结果 SHALL 独立解释，SHALL NOT 相加或平均。

#### Scenario: 计算任务生产化流量份额
- **WHEN** 达标 tasks 的 Usage Share 合计为 `68.66%`，全部公开 task cells 的 Usage Share 合计为 `85.00%`
- **THEN** 系统 SHALL 返回总产品流量口径 `68.66%` 和已发布 task-cell 条件口径 `80.776...%`
- **AND** SHALL 说明未发布 cell 使公开 task Usage Share 不一定加总到 100%

#### Scenario: 不把边际比例相乘
- **WHEN** 计算生产化流量份额
- **THEN** 系统 SHALL 只对已通过资格筛选的 Provider Usage Share 求和
- **AND** SHALL NOT 将 Usage、Work、Automation 和 Directive 相乘并冒充同一记录同时满足四项条件的联合概率

### Requirement: Taxonomy 映射只作为覆盖质量诊断

系统 SHALL 报告 monthly task ID 到公开 O*NET taxonomy 的映射覆盖率、mapped/unmapped task counts、taxonomy version 和缺失原因。Taxonomy 映射 SHALL 支持职业内生产化任务覆盖下限及其职业分布，但这些补充指标 SHALL NOT 改变四条核心序列、成为总体趋势判定输入或被解释为员工采用率。

#### Scenario: 月度任务新于公开 taxonomy
- **WHEN** 月度数据有 2,295 个可见 task cells 而仅 1,793 个 Task IDs 能连接到指定 taxonomy
- **THEN** 系统 SHALL 返回 mapping coverage `1793 / 2295`、502 个 unmapped tasks 和 taxonomy version warning
- **AND** 核心可见任务生产化率 SHALL 继续以全部 2,295 个公开 task cells 为分母

#### Scenario: Taxonomy 滞后影响补充覆盖指标
- **WHEN** 存在达到生产化标准但无法连接到指定 taxonomy 的月度 Task ID
- **THEN** 系统 SHALL 将其记为 `unmapped_qualified_task` 并公开数量和 Task IDs
- **AND** SHALL NOT 将其分配给任何职业、静默丢弃 warning 或抬高职业内任务覆盖下限

### Requirement: 数据产品提供逐职业的生产化任务覆盖下限

系统 SHALL 对指定期间和 taxonomy version 中每个至少具有一个 task relation 的 detailed occupation 计算 `occupation_production_task_coverage_lower_bound`，单位为 percent。分母 SHALL 为该职业在指定 taxonomy 中关联的去重任务总数；分子 SHALL 为这些关联任务中存在同期间 1P API task cell 且满足 `core_production_workflow_proxy_v1` 的去重任务数。一个任务关联多个职业时 SHALL 在每个职业内部各计一次，但在同一职业内部不得重复计数。结果 SHALL 返回职业 ID/名称、期间、qualified mapped task count、taxonomy task count、覆盖率、未映射 qualified task 诊断、taxonomy/threshold/methodology/derivation versions、quality status、observation IDs 和 relation IDs。

该指标 SHALL 标记为 lower bound：taxonomy 中没有公开月度 cell 的任务保留在分母且不进入分子；月度 qualified 但无法映射的任务不能归属职业。系统 SHALL NOT 将该指标称为员工采用率、职业自动化率或无偏的真实任务渗透率。

#### Scenario: 计算单个职业的生产化任务覆盖下限
- **WHEN** 一个职业在指定 taxonomy 中关联 20 个去重任务，其中 5 个任务在指定月份具有 1P API cell 且满足核心生产化标准
- **THEN** 系统 SHALL 返回 numerator `5`、denominator `20` 和 coverage `25%`
- **AND** SHALL 返回用于确认 5 个任务资格的 observations 及连接 20 个任务的 taxonomy relations

#### Scenario: Taxonomy 中的未观察任务进入分母
- **WHEN** 一个职业关联的 taxonomy task 在当月没有公开 Usage cell
- **THEN** 该任务 SHALL 保留在职业的 taxonomy task denominator 中且不进入 qualified numerator
- **AND** 输出 SHALL 说明缺失可能来自无使用、隐私过滤或未发布，因此结果是覆盖下限而非零采用判断

#### Scenario: 职业没有可用任务分母
- **WHEN** 指定 taxonomy version 中一个职业没有任何有效 task relation
- **THEN** 系统 SHALL 返回 `taxonomy_denominator_unavailable`
- **AND** SHALL NOT 返回零覆盖率或将该职业纳入分布统计

### Requirement: 数据产品提供职业生产化任务覆盖分布

系统 SHALL 基于同期间、同 taxonomy version 的逐职业 `occupation_production_task_coverage_lower_bound` 生成互补累计分布（CCDF）。每个分布点 SHALL 返回 minimum coverage threshold、达到或超过该阈值的职业数、eligible occupation 总数及职业占比。系统 SHALL 返回全部不同覆盖率对应的确定性曲线点，并至少显式返回 `10%`、`25%`、`50%`、`75%` 和 `100%` 五个 landmark。分布分母 SHALL 只包含具有有效 taxonomy task denominator 的 detailed occupations；排序、并列值和边界比较 SHALL 可复算。

该分布 SHALL 作为职业内部任务扩散的补充证据，不进入原有四条核心序列的 breadth/depth/overall 状态。跨月比较仅 SHALL 在 taxonomy、methodology、threshold 和 derivation versions 均相同时进行。

#### Scenario: 计算 Figure 4 风格 landmark
- **WHEN** 100 个 eligible occupations 中有 36 个职业的生产化任务覆盖下限至少为 `25%`
- **THEN** `minimum_coverage_pct=25` 的分布点 SHALL 返回 occupation count `36` 和 occupation share `36%`
- **AND** 该值 SHALL 被表述为“36% 的 eligible occupations 至少有 25% 的 taxonomy tasks 达到生产化代理标准”

#### Scenario: Taxonomy 版本变化
- **WHEN** 相邻月份的 taxonomy version 不同
- **THEN** 系统 SHALL 返回两个独立的职业覆盖分布并标记 `taxonomy_changed`
- **AND** SHALL NOT 将曲线位移或 landmark 差异表述为同口径趋势

### Requirement: 生产化趋势表可复现且保持历史与方法口径

系统 SHALL 提供按 period 和 grain 排列的结构化结果，并可返回 Pandas DataFrame；每行 SHALL 至少包含 `period`、`grain`、`visible_count`、`qualified_count`、`visible_production_rate`、`production_traffic_share`、`published_usage_share`、`source_product`、`threshold_version`、`methodology_version`、`quality_status` 和 lineage。月度变化只 SHALL 在连续自然月、相同 methodology 与相同 threshold version 之间计算。

#### Scenario: 返回职业与任务趋势表
- **WHEN** 消费者请求 1P API 的生产化趋势数据
- **THEN** 系统 SHALL 返回 occupation 和 task 两个 grain 的同构行集合及 DataFrame 表示
- **AND** SQL/结构化结果/DataFrame 对相同输入 SHALL 在计数、比例、期间和 observation IDs 上一致

#### Scenario: 方法版本发生变化
- **WHEN** 相邻月份的 Anthropic methodology version 不同
- **THEN** 系统 SHALL 将跨版本变化标记为 `methodology_changed`
- **AND** SHALL NOT 把两个期间连接为连续趋势

### Requirement: TOPN 只在达标单元中按 Usage Share 排名

系统 SHALL 在 occupation 与 task grain 分别从最新期间 qualified cells 中按 Provider Usage Share 降序生成 TOPN，并返回排名、实体稳定 ID、名称、Usage、Work、Automation、Directive、各指标相对上个可比月的百分点变化、资格状态、质量状态和 lineage。并列值 SHALL 以稳定 entity ID 排序。

#### Scenario: 生成任务 TOP10
- **WHEN** 消费者请求指定月份的 production task TOP10
- **THEN** 系统 SHALL 只排序满足 `core_production_workflow_proxy_v1` 的 task cells
- **AND** 每一行 SHALL 可追溯到用于资格、排名和月度变化的 observations

#### Scenario: 上月缺少同一单元
- **WHEN** TOPN 单元在上一个自然月没有公开 cell
- **THEN** 对应变化 SHALL 返回 `insufficient_history` 或 `not_published_or_privacy_filtered`
- **AND** SHALL NOT 把上月值视为零

### Requirement: 为审阅层提供职业任务组合已确认覆盖清单

数据产品 SHALL 为最新指定期间返回可按覆盖率降序筛选的职业任务组合已确认生产化覆盖记录，
包括职业稳定 ID/名称、已确认生产化任务数、O*NET 任务总数、覆盖率、taxonomy version、质量
状态和完整 observation/relation lineage。该字段可保留兼容性内部 ID，但面向用户的说明 SHALL
使用“已确认生产化覆盖”，而不是暗示存在可观测的覆盖上限。

#### Scenario: 筛选高覆盖职业
- **WHEN** 审阅 consumer 请求覆盖率不低于 50% 的职业
- **THEN** DataProducts SHALL 返回该门槛以上的确定性职业清单及分子/分母
- **AND** SHALL NOT 将此补充指标混入核心四条序列
