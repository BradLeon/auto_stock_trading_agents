# FactSet Earnings Insight 技术设计（中文阅读版）

> 本文件是 `../design.md` 的中文翻译，便于快速阅读。英文原文仍是 OpenSpec 的正式 artifact；代码标识符、配置键和状态值保持原样。

## 背景

变更动机见 `../proposal.md`，规范性行为见 `../specs/data/factset-earnings-insight/spec.md`。

遗留实现分散在 `config/macro.yaml`、`src/ats/data/factset.py` 和 Macro 的组装/评审代码中。它在消费者路径中下载当前 PDF，最多读取前 16 个正文页，并返回一个临时的 `EarningsBackdrop`。该路径无法回答某个更早决策时点可以看到哪一期报告，无法暴露行业表格，也没有中央质量状态或发布状态。

统一数据层已经将不可变的非结构化文档版本、结构化 observation/vintage、发布模式和 DataProducts 分开。FactSet 同时跨越这两侧：文字和原始图表是权威文档证据，而每周重复出现的一组有限数值适合作为可查询时间序列。

当前上线验收只使用 `082826` 报告（2026-08-28）：第 1–16 页包含可用的可提取文字，第 17–36 页主要是栅格图表。它同时展示了两类必须显式暴露、不能隐藏的问题：措辞漂移（遗留解析器遗漏 “Ten of eleven sectors”）以及报告内部疑似存在的营收增长率冲突（7.5% 与 7.7%）。历史 PDF 可以作为受控重处理输入，但不是上线门槛。

FactSet 内容属于授权研究资料。原始 PDF 和图表图片是内部证据资产，而不是新的对外再分发渠道。

## 目标 / 非目标

**目标：**

- 每期周报只抓取一次，保留精确原始字节，并确保每个已发布数值都能从证据锚点复现。
- 先发布具备适用性判断的指数核心；11 个行业的核心数据只有通过确定性表格校验后才发布。
- 将报告日期、目标期间、估计状态和真实摄取时间作为不同概念保留，避免 `as_of` 查询泄漏回填信息。
- 为 Macro 和 Sector 提供一个有类型、与具体来源无关的读取接口，并显式返回新鲜度和降级状态。
- 数据源发布和消费者切换都可逆，且回滚时无需删除已采集数据。

**非目标：**

- 在 V1 构建完整的 FactSet 终端替代品，或抽取每张图表和所有公司排名。
- 将 Topic of the Week 中提到的公司视为公司官方披露或 PEAD 输入。
- 让 LLM 或视觉模型裁决互相冲突的来源数值。
- 在未经明确授权的内部研究视图之外再分发原始 PDF 或图表作品。
- 在 FactSet 契约得到验证前，把首个实现泛化成任意文档 OCR 平台。

## 技术决策

### 1. 一次采集生成两个受治理投影

采集协调器只抓取稳定 URL 一次，并写入一个按内容寻址的原始 artifact。随后驱动：

1. 注册为来源 `factset_earnings_insight_doc` 的非结构化投影；
2. 注册为来源 `factset_earnings_insight_metrics`、数据集 `sp500_earnings_insight` 的结构化投影。

两个投影共享 `document_id`、`document_version_id`、原始 `artifact_id`、报告日期、最终 URL 和 PDF SHA-256。对同一 hash 重新处理可以产生新的处理运行元数据和候选值，但不能产生新的源文档版本或 observation vintage。

这样可以复用现有非结构化和结构化准入路径，同时避免下载两次。没有采用单一混合物理表，因为它会把文档留存和数值发布耦合，并削弱 store ownership 规则。也不保留遗留消费者抓取作为 fallback，因为这会留下未经审计的第二事实来源。

### 2. 原始二进制与文档的关联采用通用设计，而非 FactSet 专用设计

在非结构化 store 中增加 document-artifact 关联，逻辑契约如下：

| 字段 | 含义 |
|---|---|
| `document_version_id` | 不可变的已提取文档版本 |
| `artifact_id` | 按内容寻址的二进制文件或派生裁剪图 |
| `role` | `source_pdf`、`page_image` 或 `chart_crop` |
| `page_number` | 适用时使用从 1 开始的来源页码 |
| `region_json` | 适用时使用页面坐标中的归一化 `[x0,y0,x1,y1]` |
| `media_type` | PDF 或图片 MIME |
| `content_hash` | 被关联字节的完整性校验值 |

