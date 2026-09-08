## 背景

建设动机见 `proposal.md`。受治理的 `ai_work_adoption` 数据集已经将 Anthropic Economic
Index 的 Claude.ai 和 1P API 月度观测保存为相互隔离的来源产品；`DataProducts` 已提供快照、
截面、时间序列、职业画像、血缘及可选的 Pandas 输出。现有 L1 工作采用 Observer 只汇总
Usage Share，尚未定义版本化的“生产化”标准，也没有生产化广度与深度的四序列面板和确定性
趋势判断。

当前受治理 release 只有 2026-04 和 2026-05 两个可比月。这足以验证截面资格与单月变化，
但不足以声称存在趋势。Anthropic 的 1P API 数据更接近应用流量代理，但每条记录依然只是一次
独立的提示词—响应交互，不能证明请求来自持续运行的工作流。Claude.ai 反映交互式产品的使用
分布，必须与 1P API 保持隔离。

Pandas 已存在于仓库的 `data` 可选依赖中；Seaborn 和 Matplotlib 尚未声明。报告目前以 Markdown
为主，snapshot manifest 已能保存消费者使用的 observation 集合。

### 阅读本文前需要知道的术语

| 术语 | 本文中的物理含义 | 简单例子 |
|---|---|---|
| `observation`（观测记录） | 一个实体、一个月份、一个指标的源数据值，是最小数值记录 | 任务 7382 在 2026-05 的 Automation Share 为 92% |
| `cell`（分析单元） | 同一个职业或任务在同一个月、同一个产品和方法口径下的一组指标 | 任务 7382 在 2026-05 的 Usage、Work、Automation、Directive 四项值合在一起 |
| `grain`（统计粒度） | 以什么类型的对象进行统计；本方案只有职业和任务两种核心粒度 | `occupation` 表示按职业统计，`task` 表示按任务统计 |
| `entity_id`（实体 ID） | 职业或任务在系统中的稳定唯一标识，不依赖名称文本 | `SOC:15-2031.00`、`ONET_TASK:7382` |
| `methodology_version`（方法版本） | Anthropic 生成该批数据时使用的统计/分类方法版本，用于判断不同月份能否直接比较 | 若 5 月改变了分类方法，就不能把 4 月到 5 月直接连接成同口径趋势 |
| `derivation_version`（派生版本） | 本系统计算公式和处理逻辑的版本，不是 Anthropic 的版本 | 修改缺失值处理规则后，从 `v1` 升为 `v2`，避免新旧计算混淆 |
| `lineage`（数据血缘） | 一个结论由哪些源文件、observations、taxonomy relations 和计算版本产生 | 某职业生产化率可追溯到参与分子/分母的全部 observation IDs |
| `snapshot manifest`（快照清单，简称 `manifest`） | 将某次分析实际使用的数据版本和血缘冻结成可重放清单 | 即使以后 Anthropic 修订 2026-05 数据，仍能复现当时报告 |
| `artifact`（来源制品） | 持久化保存的上游数据切片或其固定版本指针 | commit-pinned 的 1P API CSV 过滤切片 |
| `regime`（可比口径区间） | source product、方法版本、阈值版本和派生版本均一致的一段连续月份 | 2026-04 至 2026-06 均采用同一方法时属于同一 regime |
| `packet`（Observer 结果包） | Observer 一次运行返回的完整结构化结果，而不是网络数据包 | 命题、指标表、TOP10、状态、警告、图表描述和 manifest 的合集 |
| `methodology_card`（方法卡） | 本 AI 生产化渗透 Observer 一次运行的数据口径、样本覆盖、版本、质量和不可推断事项摘要 | 说明本次使用 1P API、哪些月份、多少可见任务、taxonomy 覆盖和为何不能推断员工采用率 |

后文保留英文代码标识符，是为了使文档与实际 API/字段一一对应；首次出现时均按上述中文含义理解。

## 目标 / 非目标

**目标：**

- 在现有受治理 observations 之上增加一个确定性、版本化的查询时派生层。
- 返回透明的职业/任务生产化广度与深度指标，并提供精确的输入血缘。
- 为 L1 Evidence Observer 提供固定、可证伪的命题和保守的趋势状态机。
- 为本 L1 AI 应用层生产化渗透 Observer 的每次输出提供可读、可审计的方法卡。
- 生成一份规范化表格数据，同时驱动 JSON、Pandas、Markdown 和图表输出。
- 提供逐职业的生产化任务覆盖下限及 Figure 4 风格的职业覆盖率分布，作为核心四序列之外的补充证据。
- 利用当前两个月的数据形成有用的月度比较，但不将其表述为持续趋势。
- 在 observation vintage、方法版本、阈值版本以及 Anthropic 后续 release 变化后仍可重放。

