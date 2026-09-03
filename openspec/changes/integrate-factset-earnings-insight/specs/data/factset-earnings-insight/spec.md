## Purpose

为 FactSet Earnings Insight 建立可审计、可回放的周度文档与结构化数据产品，使 Macro 和 Sector Workflow 能稳定使用指数及行业盈利背景，而不在 Agent 内部下载、看图或猜测缺失数字。

## ADDED Requirements

### Requirement: The weekly report is acquired once as an immutable source asset
系统 SHALL 通过 FactSet 的稳定 Earnings Insight URL 获取当期报告，跟随重定向但同时保留稳定入口、最终日期版 URL、HTTP ETag、Last-Modified、首次可见时间、抓取时间、MIME、字节数和内容 hash。系统 SHALL 保存原始 PDF，并以 PDF 内容 hash 识别不可变版本；重新处理不得覆盖旧版本。

#### Scenario: A new weekly PDF is available
- **WHEN** 稳定入口返回一个合法、尚未保存的日期版 PDF
- **THEN** 系统 SHALL 保存一个新的原始 artifact 和 document version，并记录稳定入口与最终 URL
- **AND** 同一份下载内容 SHALL 只触发一次正文和数值处理

#### Scenario: The landing URL still points to the previous report
- **WHEN** 本次获取的最终 URL、ETag 或 PDF hash 对应已保存版本
- **THEN** 采集 SHALL 以幂等 no-op 完成，不创建重复 document version、数值 vintage 或处理投影

#### Scenario: FactSet is unavailable or does not return a PDF
- **WHEN** 请求超时、被拒绝、最终响应不是 PDF 或 PDF 无法解析
- **THEN** 系统 SHALL 记录 `unreachable`、`unauthorized`、`not_pdf` 或 `parse_failed` 等明确状态及原因
- **AND** SHALL NOT 用空文档、零值或 Agent 现场搜索伪装成功采集

### Requirement: The report is admitted as a complete governed research document
每期报告 SHALL 作为主实体为 `SP500`、来源实体为 `FACTSET`、语义为 `research_article`、载体为 `pdf` 的统一文档资产。文档版本 SHALL 关联原始 PDF artifact，并保存逐页正文、页码、章节标题和可定位的图表区域；PDF 与提取文本 SHALL 使用各自的内容 hash。

#### Scenario: Commentary and charts are both present
- **WHEN** 报告正文页可提取文本且后续页包含栅格图表
- **THEN** 系统 SHALL 保存完整正文和图表页/区域索引
- **AND** 图表 SHALL 作为原始证据或派生视图保留，而不是被当作已验证的数字表

#### Scenario: One named company appears in the weekly topic
- **WHEN** Topic of the Week 提到一家公司但该报告的业务主体仍是 S&P 500 总体
- **THEN** 系统 SHALL NOT 仅因名称出现就把整份报告发布为该公司的 PEAD 官方资料或公司级结构化数据

### Requirement: The structured dataset has explicit index and GICS-sector semantics
系统 SHALL 注册持久数据集 `sp500_earnings_insight`。指数级观测 SHALL 使用实体 `SP500`；行业级观测 SHALL 使用 `GICS_10`、`GICS_15`、`GICS_20`、`GICS_25`、`GICS_30`、`GICS_35`、`GICS_40`、`GICS_45`、`GICS_50`、`GICS_55` 和 `GICS_60`。每条观测 SHALL 明确报告日期、目标季度/年度或 snapshot 日期、`estimated|blended|actual` 状态、单位、来源、`known_at` 和质量状态。

核心指数范围 SHALL 包括披露进度、EPS/营收 above-inline-below 分布与 surprise 幅度、EPS/营收同比增长、净利率、正负 guidance 数量、行业盈利修正广度 `earnings.revision.improved_sector_count`、bottom-up EPS 端点、forward/trailing P/E 及 5/10 年参照、评级分布和目标价上行空间。核心行业范围 SHALL 包括报告提供的对应 scorecard、surprise、增长、净利率与 margin breadth、guidance、forward P/E、美国/国际收入暴露、评级分布和目标价上行空间。

#### Scenario: A report covers multiple target periods
- **WHEN** 同一期报告同时提供已披露季度、下一季度、当前日历年和下一日历年的估计
- **THEN** 系统 SHALL 将每个数值绑定到其真实目标期间和估计状态
- **AND** SHALL NOT 以报告日期替代目标期间或默认所有字段属于同一季度

