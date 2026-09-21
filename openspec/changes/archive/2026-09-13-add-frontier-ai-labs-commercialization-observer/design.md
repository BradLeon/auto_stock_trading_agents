## Context

参见 [proposal.md](./proposal.md) 的动机。现有 L1 已通过独立 Evidence Observer 观察“生产化与应用扩散”，统一结构化数据层已经具备 catalog、artifact、observation、vintage、quality、DataProduct、manifest replay 和按层运行能力。新工作应复用这些契约，而不是建立另一套网页缓存或报告专用数据文件。

Frontier AI Labs 尚无规则财报，公开信息混合了标准 ARR、短期收入年化、实际季度/年度收入、管理层预测和第三方 tracked estimate。Sacra 公开页的优势是收入段落和引用结构相对稳定，并会继续更新；但 Sacra 本身仍是二次整理者，必须同时保存它引用的原始出处及其对数值的身份描述。TickerTrends 指定文章只用于一次性补足 2026 年 1–6 月明确披露的历史点。

## Goals / Non-Goals

**Goals:**

- 让 OpenAI、Anthropic 的收入观察成为统一结构化数据层中的受治理、可 as-of、可离线重放数据。
- 形成 L1 商业化能力的独立 Observer 和正式报告，首版诚实回答“收入规模与趋势”，同时显式显示完整命题尚缺的留存和单位经济证据。
- 定期发现 Sacra 页面更新，同一次采集只访问外部页面一次，入库后所有查询、判断和图表均离线复用。
- 使收入曲线中的每个点都能追溯到来源页面、原文引用、观察身份、计量口径和 known-at。

**Non-Goals:**

- 不在本变更内估算毛利率、推理成本、客户留存、客户集中度、CAC 或盈利时间表。
- 不将 Labs 收入等同于整个 L1 应用层收入，也不将其并入生产化与应用扩散的三轴/四轴判断。
- 不购买或接入 Sacra/TickerTrends 的付费 API、MCP 或 Enterprise 数据。
- 不从 TickerTrends 图表目测数值，不补齐未披露月份，不训练自有 ARR 预测模型。
- 不把商业化 Observer 接入 Chain、评分、仓位、风控或交易决策。

## Decisions

### 1. 商业化能力采用独立 Observer，首版只开放一个已验证证据部分

在 `ai_hardware/L1_app.evidence_observers` 中新增第二个 observer declaration：

```yaml
- claim_id: ai_frontier_labs_commercialization
  claim_definition_version: v1
  runner: ai_commercialization
  label: AI 商业化能力
  enabled: true
  evidence_sections:
    - frontier_labs_revenue_scale_and_trend
```

总命题保持为用户定义的“高质量、可留存、合理单位经济、可持续商业模式”；packet 同时维护 section coverage：

```text
revenue_scale_and_growth = observed
revenue_retention         = not_yet_observed
unit_economics            = not_yet_observed
business_model_durability = not_yet_observed
```

因此首版可以判断收入部分为 `expanding`，但 overall 只能输出类似 `revenue_monetization_expanding_but_economics_unverified` 的范围受限结论。这样后续增加留存和单位经济证据时可以扩展同一商业化 Observer，而无需重写生产化 Observer。

备选方案是把 ARR 作为 Ramp 补充指标。拒绝原因是 Ramp 测量 Ramp cohort 的企业付费采用和支出份额，Labs ARR 测量供应商收入规模，主体、分母和可推断事实不同。

### 2. Sacra 是“最新观察入口”，其底层引用决定观察身份

每 7 天运行一次 `frontier_ai_labs_revenue_p7d_probe`，只检查两个注册页面：

```text
https://sacra.com/c/openai/
https://sacra.com/c/anthropic/
```

采集器优先读取无需登录的公开 HTML/页面数据，保存一次原始 artifact 后再解析；报告运行绝不访问网页。若静态响应无法包含收入段落，可使用现有受控浏览器能力生成一次页面快照作为该轮 artifact，但解析、入库和报告不得为同一 release 重复打开页面。

Sacra 页面中的数值按原文决定身份：

- “Sacra estimates … annualized revenue” → `annualized_run_rate / third_party_estimate`；
- 明确引用公司宣布的 ARR → `reported_arr / company_reported`，同时记录 `publisher=sacra` 与 origin citation；
- 引用媒体获得的内部文件 → 对应 metric + `media_reported`；
- “expects/on pace/target” → 对应 metric + `projection`。