**非目标：**

- 修改采集流程、物理 observation schema、taxonomy 采集或上游 release discovery。
- 估计员工采用率、企业渗透率、工作流连续性、生产率、岗位替代、GDP、收入或 ROI。
- 融合 Claude.ai 与 1P API，构造加权综合渗透分数，或将缺失/隐私过滤 cell 推断为零。
- 将“可映射到 taxonomy 的全量任务覆盖率”作为标题指标。
- 将结果接入 PEAD、Chief、评分、组合、风险或交易执行路径。
- 创建通用方法卡框架，或改变芯片设计、WFE、Macro、Sector 及其他现有 Observer 的接口、输出、渲染或依赖。
- 回填虚构月份，或以 release 日期伪造月度趋势。

## 设计决策

### 1. 扩展现有领域 DataProducts，而不是创建第二个数据集

实施将在 `src/ats/data/products/ai_work_adoption.py` 中增加生产化渗透函数，并在
`DataProducts` 上提供轻量公开封装。函数按既有 `as_of` 语义查询 `ai_work_adoption`
observations，不持久化派生 observations。

主要公开接口概念上为：

```python
DataProducts.ai_production_penetration(
    periods=None,
    as_of=None,
    top_n=10,
    as_frame=False,
    snapshot_consumer=None,
    snapshot_purpose=None,
)
```

公开方法可以不暴露 `source_product`，或者只允许其值为 `1p_api`。返回结果包含 `status`、
`claim_inputs`、`period_rows`、`top_occupations`、`top_tasks`、`coverage_diagnostics`、
`lineage` 以及可选 `manifest`。`as_frame=True` 返回一组具名 DataFrame，而不是一个混合多种
grain、语义不清的单表。

这一选择复用了既有查询边界和 snapshot 机制。若新建物理数据集，会重复保存相同 observations，
增加对账风险，并可能把阈值变化误解为 Provider 数据修订。

### 2. 将资格判断实现为 cell 级逻辑合取，而不是打分模型

`core_production_workflow_proxy_v1` 是不可变的常量定义：

```text
usage_share > 0
AND work_use_share >= 80
AND automation_share >= 80
AND collaboration.directive_share >= 50
```

实施首先根据以下键构建 cell 指标矩阵：

```text
(source_product, period, methodology_version,
 classification, hierarchy_level, entity_id)
```

这里的“键”是一组用于唯一确定分析对象的字段。六个字段分别表示：

| 键 | 含义 | 为什么必须进入键 |
|---|---|---|
| `source_product` | 数据来自 `1p_api` 还是 `claude_ai` | 防止把两个产品的指标拼在一起 |
| `period` | 数据月份，如 `2026-05` | 防止把 4 月 Usage 与 5 月 Automation 拼在一起 |
| `methodology_version` | Anthropic 的统计方法口径版本 | 防止跨方法版本组合或比较 |
| `classification` | 实体分类体系，如 `soc_occupation` 或 `onet` | 防止把职业与任务混成同一类对象 |
| `hierarchy_level` | 分类中的层级；例如 SOC 大类与详细职业属于不同层级 | 本方案只对详细职业和具体任务判断资格，不把职业大类当作详细职业 |
| `entity_id` | 具体职业或任务的稳定 ID | 确保四项指标属于同一个职业或任务 |

原始 observation 通常是“长表”：每一行只保存一个指标。cell 指标矩阵把具有完全相同六元键的
多行横向拼成一行，方便判断四项条件是否同时成立。以下为说明性示例，数值不代表实际数据：

```text
输入 observations（长表）

共同键：
source_product=1p_api
period=2026-05
methodology_version=aei_method_v1
classification=onet
hierarchy_level=0
entity_id=ONET_TASK:7382

metric_id                              value
usage_share                             0.34
work_use_share                         96.00
automation_share                       92.00
collaboration.directive_share          65.00

转换后的 cell 指标矩阵（宽表）

entity_id       usage  work  automation  directive  qualified
ONET_TASK:7382    0.34  96.0        92.0       65.0        true
```

因为该 cell 的四项值都来自完全相同的产品、月份、方法、分类、层级和任务，所以可以进行合取
判断。如果其中一项只存在于 Claude.ai、其他月份或其他方法版本，系统会认为当前 cell 缺少该项，
而不会借用错误口径的数据补齐。

只有详细 SOC 职业和 O*NET task 可以参与资格判断。仅当四项 accepted/warning observation
均存在于同一键下并满足原始数值比较时，cell 才标记为 `qualified`。缺失输入返回
`not_qualified` 及明确原因，不进行插补；阈值比较前不得四舍五入。