#### Scenario: The earnings season changes terminology
- **WHEN** FactSet 从 `estimated` 变为 `blended`，或在披露完成后使用 `actual`
- **THEN** 系统 SHALL 保留该状态差异并继续同一业务序列的可查询历史
- **AND** SHALL NOT 将缺少 Scorecard 或 guidance 误写为零

#### Scenario: Revision breadth is written as words
- **WHEN** 报告称 “Ten of eleven sectors” 的盈利增长相较某一日期改善
- **THEN** 系统 SHALL 将 `earnings.revision.improved_sector_count` 保存为整数 `10`，实体为 `SP500`，并保留真实目标季度、估计状态、`comparison_date`、`revision_direction` 和 `sector_total=11`
- **AND** 原始单词 token 与完整文字 span SHALL 作为证据保留
- **AND** 当原文未给出明确数量或总数时，系统 SHALL 保持 null 并记录原因，不得根据方向性描述猜测数量

#### Scenario: A chart contains a Top or Bottom company list
- **WHEN** 报告提供 Top/Bottom 10 EPS surprise、EPS revisions 或价格反应分桶
- **THEN** 这些内容 SHALL 保留在文档/图表资产中，但 SHALL NOT 成为 V1 核心数据集的完整公司截面

### Requirement: Every numeric observation is evidence-linked and centrally admitted
来自正文的数值候选 SHALL 链接到 document/version、页码和精确文字 span；来自图表的数值候选 SHALL 链接到 document/version、页码、图表标识和图像区域。只有通过实体、期间、指标、单位、范围、完整性和跨来源区域一致性检查的候选才可进入默认查询。OCR、视觉模型或 LLM 输出 SHALL NOT 单独构成准入依据。

#### Scenario: Text and chart agree
- **WHEN** 同一指标在 Key Metrics、正文和图表中重复出现且数值与口径一致
- **THEN** 系统 SHALL 发布一个观测，并保存所有支持它的 evidence anchors 与 extraction methods

#### Scenario: Text and chart disagree
- **WHEN** 同一报告对相同实体、指标、期间和口径给出不同数值
- **THEN** 系统 SHALL 将受影响候选标记为 `conflict` 或 `quarantined` 并保留各自证据
- **AND** SHALL NOT 通过来源顺序、模型置信度或任意取最后值静默解决冲突

#### Scenario: A sector table is incomplete
- **WHEN** 图表抽取未得到预期的 11 个 GICS 行业、必要列、合计关系或完整标签
- **THEN** 该表的行业候选 SHALL 保持 shadow/quarantined
- **AND** 已独立验证的指数正文指标 MAY 继续发布

### Requirement: Weekly revisions are queryable as immutable vintages
同一目标期间的每周变化 SHALL 以新的 `known_at` vintage 保存，不覆盖旧周数值。DataProducts、CLI 和 snapshot manifest SHALL 支持 latest、`as_of` 和全 vintage 查询，并返回报告日期、选择来源、质量警告和 lineage。

#### Scenario: Q2 blended growth changes between weekly reports
- **WHEN** 连续两期报告对 `2026Q2` 给出不同的 blended EPS growth
- **THEN** latest 查询 SHALL 返回新一期，`--as-of` SHALL 返回当时已知版本，`--vintages` SHALL 同时返回两期

#### Scenario: Historical PDFs are backfilled after the fact
- **WHEN** 系统在今天导入过去日期的本地 PDF
- **THEN** `known_at` SHALL 反映系统首次实际取得该版本的时间，报告日期 SHALL 作为事件/来源日期保留
- **AND** 回填 SHALL NOT 伪造过去某个实时决策已经能够看到该数据

### Requirement: Consumers use a stable DataProducts snapshot and do not fetch the source
系统 SHALL 提供 typed Earnings Insight 快照产品，返回指数读数、行业截面、文档引用、质量、freshness 和 lineage。Macro Workflow SHALL 直接消费指数及盈利质量信息；Sector Workflow SHALL 消费 GICS 截面作为顶部行业背景。两者 SHALL NOT 直接访问网络、PDF、本地 Obsidian 路径或结构化物理表。

#### Scenario: Macro prepares its weekly review
- **WHEN** 已有一份通过发布门的最新 FactSet snapshot
- **THEN** Macro SHALL 从 DataProducts 得到与当前 `EarningsBackdrop` 等价或更完整的确定性上下文
- **AND** SHALL 在输出中保留 estimated/blended/actual、报告日期和质量警告