`structured_evidence_links` 将支持 `anchor_kind`（`text_span` 或 `image_region`）、`page_number`、可选字符偏移、可选图表标识符和 `region_json`。现有文字证据继续有效；新增字段允许为空，以保证向后兼容。

没有只在 FactSet 行中添加 `raw_pdf_path`，因为路径依赖运行环境，也无法表示多个图表裁剪或未来的其他二进制文档来源。

### 3. 稳定业务指标 ID 与提供商无关，FactSet 状态由维度承载

Catalog 将注册以下 31 个 V1 指标族。比率以小数存储、以百分比展示；计数以整数存储；EPS 和估值使用带明确单位的十进制数。

| Metric ID | 实体范围 | 必需维度 / 说明 |
|---|---|---|
| `earnings.reporting.coverage` | SP500、GICS | 目标季度；可获得时包含已披露数/总数 |
| `earnings.eps.above_estimate_share` | SP500、GICS | 目标季度、估计状态 |
| `earnings.eps.inline_estimate_share` | SP500、GICS | 目标季度、估计状态 |
| `earnings.eps.below_estimate_share` | SP500、GICS | 目标季度、估计状态 |
| `earnings.revenue.above_estimate_share` | SP500、GICS | 目标季度、估计状态 |
| `earnings.revenue.inline_estimate_share` | SP500、GICS | 目标季度、估计状态 |
| `earnings.revenue.below_estimate_share` | SP500、GICS | 目标季度、估计状态 |
| `earnings.eps.surprise_pct` | SP500、GICS | 目标季度、估计状态 |
| `earnings.revenue.surprise_pct` | SP500、GICS | 目标季度、估计状态 |
| `earnings.eps.yoy_growth` | SP500、GICS | 目标季度或年度、估计状态 |
| `earnings.revenue.yoy_growth` | SP500、GICS | 目标季度或年度、估计状态 |
| `earnings.net_profit_margin` | SP500、GICS | 目标季度、估计状态 |
| `earnings.margin.increase_share` | SP500、GICS | 目标季度相对于对比期间 |
| `earnings.margin.unchanged_share` | SP500、GICS | 目标季度相对于对比期间 |
| `earnings.margin.decrease_share` | SP500、GICS | 目标季度相对于对比期间 |
| `earnings.guidance.positive_count` | SP500、GICS | guidance 的目标季度 |
| `earnings.guidance.negative_count` | SP500、GICS | guidance 的目标季度 |
| `earnings.revision.improved_sector_count` | SP500 | 目标季度、估计状态、对比日期、修正方向和行业总数 |
| `earnings.bottom_up_eps` | SP500 | 目标季度/年度及快照日期 |
| `valuation.forward_pe` | SP500、GICS | 前瞻区间及快照日期 |
| `valuation.trailing_pe` | SP500 | 回溯区间及快照日期 |
| `valuation.forward_pe.average_5y` | SP500 | 对比基准 |
| `valuation.forward_pe.average_10y` | SP500 | 对比基准 |
| `valuation.trailing_pe.average_5y` | SP500 | 对比基准 |
| `valuation.trailing_pe.average_10y` | SP500 | 对比基准 |
| `revenue.geographic.us_share` | SP500、GICS | 报告快照 |
| `revenue.geographic.international_share` | SP500、GICS | 报告快照 |
| `consensus.rating.buy_share` | SP500、GICS | 报告快照 |
| `consensus.rating.hold_share` | SP500、GICS | 报告快照 |
| `consensus.rating.sell_share` | SP500、GICS | 报告快照 |
| `consensus.target.upside` | SP500、GICS | 报告快照 |

每个 observation key 都包含实体、指标、目标期间或快照日期、估计状态（`estimated`、`blended`、`actual` 或 `not_applicable`）、单位、来源和 `known_at`。`report_date` 是来源元数据，绝不替代 `known_at`。`earnings.revision.improved_sector_count` 必须是从零到显式 `sector_total`（当前为 11）的整数，并保留 “Ten of eleven sectors” 等英文数字形式的原始 token、`comparison_date` 和 `revision_direction`。当来源同时提供计数和占比时，保存原始计数，占比可以在中央派生；缺失值必须是带原因的 null，不能写成零。