每条资格记录返回四个 observation IDs、观测值、阈值、逐项比较结果、聚合质量状态和
`derivation_version`。threshold version 与通用 derivation version 分开管理，使未来阈值版本
能够共存而无需改写历史。

不采用以下方案：

- 将 Usage、Work、Automation 和 Directive 相乘：这些是边际分类比例，不是 Provider 发布的
  联合分布。
- 构建连续加权分数：权重会引入未经验证的模型，并掩盖究竟是哪项生产化条件失败。
- 将 Claude.ai 作为佐证并入：产品边界和使用场景不同，融合后分母失去清晰含义。

### 3. 用“两个核心指标 × 两种统计粒度”构建四条核心序列

这里的四条核心序列不是下面四个公式字段，而是我们此前确认的四个观测量：

| 核心序列 | 统计粒度 | 使用的核心指标 | 物理含义 |
|---|---|---|---|
| `occupation_visible_production_rate` | 职业 | `visible_production_rate_pct` | 可见职业中有多少比例达到生产化代理标准 |
| `task_visible_production_rate` | 任务 | `visible_production_rate_pct` | 可见任务中有多少比例达到生产化代理标准 |
| `occupation_production_traffic_share` | 职业 | `production_traffic_share_pct` | 达标职业合计承载多少 1P API 总流量 |
| `task_production_traffic_share` | 任务 | `production_traffic_share_pct` | 达标任务合计承载多少 1P API 总流量 |

换言之，存储/查询结果可以按 `period + grain` 排成两行；每行有两个核心指标字段。将
`grain=occupation` 和 `grain=task` 分别展开后，正好得到上表四条时间序列。

`published_usage_share_pct` 和 `conditional_production_traffic_share_pct` 是帮助解释公开数据是否
完整的辅助诊断字段，不属于四条核心序列，也不参与 Observer 的趋势判断。

每个期间、每种 grain（`occupation`、`task`）分别计算：

```text
visible_production_rate_pct
  = 100 * qualified_count / visible_usage_cell_count

production_traffic_share_pct
  = sum(provider usage_share for qualified visible cells)

published_usage_share_pct
  = sum(provider usage_share for all visible cells)

conditional_production_traffic_share_pct
  = 100 * qualified_usage_share / published_usage_share
```

以当前两个月的对账结果为例，核心输出在逻辑上类似：

| period | grain | `visible_production_rate_pct` | `production_traffic_share_pct` |
|---|---|---:|---:|
| 2026-04 | occupation | 44.95% | 68.77% |
| 2026-04 | task | 32.08% | 59.40% |
| 2026-05 | occupation | 46.67% | 75.36% |
| 2026-05 | task | 31.72% | 68.66% |

纵向读取 occupation 行得到两条职业序列，纵向读取 task 行得到两条任务序列。当前只能陈述
2026-04 到 2026-05 的月度变化，不能据此声称形成持续趋势。

标题指标“生产化流量份额”是 qualified cell 的 Usage Share 之和，因此单位仍是占 Provider
全部产品流量的百分比。条件流量份额仅用于诊断隐私过滤或公开 cell 不完整的问题，不替代标题
指标。职业与任务是同一流量的不同分类方式，两类结果不得相加或平均。

可见分母由符合目标 grain 且公开了 Usage observation 的全部 cell 构成，包括因其他必要指标
缺失而未达标的 cell。没有公开 Usage observation 的 cell 不进入该分母，只出现在覆盖诊断中。

每条期间记录公开分子、分母、单位、输入 observation IDs、期间、methodology/threshold/
derivation versions、来源 artifact IDs 和质量状态。计算保留完整精度，只允许展示适配器进行
四舍五入。

### 4. Taxonomy 不进入核心四序列，但用于计算职业内任务覆盖下限

任务资格和核心“可见任务生产化率”直接使用 AEI 稳定 task entity ID，因此不依赖 O*NET
relation。这样即使月度数据出现公开 taxonomy 尚未收录的新 Task ID，该任务仍会保留在核心
四序列中。独立的映射诊断将相同 task IDs 连接到 `as_of` 时点可见的 taxonomy，报告
mapped/unmapped 数量、relation IDs、taxonomy version 和代表性 unmapped IDs。

在补充证据层，taxonomy 用于回答另一个不同的问题：“一个具体职业的完整任务组合中，有多少
任务已经达到生产化代理标准？”对每个 detailed occupation 计算：

```text
occupation_production_task_coverage_lower_bound_pct
  = 100 * qualified_mapped_task_count / taxonomy_task_count

taxonomy_task_count
  = 指定 taxonomy version 中与该职业关联的去重任务总数

qualified_mapped_task_count
  = 上述关联任务中，当月存在 1P API task cell
    且满足 core_production_workflow_proxy_v1 的去重任务数
```