Sacra 仅提供导航和结构化整理，不自动提升原始证据等级。来源链保存 `publisher_url → quoted passage → origin_url/origin_name`。

备选方案是直接抓取所有新闻源。首版拒绝该方案，因为来源宇宙和语义消歧成本过大；Sacra 已提供一个可审阅的发现层，足以验证数据模型与 Observer。

### 3. TickerTrends 使用冻结的历史回填窗口

指定文章作为一次性 seed artifact：

```text
https://blog.tickertrends.io/p/anthropic-vs-openai-arr-tracking
accepted_reference_period: 2026-01-01..2026-06-30
observation_identity: third_party_estimate
```

只解析正文明确陈述的公司、月份和数值。raw label 即使写作 ARR，只要文章表达的是 tracking/annualized run rate，就规范化为 `annualized_run_rate`，并保留 `raw_metric_label=ARR`。缺失月份保存为 coverage diagnostic，不产生 observation；7 月及以后数值因用户指定的历史边界被过滤。

TickerTrends 不加入周期发现任务，避免公开 Blog 的选题变化把不一致的估算模型持续混入主序列。未来若需要高频 tracked estimate，应另行定义方法透明度和跨版本校准契约。

### 4. 使用统一 observation/vintage schema，不新建报告专用数据库

建议注册：

```text
source_id:
  sacra_public_company_profiles
  tickertrends_public_research

dataset_id:
  frontier_ai_labs_revenue

metric_id:
  ai.frontier_lab.reported_arr
  ai.frontier_lab.annualized_revenue_run_rate
  ai.frontier_lab.trailing_revenue
  ai.frontier_lab.forward_revenue_projection
```

规范化 observation grain：

```text
source_id, dataset_id, company_entity_id, product_entity_id?,
metric_id, observation_identity, reference_period,
value, currency, unit, period_basis, published_at, known_at,
publisher_url, origin_citation, raw_quote, raw_metric_label,
methodology_regime, artifact_id, revision, quality_status
```

`company_entity_id` 使用受治理实体表的 OPENAI/ANTHROPIC 身份，不以页面标题临时创建实体。数值统一存储原币种原值；首版均为 USD 时可展示十亿美元，但转换公式属于 derivation。自然键不包含 value，value 或引用变化生成 revision；payload 完全相同则幂等。

备选方案是保存一张手工 CSV。拒绝原因是无法可靠处理 known-at、来源冲突、修订和 manifest replay，也会重现此前重复物理数据层问题。

### 5. Headline 选择与冲突不等于跨来源融合

每个公司 latest headline 先按“相同 metric identity 和 observation identity”建立候选集合，再按以下顺序选择：

1. 可追溯的 `company_reported`；
2. 可追溯的 `media_reported`；
3. Sacra 明确标识的 `third_party_estimate`；
4. TickerTrends 历史补充只参与其冻结窗口，不竞争最新 headline。

来源优先级只决定报告主显示，不删除候选。相同期间的可比值差异超过 10% 时标记 `source_conflict` 并并列披露；不同 metric/identity 则标记 `not_comparable`，不计算差异。

不建立“统一 ARR”合成指标，也不以平均数调和来源差异。这符合不同身份信息相互印证而非硬融合的既有原则。

### 6. 趋势使用透明规则并适应不规则披露

趋势 cell 的键为：

```text
company_entity_id, metric_id, observation_identity,
currency, methodology_regime
```

同一 cell 至少需要 3 个有效观察、覆盖至少 60 天才能判断方向。派生输出包括起点、终点、净变化额、净变化率、按真实天数计算的线性斜率、观察数和 period gaps：

- `expanding`：净变化率 ≥ +10%，斜率为正，最近一个可比变化不构成 ≥10% 的反向变化；
- `contracting`：净变化率 ≤ -10%，斜率为负，最近一个可比变化不构成 ≥10% 的反向变化；
- `stable`：绝对净变化率 < 10%，且没有相反方向的重大变化；
- `mixed`：端点与斜率方向冲突，或观察期内同时存在重大上升与下降；
- `insufficient_history`：少于 3 点或不足 60 天；
- `unavailable`：没有通过质量门的数据。

公司之间不合成平均增速。section 总状态只有两家公司均为同方向时才沿用该方向；一家公司历史不足时返回 `partial/insufficient_history`；方向相反时为 `mixed`。阈值和计算步骤进入 sidecar/packet，正文只呈现结论所需摘要。

### 7. 正式报告显示离散证据曲线，不制造完整月度序列

报告结构：