标准实体为 `SP500` 以及 `GICS_10`、`GICS_15`、`GICS_20`、`GICS_25`、`GICS_30`、`GICS_35`、`GICS_40`、`GICS_45`、`GICS_50`、`GICS_55`、`GICS_60`。提供商标签作为别名，在准入前解析。

没有使用带提供商前缀的 metric ID，因为如果未来加入第二个授权来源，这些概念应当仍可比较。没有使用周度宽表，因为同一报告内期间和估计状态不同，宽表会产生语义含糊的空列。

### 4. 抽取感知模板、候选优先，并在发布时保持确定性

解析器分为四个阶段：

1. **文档分类：** 校验 `%PDF`、标题、报告日期、预期页数范围以及章节/目录锚点。
2. **文字候选：** 使用小型、按章节划分的 extractor 解析具名章节和句子。每个候选包含标准化实体、指标、期间、状态、单位、原始 token、解析值、页码、字符 span、extractor 版本和 run ID。
3. **图表候选：** 通过标准化标题、坐标轴和图例锚点定位图表，不只依赖固定页码。渲染或提取图片，应用特定布局裁剪和确定性 OCR/表格识别，并为每个单元格产生图片区域证据。视觉模型可以在开发阶段建议候选值，但其输出不能满足发布门槛。
4. **准入：** 执行 schema、范围、组成、完整性、重复和证据校验；只发布通过校验的子集。

文字校验包括百分比范围、非负计数、期间/状态抽取以及重复项一致性。above/inline/below、margin increase/unchanged/decrease、地域和评级分布等组成组，在考虑来源四舍五入后必须合计为 `1.0 ± 0.01`。行业表格必须恰好包含预期的 11 个 GICS 行，且不能有重复或未知标签。若 Scorecard 印有总数，其三个计数必须能与总数勾稽。

首个图表 adapter 使用固定布局栅格流水线，并由本地可用的确定性 OCR 引擎支持。如果 OCR 依赖不存在，运行记录 `extractor_unavailable`；文档和指数文字处理继续，行业候选保持 shadow。没有把云 OCR 作为隐式 fallback，因为它会改变数据驻留、成本和可复现性。也没有只按页码解析图表，因为财报季内页码位置会变化。

当同一个 observation identity 对应不同来源数值时，所有候选都以 `conflict` 保留，不自动选择任何一个。这可以在不虚构权威优先规则的前提下处理 7.5% 与 7.7% 之类的差异。

### 5. 质量按适用性分组评估，再独立发布

报告阶段决定哪些指标组适用：

- `pre_reporting`：估计、guidance、估值、评级、目标价、地域；
- `in_progress`：pre-reporting 指标组，加上披露进度、Scorecard、surprise、blended 增长和利润率；
- `substantially_complete`：actual/blended 结果指标组，加上下期估计和 guidance。

缺少不适用的指标组不会导致报告失败。对于每个适用指标组，发布 manifest 记录预期单元格、观察到的单元格、准入单元格、冲突、隔离单元格和证据覆盖率。

系统包含两个可独立发布的分区：

- `index_core`：所有适用的 SP500 文字字段必须具有有效的期间、状态、单位和证据；任何冲突只隔离受影响指标，但分区发布前必须完整具备必需的头部指标。
- `sector_core`：每张适用图表必须包含全部 11 个预期行业和全部预期列。在首次切换期间，还要求与人工标注验收集逐单元格 100% 一致。

发布状态为 `registered_no_data`、`shadow`、`platform`、`stale` 或 `unavailable`，与现有 rollout 语义一致。默认新鲜度从报告日期起算 10 个自然日；这样既允许节假日周，又能显式暴露缺少下一期报告。一次来源抓取成功并不表示两个分区都已发布。

没有采用整份报告全有或全无的单一门禁，因为不稳定的行业 OCR 会阻塞质量更高的指数文字。也没有逐单元格尽力发布不完整行业表，因为消费者可能把缺失行误解为相对较弱。