一个任务如果关联多个职业，会在每个职业的任务组合中各计一次；但同一个任务在同一职业中只计
一次。这是在计算“每个职业拥有哪些任务”，不是在分配 Usage 流量，因此不使用职业间的分数权重。

示例：某职业在 taxonomy 中共有 20 个任务。当月其中 8 个任务有公开 1P API cell，5 个达到
生产化标准，则：

```text
qualified mapped tasks = 5
taxonomy tasks          = 20
occupation coverage     = 5 / 20 = 25%
```

其余 15 个任务没有进入分子，其中可能包括不达标、未使用、未发布或被隐私过滤的任务。因此 25%
表示“至少已有 25% 的 taxonomy tasks 可被公开数据确认达到代理标准”，而不是断言其余 75% 完全
没有 AI 使用。反过来，当月达到标准但无法映射到当前 taxonomy 的新任务，也不能被擅自分配给某个
职业，所以该指标明确命名为 lower bound。

随后对全部具有有效 taxonomy task denominator 的详细职业生成互补累计分布（CCDF）：

```text
occupation_share_at_or_above(x)
  = 100 * count(occupation coverage >= x) / eligible_occupation_count
```

例如 100 个 eligible occupations 中有 36 个的覆盖下限不低于 25%，则 Figure 4 风格结果为：

```text
minimum task coverage = 25%
occupation count      = 36
occupation share      = 36%
```

系统返回每个不同覆盖率形成的完整曲线点，并固定输出 10%、25%、50%、75% 和 100% 五个 landmark。
逐职业覆盖率和分布均是 taxonomy-dependent 补充证据，不加入四条核心 breadth/depth/overall
状态。跨月比较还要求 taxonomy version 相同；否则只并列展示两期快照。

### 5. 使用严格的可比性键计算月度变化和趋势

期间按自然月排序。只有相邻期间连续，并且 source product、grain、methodology version、
threshold version 和 derivation version 完全一致时，才生成环比变化；否则返回 `period_gap`、
`methodology_changed` 或 `threshold_changed` 等原因。

DataProducts 层负责计算数值环比；Observer 只有在至少存在三个连续可比月时，才对四条序列
应用规范定义的趋势分类器。只有两个月时返回 `insufficient_history`，同时提供单独标注的
`monthly_comparison`。总体状态是职业/任务广度与职业/任务深度状态的显式逻辑合取，不允许
多数投票或 LLM 主观判断。

因此未来 release 可以直接扩展同一面板而不修改命题。Anthropic methodology 发生变化时，
必须开始新的 regime，不得静默连接为连续折线。

### 6. 所有消费者格式共用一套规范化记录

派生函数首先返回 JSON-safe dataclass/dict，然后由格式适配器转换为以下具名 DataFrame：

- `summary`：每个期间、每种 grain 一行，包含两项核心指标及其变化。
- `top_occupations`：选定最新期间的 qualified 职业排名。
- `top_tasks`：选定最新期间的 qualified 任务排名。
- `coverage`：taxonomy 映射与公开覆盖诊断。
- `occupation_task_coverage`：每个职业的生产化任务覆盖下限。
- `occupation_coverage_distribution`：职业覆盖率完整 CCDF 点集及固定 landmarks。

列顺序、dtype、grain 标签、单位、排序和 null 语义均显式定义并测试。SQL/结构化 JSON/Pandas
的一致性以 observation IDs 和完整精度数值核验，而不是对比格式化字符串。

Pandas 只是输出表示，不是第二套计算路径。这样 CLI、Observer 和 notebook 不会演化出不同公式。

### 7. TOPN 仅在生产化达标单元中按 Usage Share 排名

在请求的最新期间，每个合格 cell 按 `usage_share DESC, entity_id ASC` 排序。输出包含当前
Usage、Work、Automation、Directive、逐项资格比较，以及相对上一个可比自然月的变化。如果
上期 cell 缺失，则变化值保持 null 并附原因，不得以零替代。

每种 grain 默认返回十行。按 Usage 排名具有清晰物理含义：这些单元是在已满足代理标准的前提下，
承载最多 1P API 流量的职业或任务；同时四项原始输入使读者能够复核其达标原因。

### 8. 将 Observer 实现为 DataProducts 之上的确定性适配器

`src/ats/agents/evidence/work_adoption.py` 将增加独立的生产化渗透 Observer 入口，同时保留现有
Usage Observer。新入口只调用 `DataProducts.ai_production_penetration`，从返回记录生成事实，
应用固定命题/状态契约，并附加语义限制。