1. 商业化能力总命题与范围受限结论；
2. Frontier Labs 收入部分判断；
3. OpenAI、Anthropic 最新可比收入卡片；
4. 收入水平与趋势双面板图；
5. 历史 observation 表；
6. 来源、口径与冲突说明；
7. 指标公式、统计范围和数据缺口；
8. 尚待建设的留存、单位经济与商业模式证据。

图中横轴为真实参考月份，纵轴为 USD 十亿美元。点的颜色区分公司，形状/注记区分来源与观察身份；只有同一 cell 内的点可以用虚线连接，明确表示“离散披露点之间的方向辅助线”，不是月度估算。不同 metric identity 使用分面或独立图，禁止连接。中文字体沿用已经验证的字体发现与 glyph 检查机制。

图表 CSV/JSON、PNG、sidecar、Markdown、packet 和 compact context 全部由同一受治理 rows hash 生成。报告方法卡明确：Labs 收入不能代表全部 L1、run rate 不等于审计收入、收入增长不证明留存或单位经济。

### 8. 首次通过端到端验收后直接发布 platform

开发使用隔离 SQLite、artifact 与 output 路径完成测试，但这只是测试隔离，不形成运行时 shadow 数据产品。验收必须覆盖：

```text
catalog validation
→ TickerTrends history seed
→ Sacra scheduled discovery
→ parse/normalize/quality
→ ingest/vintage/as-of
→ DataProduct/comparability
→ commercialization Observer
→ Agent context/Markdown/visualization
→ lineage/manifest offline replay
→ L1 layer independent run
→ existing production Observer regression
```

全部通过后，在同一变更中把 source/dataset 状态配置为 `platform`，并启用 L1 observer。没有额外的 shadow promotion 操作。后续 source failure 保留最近有效数据并告警；新 revision 质量失败或 methodology drift 时仅隔离该 revision。

## Risks / Trade-offs

- **[私营公司口径不一致]** 同一个“ARR”可能是合同 ARR、月收入年化或渠道 gross/net 不同口径 → 保存 raw label、规范 metric identity 和 observation identity；不可比时不排行、不计算差额。
- **[Sacra 是二次来源]** 页面可能更新估算但没有稳定 release ID → 保存页面 artifact、内容 hash、引用链和 known-at；同期间变化生成 revision vintage。
- **[公开页面结构变化]** DOM 或文本结构改变导致解析失败 → 使用小范围语义锚点和 fixture contract；失败保留最近有效 platform 数据并发出 parse/methodology warning。
- **[历史点稀疏]** TickerTrends 文章无法形成完整月序列 → 只绘离散点、披露 gaps，并以最小点数/跨度门槛限制趋势判断。
- **[幸存者与头部偏差]** OpenAI、Anthropic 不能代表所有模型公司或应用层 → 报告标题、方法卡和 Agent context 固定披露“Frontier Labs revenue”范围。
- **[收入不等于高质量收入]** 快速 run-rate 增长可能伴随高推理成本、折扣或集中度 → overall 始终显示 economics/retention 未验证，直到未来证据部分落地。
- **[直接 platform 的发布风险]** 没有 shadow 观察期可能放大首次解析错误 → 以冻结 fixtures、双来源人工抽样、全链路 replay 和正式报告视觉审阅作为发布闸；失败即不启用 observer。

## Migration Plan

1. 注册两个来源、一个收入 dataset、四类 metric、实验室实体映射、P7D 调度和保存限制。
2. 将 TickerTrends 指定文章作为冻结 artifact 回填 2026 年 1–6 月明确数值，完成人工逐点对账。
3. 对 Sacra 两个公开页面做一次受控发现，保存 artifact、引用与当前收入 observations；验证重复运行返回 `no_change`。
4. 完成统一入库、revision/as-of、DataProduct、比较矩阵和离线 manifest replay。
5. 注册并启用 L1 商业化 Observer，生成独立 packet、Agent context、中文正式报告与图表；确认既有生产化 Observer 输出不变。
6. 全部验收通过后随同代码直接发布 `platform`；若首次验收失败，则不启用 source/observer，而不是发布残缺报告。
7. 回滚时停用新的 observer 与调度并将 source 设为 disabled；保留已入库 artifacts/observations/vintages 以供审计，不删除或回写既有 L1 数据。

## Open Questions

无会改变当前规格或任务拆分的阻塞问题。实施时可根据 Sacra 实际公开 HTML 确定使用静态 HTTP 还是单次受控浏览器 snapshot；两种路径必须产生相同 artifact 与 observation 契约，且不影响一次采集、离线复用原则。