### 6. DataProducts 暴露一个有类型的周度快照

新增 `src/ats/data/products/earnings_insight.py`，其中有类型记录的概念结构如下：

```text
EarningsInsightSnapshot
  report: report_date、document/version/artifact 引用、official_url
  index: 按期间和指标分组的 observations
  sectors: map[GICS entity, 按期间和指标分组的 observations]
  status: index_release、sector_release、freshness、warnings
  lineage: 被选中的 observation IDs、known_at 值、extractor/release 版本
```

`DataProducts.earnings_insight_snapshot(as_of=None)` 在 store 接口之后执行已发布的结构化查询和文档关联。它接受显式决策时间，并且不会触发刷新。不存在已发布数据时，它返回有效的 unavailable 对象，而不是 `None` 或异常。Macro 切换期间使用兼容 mapper 创建遗留 `EarningsBackdrop`；该 mapper 是 DTO adapter，不是第二个解析器。

Macro 只接收指数分区，用于盈利广度、增长、利润率、guidance 和估值背景。Sector 接收 GICS 矩阵作为 top-down overlay，并保持它与本项目 AI hardware layers 的区别。Chief、Risk 和 PEAD 不直接调用此产品；它们继承已经持久化的 Macro/Sector 发现。Technical 和公司级证据 workflow 不在范围内。

没有返回通用 dictionary，因为无声的字段和单位漂移正是该来源的主要风险。也没有让 Agent 单独查询 observations，因为这会重复选择规则，并可能产生混合多期报告的快照。

### 7. 刷新是现有周度评审之前的独立周六任务

新增 `schedule.factset_refresh_at`，默认值为 `08:10`；新增 `schedule.factset_refresh_tz`，默认继承周度评审时区。`src/ats/runtime/scheduler.py` 在现有 `weekly_review_at` 任务（当前配置为 `08:50`）之前注册周六运行的 `factset_weekly_ingest`。该任务在一个可观察的 pipeline run 中完成采集、文档投影、候选抽取、校验和满足条件的发布。

周度评审不会等待或触发网络抓取。它读取截至运行开始时最新的已发布快照。如果刷新失败，DataProducts 返回上一期 release，并带有 `stale` 和本次尝试的失败信息；如果没有任何 release，则返回 `unavailable`。Scheduler 启动时校验同一时区内刷新时间早于评审时间。误触发只在有限 grace period 内执行一次，且不会制造多个 vintage。

若在下一个周六窗口前已获批准进行切换，操作人员可以在不修改调度器的情况下产生等价验收证据：针对授权的当前 PDF 运行已注册 FactSet import pipeline，再按顺序直接运行 Macro 与 Sector review。导入与两次 review 必须使用正常的生产路由，并持久化 run ID、时间戳、选中报告版本、effective mode 和结果。这只替代本次观察证据，不绕过质量/release 门，也不改变正式的定时执行顺序。`sector_factset` 在独立提升任务完成前仍保持 shadow。

没有把刷新隐藏在 Macro assembly 中，因为 Sector 和未来消费者需要同一个版本，而且重试不应与 Agent 执行耦合。也没有假设完全依赖外部调度，因为仓库已经负责周度评审顺序，需要一个可执行的依赖边界。

### 8. 发布采用独立的数据源控制与消费者控制

为 `factset_earnings_insight_doc`、`factset_earnings_insight_index` 和 `factset_earnings_insight_sector` 注册 source mode，并为 `macro_factset` 和 `sector_factset` 注册 consumer mode。source mode 控制采集、准入和发布；consumer mode 控制读取路由。这样可以在行业仍处于 shadow 时把指数提升到 platform，也可以在不删除数据的情况下回滚消费者。

验收语料以小型、可提交的 manifest 保存，其中包含 PDF SHA-256、预期报告元数据、选定文字 span 和预期表格单元格。授权 PDF 字节保留在配置的内部 artifact 位置，不提交到仓库。需要该语料的测试在 artifact 不可用时以明确原因跳过；fixture 级合成 PDF 覆盖 CI 机制。

### 9. 可观察性以报告和分区为中心