`methodology_card` 的构建逻辑、类型和渲染均放在 AI 工作采用/生产化渗透领域模块内，并由这一
专属入口显式调用。不得把它加入通用 Observer 基类、共享 protocol、所有 L1 输出 schema 或
全局报告前置处理。芯片设计、WFE、Macro、Sector 等 Observer 不导入该 builder，也不需要返回
空方法卡以满足兼容性。

输出 packet 包含：

```text
claim_id / claim_text / source_scope / threshold_definition
period_range / latest_period / history_status
four series statuses / breadth_status / depth_status / overall_status
summary table / top occupations / top tasks / coverage diagnostics
occupation task coverage / occupation coverage distribution / methodology card
facts / warnings / visualization descriptors / snapshot manifest
```

各部分的物理含义如下：

| packet 字段 | 物理含义 | 消费者如何使用 |
|---|---|---|
| `claim_id` / `claim_text` | 固定命题的机器 ID 和人类可读问题 | 确保每次报告回答同一个问题 |
| `source_scope` | 本次分析使用的数据边界 | 明确这里只使用 1P API，不融合 Claude.ai |
| `threshold_definition` | 生产化代理的四项阈值与版本 | 让读者知道“达标”如何定义并可复算 |
| `period_range` / `latest_period` | 覆盖月份和最新月份 | 说明结论对应什么时间 |
| `history_status` | 历史是否足够判断趋势 | 当前两个月返回 `insufficient_history` |
| `four_series_statuses` | 四条核心序列各自的方向状态 | 防止一个上涨指标掩盖另一个下跌指标 |
| `breadth_status` | 两条可见单元生产化率的合并状态 | 回答达标职业和任务的覆盖面是否共同扩大 |
| `depth_status` | 两条生产化流量份额的合并状态 | 回答 API 流量是否更集中到达标单元 |
| `overall_status` | 广度与深度同时满足规则后的总状态 | 只有证据充分且二者一致时才称渗透扩大 |
| `summary_table` | 四条核心序列按月排列的数值表 | 支持报告、Pandas 分析和图表 |
| `top_occupations` / `top_tasks` | 最新月达标单元中 Usage 最大的职业/任务 | 查看生产化特征最集中的典型单元 |
| `coverage_diagnostics` | 公开 cell、缺失 cell 和 taxonomy 映射覆盖情况 | 判断样本缺失或 taxonomy 滞后是否影响解释 |
| `occupation_task_coverage` | 每个职业中达到代理标准的 taxonomy task 覆盖下限 | 判断 AI 是否深入某个职业的任务组合 |
| `occupation_coverage_distribution` | 至少达到给定任务覆盖率的职业占比，即 Figure 4 风格 CCDF | 判断深度任务覆盖在职业间是普遍还是集中于少数职业 |
| `methodology_card` | 本次分析的数据源、样本、缺失、版本、可比性、血缘和限制 | 让人先理解口径，再阅读结论 |
| `facts` | 从结构化数值生成的短事实句 | 供 Evidence Observer 或报告正文引用 |
| `warnings` | 数据和语义限制 | 阻止把代理指标误写成员工采用率、持续工作流或 ROI |
| `visualization_descriptors` | 图表路径、类型、期间、单位和数据 hash | 证明图表与表格来自同一份数据 |
| `snapshot_manifest` | 冻结本次使用的 observations、relations 和版本 | 用于审计和日后原样重放 |

下面是一个删减后的说明性 packet。TOP10 和血缘在真实输出中会包含完整列表，这里只展示结构：