#### Scenario: Sector reviews AI hardware layers
- **WHEN** Sector 读取 FactSet 行业截面
- **THEN** 它 SHALL 将 GICS 数据标为 top-down 市场背景
- **AND** SHALL NOT 把 GICS 行业指标伪装为 AI Hardware L1-L8 层级、个股基本面或 Chain 独立证据

#### Scenario: Downstream decision agents need macro context
- **WHEN** Chief、Risk 或 PEAD 需要盈利环境信息
- **THEN** 它们 SHALL 继续通过已持久化的 Macro/Sector review 上下文间接消费
- **AND** SHALL NOT 直接再次读取或加权 FactSet snapshot

### Requirement: Collection precedes weekly review through an explicit scheduled stage
系统 SHALL 在周度 Macro→Sector 评审之前，以独立、可观测的数据准备阶段采集 FactSet。该阶段与消费者读取 SHALL 由不同 feature flag/release mode 控制；消费者不得以读取动作触发网络采集。

#### Scenario: The scheduled refresh succeeds
- **WHEN** 周度数据准备作业在评审前取得并发布新报告
- **THEN** 本周 Macro 和 Sector SHALL 读取该报告对应的 snapshot

#### Scenario: The scheduled refresh fails but an older release exists
- **WHEN** 本周采集失败且存在上一期已发布 snapshot
- **THEN** 消费产品 SHALL 返回上一期数据并明确标记 stale、报告日期和失败状态
- **AND** Workflow SHALL 按自身降级规则继续，而不是把旧值称为本周新数据

#### Scenario: No released snapshot exists
- **WHEN** 数据集尚未发布任何合格观测
- **THEN** Macro 和 Sector SHALL 得到 unavailable/registered-no-data 状态并省略对应结论
- **AND** 整个周度评审 SHALL NOT 因 FactSet 缺失而崩溃

### Requirement: Consumers receive analysis-ready FactSet evidence
系统 SHALL 在同一期已发布快照上构造面向分析的材料，而不是仅把快照压缩为遗留摘要。Macro 材料 SHALL 包含全部适用且已发布的指数观测、按主题分组的确定性诊断，以及至多六段从受治理逐页正文中确定性选择的证据。每段正文证据 SHALL 保留报告版本、页码和字符范围；系统 SHALL NOT 把整份 PDF 正文默认发送给模型。

#### Scenario: Macro analyzes the current report
- **WHEN** `082826` 的 25 条适用指数观测已经发布
- **THEN** Macro SHALL 收到全部 25 条观测，而不是只收到遗留 `EarningsBackdrop` 的字段子集
- **AND** 系统 SHALL 确定性计算 EPS/营收增长差、EPS/营收 surprise 差、正负指引比例及净差、forward/trailing P/E 相对 5 年和 10 年均值的偏离
- **AND** Macro 报告 SHALL 分别分析增长质量、集中度、surprise、利润率与指引、估值与市场预期、来源冲突/限制及板块含义，并列出实际使用的 observation ID 和正文页码

#### Scenario: Narrative evidence is selected
- **WHEN** 已发布报告包含集中度、GAAP/Non-GAAP、行业贡献、利润率或估值评级说明
- **THEN** 系统 SHALL 按已注册主题和页面章节选择最多六段有界证据
- **AND** 选择过程 SHALL 是确定性的、可测试的，不得依赖模型现场检索或读取未发布文档

#### Scenario: Layered Sector review completes its eight layer verdicts
- **WHEN** Sector 已独立完成八个基于公司/产业证据的单层判断
- **THEN** 最终跨层汇总 SHALL 读取最新正式 Macro review 及有效 release mode 允许的 FactSet GICS 背景
- **AND** SHALL 明确报告二者与八层结论的一致、分歧及对跨层加减建议的影响
- **AND** Macro 或 GICS 背景 SHALL NOT 回写、替换或重新计算任何单层配置或个股判断

#### Scenario: Offline review uses local governed context
- **WHEN** 操作人员使用 offline 模式运行 Macro 或 Sector review
- **THEN** 系统 SHALL 禁止现场网络抓取，但仍读取本地已发布 DataProducts 和已持久化 workflow memory
- **AND** 报告 SHALL 明确区分网络数据缺失与本地 FactSet/Macro 上下文的 release、stale 或 unavailable 状态