每次运行输出：稳定/最终 URL、响应状态、artifact hash、报告日期、extractor 版本、页面/文字/图表数量、按指标组统计的 candidate/admitted/conflict/quarantine 数量、指数/行业发布结果以及耗时。绝不记录 PDF 正文或带签名/认证参数的 URL。

运维 CLI/报告将支持：

- 查看当前来源状态和最近一次失败；
- 列出报告版本及 hash；
- 按分区和指标组查看质量；
- 将 observation 追溯到文字 span 或图表区域；
- 查询 latest、`as_of` 和全部 vintages；
- 使用新的 extractor 版本重新处理 artifact，而不重新下载。

## 风险 / 权衡

- **[FactSet 改变 PDF 布局或保护方式]** → 通过锚点和 extractor 版本识别模板；隔离未知模板，同时保留原始文档并提醒操作人员。
- **[栅格 OCR 产生看似合理的错误数字]** → 发布行业数据前要求实体/列完整、组成关系校验、逐单元格证据以及标注语料 100% 准确；模型置信度绝不作为准入依据。
- **[来源自身互相矛盾]** → 保留两组候选并标记 conflict，在出现确定性规则或更正报告前排除受影响 observation。
- **[历史回填看起来像当时已经可见]** → 分离报告日期与 `known_at`，将后者设为真实首次摄取时间；只有独立保存的获取记录才能证明更早的已知时间。
- **[节假日或发布延迟使有效数据变成 stale]** → 使用 10 天阈值并暴露数据年龄/报告日期，让消费者显式降级而非硬失败。
- **[授权图表泄漏到输出中]** → 将原图/裁剪图保留在内部 artifact 访问边界内，优先依据已发布 observations 重绘，并在内部视图包含来源/授权元数据。
- **[新 OCR 系统增加原生依赖]** → 将其设计为可选图表 adapter；没有 OCR 时指数文字仍可发布，启动/来源验收报告依赖可用性。
- **[更多结构化字段造成虚假精确性]** → V1 仅覆盖重复出现且有决策价值的指标族；保留原始措辞及状态/期间语义，长尾公司表格继续作为文档证据。

## 迁移计划

1. 增加 document-artifact link、图片证据锚点、FactSet 来源、实体、指标、数据集、质量 profile、调度和独立发布控制的 catalog/schema 支持。在不开启采集的情况下运行迁移。
2. 实现采集和文档投影。将当前 `082826` 本地 PDF 摄取到隔离数据库/artifact namespace；校验 hash、报告日期、幂等性、完整正文和图表 inventory。
3. 实现指数文字候选与准入。产出带标注的 source-acceptance 报告，解决所有适用必需字段缺口，并仅以 shadow 模式发布 `index_core`。
4. 增加 DataProducts 和 `EarningsBackdrop` 兼容 mapper。对当前报告执行 Macro 双读比较；对比数值、期间/状态标签、新鲜度以及渲染后的评审文字。
5. 将指数来源提升到 platform，再把 `macro_factset` 切换到 platform。观察一次定时运行和一次周度评审；在下一个周六窗口前，可执行获批准的等价操作：FactSet import 后直接运行 Macro 与 Sector review。若 Macro 回归门禁失败，回滚 consumer flag，但保持来源采集开启并记录演练。
6. 实现图表 adapter，并标注 `082826` 中所有适用的行业单元格。在提升 `sector_core` 前，要求已验收单元格 100% 一致、恰好包含 11 个行业、且没有未解决的标签映射。
7. 在 `sector_factset=shadow` 后增加 Sector top-down overlay，运行消费者 smoke/regression 测试，再在一次成功定时刷新后独立提升它。
8. 两个消费者都完成观察期后，从 Macro 移除直接下载/PDF 解析，并取消外部 Obsidian 文件夹的运行时依赖。保留有文档说明的 artifact 导入命令，用于由操作人员控制的回填。

回滚只改变 consumer/source release mode：将受影响消费者设为 legacy/off，保留 artifact、document、candidate、observation 和 vintage。事故响应不需要回滚 schema；新增表/列保持闲置即可。只有在观察期结束后才移除遗留解析器，因此在最终步骤之前仍可回滚到旧路径。