```json
{
  "claim_id": "ai_core_production_workflow_penetration",
  "claim_text": "满足核心生产流程标准的职业和任务是否持续扩大，且其 API 使用量是否持续提高？",
  "source_scope": {
    "provider": "Anthropic Economic Index",
    "source_product": "1p_api",
    "geography": "GLOBAL"
  },
  "threshold_definition": {
    "version": "core_production_workflow_proxy_v1",
    "usage_share": "> 0",
    "work_use_share": ">= 80%",
    "automation_share": ">= 80%",
    "directive_share": ">= 50%"
  },
  "period_range": ["2026-04", "2026-05"],
  "latest_period": "2026-05",
  "history_status": "insufficient_history",
  "four_series_statuses": {
    "occupation_visible_production_rate": "insufficient_history",
    "task_visible_production_rate": "insufficient_history",
    "occupation_production_traffic_share": "insufficient_history",
    "task_production_traffic_share": "insufficient_history"
  },
  "breadth_status": "insufficient_history",
  "depth_status": "insufficient_history",
  "overall_status": "insufficient_history",
  "monthly_comparison": {
    "breadth": "职业上升、任务下降，表现混合",
    "depth": "职业和任务的生产化流量份额均上升"
  },
  "summary_table": [
    {"period": "2026-04", "grain": "occupation", "visible_production_rate_pct": 44.95, "production_traffic_share_pct": 68.77},
    {"period": "2026-04", "grain": "task", "visible_production_rate_pct": 32.08, "production_traffic_share_pct": 59.40},
    {"period": "2026-05", "grain": "occupation", "visible_production_rate_pct": 46.67, "production_traffic_share_pct": 75.36},
    {"period": "2026-05", "grain": "task", "visible_production_rate_pct": 31.72, "production_traffic_share_pct": 68.66}
  ],
  "top_occupations": ["...最多 10 条结构化记录..."],
  "top_tasks": ["...最多 10 条结构化记录..."],
  "coverage_diagnostics": {"mapped_tasks": 1793, "visible_tasks": 2295, "unmapped_tasks": 502},
  "occupation_task_coverage": [
    {"occupation_id": "SOC:15-2031.00", "qualified_mapped_tasks": 5, "taxonomy_tasks": 20, "coverage_lower_bound_pct": 25.0}
  ],
  "occupation_coverage_distribution": {
    "eligible_occupations": 100,
    "landmarks": [
      {"minimum_coverage_pct": 25, "occupation_count": 36, "occupation_share_pct": 36.0}
    ]
  },
  "methodology_card": {
    "source_product": "1p_api",
    "geography": "GLOBAL",
    "periods": ["2026-04", "2026-05"],
    "threshold_version": "core_production_workflow_proxy_v1",
    "methodology_version": "...",
    "taxonomy_version": "...",
    "visible_occupations": "...",
    "visible_tasks": 2295,
    "mapped_tasks": 1793,
    "history_status": "insufficient_history",
    "limitations": ["缺失 cell 不等于零", "不能推断员工采用率、持续工作流、生产率或 ROI"]
  },
  "facts": ["2026-04 至 2026-05，生产化流量深度提高，但生产化广度表现混合。"],
  "warnings": ["只有两个可比月，不能判断持续趋势。", "该指标不是员工或企业采用率。"],
  "visualization_descriptors": {"chart_type": "monthly_comparison", "data_hash": "..."},
  "snapshot_manifest": {"manifest_id": "...", "observation_ids": ["..."]}
}
```

这个 packet 的人类可读结论是：当前证据显示，2026-04 到 2026-05 之间，达到代理标准的职业所占
比例略升、任务所占比例略降，因此广度表现混合；与此同时，达标职业和任务承载的 1P API 流量
份额均上升，因此深度在这个单月比较中提高。因为只有两个月，四条趋势状态和总体状态仍必须是
`insufficient_history`。

LLM 可以总结 packet，但不能改变资格结果、趋势标签或状态。事实模板必须区分“占 1P API 流量
的份额”与员工采用率，也必须区分明确的单月比较与趋势。

方法卡固定放在报告指标和结论之前，至少回答以下问题：数据来自哪里、覆盖什么期间、样本中有
多少可见职业/任务、哪些数据可能因隐私或 taxonomy 滞后而缺失、采用什么生产化阈值、当前方法
和派生版本是什么、月份是否可比、能否判断趋势、结论可追溯到哪个 manifest，以及哪些经济含义
不能从本数据推出。历史论文的分类验证率只有在确认与当前 release 使用同一方法时才可展示为
当前准确率；否则只能作为历史研究背景。

### 9. 在规范化 DataFrame 下游渲染图表

Evidence/report 边界内增加一个小型确定性 renderer。它只接收 `summary` DataFrame，不自行查询
数据。Seaborn 和 Matplotlib 加入 `data` 可选依赖，并采用延迟导入。

- 只有两个可比月：渲染 slope/dumbbell 月度比较图，标题和注释明确写明“月度比较；历史不足，
  不能判断趋势”。
- 至少三个可比月：渲染四个 small multiples 时间序列，或两个明确分开的广度/深度面板。
- methodology 或 threshold regime 边界使用断线或分面，不得连接折线。
- 对最新期间另行渲染 Figure 4 风格 CCDF：x 轴为“职业内生产化任务覆盖下限”，y 轴为“达到或
  超过该覆盖的 eligible occupations 占比”，并标注 10%、25%、50%、75%、100% landmarks。

图表使用非交互式 backend、固定主题、确定性尺寸、稳定颜色映射和易读标签。renderer 输出 PNG
和 JSON sidecar；sidecar 包含有序图表记录、数据 SHA-256、source product、版本、期间范围、
单位、renderer version 和 manifest ID。Markdown 报告链接 PNG，同时保留底层 summary 与 TOP10
表格，因此即使图片无法渲染，报告仍然可用。