### Requirement: Source charts remain internal evidence rather than redistributable output
原始 PDF 和 FactSet 图表 SHALL 按内部研究资产保存并保留版权、来源和官方 URL。Agent 正式数值输入 SHALL 优先使用结构化观测；系统生成的新图表 SHALL 由已发布观测重绘。除显式内部报告外，默认输出 SHALL NOT 复制或对外发布 FactSet 原图。

#### Scenario: An internal weekly report needs visual verification
- **WHEN** 操作者查看一个结构化行业数值的来源
- **THEN** 系统 MAY 展示带 FactSet 标识的原始图表区域并链接官方 PDF
- **AND** SHALL 同时展示对应 observation、质量状态和证据定位

### Requirement: Migration is gated by current-report source quality and consumer regressions
系统 SHALL 以最新 `082826` 报告建立正文和行业图表验收集，并在隔离库完成 source validation、quality report、lineage、consumer smoke 和 rollback drill。历史 PDF MAY 保留为受控重处理输入，但 SHALL NOT 构成当前发布门槛或历史消费结论依据。指数正文指标 SHALL 可独立先行发布；行业图表指标只有在该期标注样本逐单元格 100% 准确且完整性检查通过后才可切换到 platform。

#### Scenario: An operator reviews the current-report sector golden dataset
- **WHEN** 系统为 `082826` 生成 Sector 图表候选值
- **THEN** 系统 SHALL 产出按图表分组的可审阅标注包，其中每个候选至少包含 `chart_id`、页码、GICS entity、列、规范化值、单位、原始 token 和 image-region evidence
- **AND** 操作者 SHALL 能逐图表接受、修正或标记不可判读的候选，而无需手工定位图像坐标
- **AND** `SP500` 总体行 SHALL NOT 写入 Sector golden cells

#### Scenario: Golden cells are used for sector release validation
- **WHEN** `082826` 的人工审阅标注被标记为 complete
- **THEN** 系统 SHALL 将每个适用 extractor cell 与同一 `(chart_id, entity, column, period, estimate_state, unit)` 的 golden cell 比较
- **AND** SHALL 在缺失、额外、重复、单位/期间/状态不匹配、值不匹配或未决人工标记时使 `sector_core` 保持 shadow
- **AND** 只有逐格 100% 一致、11 个 GICS 行和预期列完整时才允许通过该分区的 release gate

#### Scenario: Completion evidence is independent and report-specific
- **WHEN** 操作者声称 `082826` 的 Sector acceptance 已完成
- **THEN** 系统 SHALL 提供该 PDF 的完整验收报告，证明独立 decoder 从原始图像产生候选 cells，且 231 个适用 cells 均与人工 golden cells 一致
- **AND** decoder SHALL NOT 读取 golden manifest、人工确认值或其派生数据作为输入
- **AND** 每个 golden/candidate cell SHALL 含真实图像区域，不得以整页、整行或占位区域替代

#### Scenario: A sector chart omits an otherwise registered metric column
- **WHEN** `082826` 或后续报告未披露某一已注册 Sector 指标列，例如行业 `revenue_surprise`
- **THEN** 系统 SHALL 将该列记录为 `not_applicable`，并保留图表/模板证据
- **AND** SHALL NOT 以零或估算值补齐，或将该未披露列作为当前报告 Sector release 的必需列

#### Scenario: A sector growth chart includes a prior comparison date
- **WHEN** `082826` 的行业 EPS growth 图同时显示 `Today` 与 `30-Jun` 值
- **THEN** 系统 SHALL 仅发布 `Today` 为 `earnings.eps.yoy_growth`，并将其 `comparison_date` 归一为报告发布日 `2026-08-28`
- **AND** SHALL 将 `30-Jun` 保留为图表修正证据，而非发布为第二条 Sector observation

#### Scenario: Index metrics pass but chart extraction does not
- **WHEN** 正文核心指标通过验收而行业图表仍有单元格错误或模板不稳定
- **THEN** 系统 SHALL 只发布指数指标并保持行业部分 shadow
- **AND** Macro MAY 切换，Sector SHALL 保持现有路径或不消费缺失字段

#### Scenario: The platform consumer is rolled back
- **WHEN** Macro 或 Sector 的 platform 读取出现回归
- **THEN** 操作者 SHALL 能通过 consumer release mode 回滚读取而不删除已采集 PDF、document versions、candidates 或 structured vintages