如果导入或渲染失败，Observer 返回 `visualization_warning`；表格、事实、manifest 和命题状态
保持不变。图表数据 hash 在渲染前计算，并与 manifest 支持的规范化记录匹配。

不把图表生成放在 DataProducts 内，因为这会将展示依赖和文件系统副作用混入受治理查询层。

### 10. 提供研究型 CLI，但不扩大到决策集成

增加概念上等价的只读命令：

```text
ats data ai-production --period 2026-05 --top-n 10 --format json|markdown --chart-dir <path>
```

未指定 period 时，选择最新可用的 1P API 月份。JSON 是默认机器可读契约；Markdown 可选择包含
由同一记录生成的图表。命令的 `as_of` 和 snapshot 选项与现有 structured-data 命令保持一致。
不提供 Claude.ai 模式、评分参数或任何直接 Chain/portfolio 动作。

Evidence/Chain 报告集成只增加一个只读章节，嵌入已完成的 Observer packet。命题状态不得进入
corroboration score 或下游交易状态。该章节按 AI 生产化 claim ID 显式选择专属 renderer，不改变
通用 Evidence/Chain 报告对其他 Observer packet 的处理逻辑。

### 11. 将每项结果和图表绑定到可重放 manifest

请求 snapshot 时，将资格、聚合、TOPN 和比较使用的所有 observation IDs 去重后写入现有
snapshot manifest；覆盖诊断使用的 relation IDs 也一并记录。派生元数据包含 claim ID、
threshold version、derivation version、methodology regimes、有序记录 hash、图表数据 hash 和
renderer version。

即使之后出现新的 vintage，重放 manifest 仍必须复现相同数值记录。图像像素不是事实源；重放
过程验证图表 sidecar 的数据 hash，并可使用所记录 renderer version 重新渲染。

### 12. 对歧义数据失败关闭，同时保留部分诊断

不支持的 source product、cell 内混合产品、重复且冲突的 observations、非法单位或不可能的
百分比，都使相关 cell 失去资格并产生明确质量错误。若任一 grain 没有可见分母，则整个期间
不可用。taxonomy 只能部分映射属于 warning，不导致核心期间失败。

Observer 通过既有 accepted/warning 查询边界排除隔离 observations。它不会因为可选图表或
taxonomy 诊断失败而静默丢弃整个期间。

### 13. 审阅展示与按层 Evidence workflow 是同一条受治理路径

临时 notebook 或直接 Python 调用只能用于开发验收，不能成为用户审阅的默认入口。正式运行由通用按层入口发起：

```text
ats evidence layer --sector ai_hardware --layer L1_app
```

命令先加载 sector 配置并验证 layer 存在，然后只运行该 layer 的 `evidence_observers` 中已启用声明；
它不遍历或运行 L1–L8 的其他命题。`evidence_observers` 是配置真源，Python 仅维护受支持的
`runner` 名称到受治理 callable 的映射，禁止再以 `(sector, layer)` 静态白名单充当注册源。当前
`ai_hardware/L1_app` 声明 `ai_core_production_workflow_penetration` / `ai_production_penetration`；
其他已配置层如果没有声明，返回 `no_registered_observers`，不制造空泛结论。未来层可通过添加自己的
声明接入，不改变本命题的数据口径。

每个声明的最小结构为：

```yaml
evidence_observers:
  - claim_id: ai_core_production_workflow_penetration
    runner: ai_production_penetration
    label: AI 生产化与应用扩散
    enabled: true
```

它与 `claims` 有严格边界：`claims` 是 sector analyst/Chain 的公司证人与归因命题；
`evidence_observers` 是不接入 Chain 的受治理、只读数据观察。`ats evidence ai-production` 保留为
claim 快捷入口，但仍先解析本层声明；因此它不是独立的全局 scope 白名单。

每次按层运行默认把 Markdown 报告和图表 sidecar 写入 sector 的 `output_dir`，并在 stdout 打印产物
路径；`--output` 可覆盖报告路径，`--chart-dir` 可覆盖图表目录。JSON 是可选的机器接口，不能成为
唯一的用户可见产出。scope 写入每个 packet、报告和 snapshot purpose。该 workflow 不调用 Chain
corroboration，也不会改变既有 `ats evidence observe/report` 行为。

DataProducts 仍保留内部兼容字段
`occupation_production_task_coverage_lower_bound_pct`，因为该名称准确表达了数据不完整性；面对
审阅者时将它命名为“职业任务组合的已确认生产化覆盖”。这不意味着存在一个可由本数据观察到的
“上限”：分母包含 O*NET 中未公开、未观察或隐私过滤的任务，而分子只包含已映射且达到生产化代理
的公开 task cell。因此报告必须同时给出 `已确认生产化任务数 / O*NET 任务总数`，并说明它是
保守确认范围。

Markdown 审阅顺序固定为：

1. 方法卡与 scope；
2. 命题状态、四条核心序列及两月比较；
3. 职业任务组合的已确认生产化覆盖分布（CCDF 与 landmarks）；
4. 覆盖率至少 50% 的全部职业清单，明确标作分布尾部的完整披露、而非典型职业证据；
5. 按 Usage Share 排名的高流量生产化职业与任务 TOP10，作为典型使用单元的独立证据；
6. 四个核心指标的文末注释、公式与语义限制。

图表只读取同一份 `summary` 或覆盖 DataFrame。两个月的图标题使用“职业/任务可见单元生产化率”
和“职业/任务生产化流量份额”；不得把内部字段、宽泛的 Breadth/Depth 或无解释英文放入面向读者的
标题。CCDF 使用“职业任务组合的已确认生产化覆盖分布”。

## 风险 / 取舍

- [阈值由用户定义，并非 Anthropic 实证验证] → 明确命名和版本化为代理指标，公开四项输入；
  任何变更必须创建新版本。
- [1P API 流量可能来自测试或一次性调用，而不是生产工作流] → 所有命题保留“类生产化”限制，
  禁止使用确认工作流连续性的表述。
- [Usage Share 衡量 Anthropic 产品内部流量分布，不是市场采用率] → 保留“占产品流量百分比”的
  单位，禁止解释成员工或企业采用率。
- [隐私过滤可能使公开 cells 的 Usage Share 合计不足 100%] → 同时返回全产品流量份额、条件
  份额和 published share 总量，绝不将缺失填零。
- [task taxonomy 可能滞后于月度 Task IDs] → 核心 task 计算与 taxonomy join 解耦，映射覆盖率
  作为质量诊断；逐职业覆盖和职业分布明确标为 lower bound 且不进入核心状态。
- [逐职业覆盖会把未观察、隐私过滤和真实零使用合并在分母外观中] → 保留完整 taxonomy 分母，
  使用“至少已确认达到”的下限措辞，并同时展示公开与映射覆盖诊断。
- [当前两个月容易被过度解读] → 返回 `insufficient_history`，使用月度比较图而不是趋势线，
  并通过测试约束措辞。
- [四条序列可能方向不一致] → 保留每条序列，采用基于逻辑合取的状态判断，不构造综合分数。
- [Seaborn/Matplotlib 增加依赖和渲染差异] → 延迟导入、固定在 data extra/lockfile、使用确定性
  配置，并以表格输出作为事实源。
- [task 聚合的完整 observation 血缘可能很大] → 对 IDs 去重，在顶层展示对象中保留紧凑 hash，
  完整血缘通过 manifest/lineage 端点提供。
- [未来 methodology 变化会破坏可比性] → 拆分 regime；任何桥接逻辑都必须经过独立 change 的
  明确分析审阅。

## 迁移计划

1. 增加领域常量、结果契约和 DataProducts 方法，不改变现有 `ai_work_adoption` 调用者。
2. 在新方法内部实现资格判断、聚合、比较、TOPN、逐职业任务覆盖、职业 CCDF、覆盖诊断及 manifest 绑定。
3. 在 AI 工作采用领域模块内增加固定 Observer 入口、专属方法卡和结构化结果 packet；不修改通用 Observer contract，并保留当前 work-adoption 及所有其他领域 Observer 以维持兼容。
4. 增加 Pandas 适配器、可选可视化依赖、四序列与 Figure 4 风格 renderer、报告章节和只读 CLI。
5. 在默认启用报告章节前，验证 hermetic fixtures、当前受治理 2026-04/2026-05 结果、消费者边界
   和 snapshot replay。
6. 首先以 shadow/report-only 模式发布；对同一 manifest 比较 JSON、DataFrame、Markdown 表格和
   图表 sidecar hash。
7. 验收后启用 L1 报告章节；其性质始终是信息展示，不参与交易。

回滚只涉及代码：禁用新的报告/CLI 入口并撤销新增方法。不迁移或删除任何已保存 observation
或 taxonomy relation。因为底层数据与查询语义没有改变，现有 manifest 仍然有效；派生结果可用
记录的版本重新生成。若对应实现被有意移除，则应返回明确的 unsupported 状态。

## 开放问题

- 首版报告可以使用仓库固定主题；后续 UI 专项 change 可以定义本地化字体、交互式图表或
  dashboard 嵌入，而无需改变计算与命题逻辑。
- 如果 Anthropic 以后发布工作流连续性或 session 指标，未来 proxy version 可以在完成明确的
  methodology review 后，将其作为独立资格输入。
