## Purpose

定义 Anthropic Economic Index 职业与任务数据的受治理采集、规范化、质量、更新及消费契约，为 Evidence Observer L1 提供可追溯且不可误读为真实员工采用率的 AI 工作应用证据。

## ADDED Requirements

### Requirement: 数据产品明确表达来源口径与能力边界

系统 SHALL 将官网 Usage % 表达为某职业或任务占对应 Claude source product 总使用量的份额，SHALL NOT 将其命名或解释为从业者采用率。官网 Industry SHALL 映射为 SOC occupational major group，SHALL NOT 映射为企业所属 NAICS 或 GICS 行业。Observed Exposure SHALL 被标记为研究快照而非月度 Usage Share 序列。

#### Scenario: 消费者读取职业 Usage Share
- **WHEN** 消费者查询某职业的 `ai.work_adoption.usage_share`
- **THEN** 结果 SHALL 使用 percent 单位并说明分母是对应 Claude source product 的总使用量
- **AND** 结果 SHALL NOT 声称该值代表该职业从业者中使用 Claude 的比例

#### Scenario: 消费者读取 Industry 维度
- **WHEN** 消费者查询官网展示的 Industry 节点
- **THEN** 系统 SHALL 返回对应 SOC occupational major group 及代码
- **AND** SHALL NOT 将该节点标记为 NAICS、GICS 或雇主行业

### Requirement: 官方发布发现固定到不可变版本

系统 SHALL 通过公开、无需鉴权的 Anthropic Hugging Face repository metadata 发现 commit SHA、最后修改时间和完整文件清单。候选 release SHALL 符合 `release_YYYY_MM_DD` 命名，并同时包含该 release 的数据文档、Claude.ai 月度文件和 1P API 月度文件；持久化血缘 SHALL 固定到 commit SHA 和完整上游文件哈希，SHALL NOT 以可变 `main` URL 作为最终来源版本。

#### Scenario: 发现完整新 release
- **WHEN** metadata 中出现同时具有数据文档、Claude.ai CSV 和 1P API CSV 的新 release 目录
- **THEN** 系统 SHALL 记录 repository commit、release 目录、文件路径和 commit-pinned URL
- **AND** 每个来源版本 SHALL 包含 commit、release、文件名和完整文件哈希

#### Scenario: release 文件尚未齐全
- **WHEN** 新 release 目录缺少数据文档或任一月度产品文件
- **THEN** 系统 SHALL 将该候选标记为 `not_yet_published`
- **AND** SHALL NOT 将不完整 release 发布为最新可用数据

### Requirement: 下载、解析与持久化限定为 Global 职业和任务切片

系统 SHALL 流式下载每个来源文件，在解析前校验文件大小上限、Content-Length、完整 SHA-256、CSV 表头和已支持 release schema。系统 SHALL 仅准入 `geo_id=GLOBAL`、`geo_level=global`，以及 `soc_occupation` level 0、`soc_occupation` level 1、`onet` level 0 的记录；所有其他地理、分类或层级 SHALL 不进入首版 observation。

#### Scenario: 大文件流式处理
- **WHEN** 月度 CSV 在允许大小内但不能安全整体载入内存
- **THEN** 系统 SHALL 使用有界内存逐行解析并在下载过程中计算完整文件哈希
- **AND** SHALL 保存过滤后 query slice 及其独立内容哈希

#### Scenario: 文件包含国家和州记录
- **WHEN** 上游 CSV 同时包含 Global、Country 和 State/Province 行
- **THEN** 规范化 observation 中非 Global 行数 SHALL 为零
- **AND** 对账 SHALL 只以满足 Global 与分类层级白名单的源行作为合格分母

#### Scenario: 文件截断或 schema 漂移
- **WHEN** Content-Length、文件哈希、必需表头或 release schema 校验失败
- **THEN** 系统 SHALL 阻止对应 slice 发布并返回 `validation_failed`
- **AND** SHALL 保留失败原因而不发布部分解析结果

### Requirement: Claude.ai 与 1P API 始终形成隔离的 source product 序列

每条月度 observation SHALL 具有 `source_product=claude_ai` 或 `source_product=1p_api`。两个 source product SHALL 使用独立 artifact、运行结果和 series identity；一个产品失败 SHALL NOT 阻止另一个通过，且查询 SHALL NOT 在产品之间补值或默认合并。

#### Scenario: 单一产品下载失败
- **WHEN** Claude.ai 文件通过全部校验而 1P API 文件下载或解析失败
- **THEN** release 运行 SHALL 返回 `partial` 并发布已通过的 Claude.ai slice
- **AND** 1P API 状态 SHALL 独立显示失败原因且不得使用 Claude.ai 值补齐

#### Scenario: 相同职业和月份存在两个产品值
- **WHEN** Claude.ai 与 1P API 都发布同一职业、月份和指标
- **THEN** 系统 SHALL 保存为两个不同 series
- **AND** SHALL 分别绑定各自产品文件 artifact

### Requirement: SOC 和 O*NET taxonomy 使用稳定标识建立版本化层级

系统 SHALL 从 Anthropic 官方仓库的 O*NET task statements 和 SOC structure 建立 major group、occupation 与 task 实体关系。Task ID SHALL 与 AEI `node_external_id` 对接，O*NET-SOC Code SHALL 对接详细职业，SOC code 前缀 SHALL 对接 occupational major group；名称只可用于展示和一致性告警，SHALL NOT 作为主键连接依据。

#### Scenario: 任务 7382 建立层级关系
- **WHEN** taxonomy 包含 Task ID `7382` 及其 O*NET-SOC 归属
- **THEN** 系统 SHALL 将 `ONET_TASK:7382` 关联到 `SOC:15-2031.00` Operations Research Analysts
- **AND** SHALL 将该职业关联到 `SOC:15-0000` Computer and Mathematical Occupations

#### Scenario: 名称变化但稳定 ID 不变
- **WHEN** 新 taxonomy 版本调整 task 或 occupation 展示名称但稳定 ID 未变化
- **THEN** 系统 SHALL 继续识别同一实体并追加新的 taxonomy version
- **AND** SHALL NOT 因名称变化新建重复实体

#### Scenario: task 映射缺失
- **WHEN** AEI 中出现无法映射到 occupation 的有效 Task ID
- **THEN** 系统 SHALL 保留该 task entity 和 observation 并标记 warning
- **AND** SHALL NOT 静默丢弃该 task 或使用名称猜测关系

### Requirement: 原始字段规范化为明确单位的工作采用指标

系统 SHALL 按以下语义注册并保存 Provider 原始字段，同时在 raw payload 中保留原字段和值：`pct` 为 `ai.work_adoption.usage_share`；automation/augmentation bucket 为对应 share；directive、feedback loop、task iteration、validation、learning、none 为对应 collaboration share；work use case 为 `work_use_share`；AI autonomy mean 为 `autonomy_mean`。百分比指标 SHALL 使用 percent，autonomy SHALL 使用 `scale_1_5`。

#### Scenario: 规范化 collaboration 指标
- **WHEN** Provider 行包含 `collaboration_feedback_loop_pct`
- **THEN** 系统 SHALL 发布 `ai.work_adoption.collaboration.feedback_loop_share`
- **AND** SHALL 保存 percent 单位、原字段名、原值、产品、分类、层级、release 和 methodology 维度

#### Scenario: 未列入首版白名单的指标
- **WHEN** Provider 发布教育年限、耗时、artifact type 或其他未注册指标
- **THEN** 系统 SHALL NOT 将其发布为首版 observation
- **AND** 已保存的 commit-pinned 来源指针 SHALL 支持未来按新规则重新解析

### Requirement: Observed Exposure 与 task penetration 作为独立研究快照

系统 SHALL 分别采集 `labor_market_impacts/job_exposure.csv` 和 `labor_market_impacts/task_penetration.csv`，将源文件 0–1 值保存为 `ai.work_adoption.observed_exposure` 和 `ai.work_adoption.task_penetration`。其 period SHALL 使用研究或文件首次发布时间，`period_basis` SHALL 为 `research_snapshot`，SHALL NOT 计算月环比或并入月度趋势。

当 task penetration 源文件未发布 Task ID 时，系统 SHALL 使用任务文本内容哈希建立 source-native research-task 实体，明确标注稳定 O*NET 映射不可用，SHALL NOT 以任务名称强行连接 O*NET taxonomy。

#### Scenario: 查询 exposure 快照
- **WHEN** 消费者查询一个具有 Observed Exposure 的职业
- **THEN** 系统 SHALL 返回 ratio_0_1 原始比例、snapshot period 和来源版本
- **AND** SHALL NOT 将该值乘以 100 后冒充月度 Usage Share

#### Scenario: SOC 粒度不同
- **WHEN** exposure 的 6 位 SOC 与 O*NET-SOC `.XX` occupation 存在父子粒度关系
- **THEN** 系统 SHALL 保留两个不同实体及显式关系
- **AND** SHALL NOT 强行把它们合并为同一实体

### Requirement: Observation 支持幂等、修订和精确来源血缘

每条 observation SHALL 保存 source、dataset、entity、metric、value、unit、period、period start/end、period basis、published/known/fetched time、准确 artifact、dimensions、raw、quality status 和 content hash。完全相同重跑 SHALL 不新增 observation；同一期间内容变化 SHALL 追加 vintage，且 `as_of` 查询 SHALL 可返回修订前值。

#### Scenario: 相同 release 重复采集
- **WHEN** 上游文件和规范化结果与已保存内容完全相同
- **THEN** 系统 SHALL 返回 `no_change`
- **AND** SHALL NOT 新增重复 observation 或 relation version

#### Scenario: 上游修订既有月份
- **WHEN** 新 commit 修改一个已发布月份的值
- **THEN** 系统 SHALL 追加带新 known time 和 artifact 的 vintage
- **AND** 修订发布前的 `as_of` 查询 SHALL 继续返回旧值

### Requirement: 来源专属质量门在 slice 发布前执行

系统 SHALL 对每个 source-product slice 独立执行质量门：合格源行与规范化记录对账率为 100%；百分比在 `[0,100]`，autonomy 在 `[1,5]`，exposure/penetration 在 `[0,1]`；automation 与 augmentation 均齐全时总和容差为 ±0.15 个百分点；全部 collaboration pattern 齐全时采用相同容差；Task ID 到 occupation 的关系覆盖率健康目标为至少 99%。当官方 task taxonomy 落后于月度 release 时，低于目标 SHALL 产生带版本信息的 warning、保留未映射 task observation 且禁止名称映射；未经解释的覆盖下降、重复值、schema 漂移、文件截断或哈希异常 SHALL 阻止对应 slice 发布。

#### Scenario: 隐私过滤导致 cell 缺失
- **WHEN** taxonomy 中存在任务但官方数据没有发布对应 cell
- **THEN** 系统 SHALL 返回 `not_published_or_privacy_filtered` 或等价缺失状态
- **AND** SHALL NOT 将缺失转换为零使用

#### Scenario: collaboration 总和超出容差
- **WHEN** 一个完整 collaboration pattern 集合的百分比总和偏离 100 超过 0.15 个百分点
- **THEN** 对应 slice SHALL 失败质量门并阻止发布
- **AND** 质量报告 SHALL 指出产品、期间、节点和各输入值

### Requirement: 更新按 release event 驱动并报告可操作状态

系统 SHALL 支持每周 metadata discovery，同时将业务 cadence 声明为 `release_event`。没有新 release SHALL 返回 `no_change` 而非 stale；健康信息 SHALL 展示 last checked time、latest upstream commit、latest ingested release、latest available period、latest run status 及每个 source product 的独立状态。

#### Scenario: 周检没有新 commit 或文件
- **WHEN** metadata 与最近成功采集版本相同
- **THEN** 系统 SHALL 更新 last checked time 并返回 `no_change`
- **AND** SHALL NOT 因 Anthropic 未承诺固定发布时间而标记 stale

#### Scenario: metadata 不可访问
- **WHEN** metadata API 超时、限流或不可达
- **THEN** 系统 SHALL 返回 `unreachable` 和可操作错误上下文
- **AND** SHALL 保持最近已发布数据可查询

### Requirement: 数据产品提供 L1 snapshot 与职业 drill-down

系统 SHALL 提供按 source product、period 和 `as_of` 查询的 work-adoption snapshot，以及按 occupation、source product、period 和 `as_of` 查询的 job profile。默认结果 SHALL 只包含 accepted/warning observations，并包含口径、缺失覆盖、methodology、taxonomy、质量和 lineage；每次 L1 Observer 使用 SHALL 可保存可重放的 snapshot manifest。

#### Scenario: L1 获取月度 snapshot
- **WHEN** Observer 请求 Claude.ai 的 2026-05 snapshot
- **THEN** 系统 SHALL 返回 SOC major groups、详细职业、Usage/Automation 横截面、任务覆盖结构和可计算的月度变化
- **AND** SHALL 附带实际 observation 集合、派生版本和 lineage

#### Scenario: 查询职业 profile
- **WHEN** 研究者请求 `15-2031.00` 在指定产品和月份的 job profile
- **THEN** 系统 SHALL 返回职业及 major group、job 指标与变化、关联 tasks、task 指标、未观察 tasks、exposure 快照和逐项血缘
- **AND** SHALL 通过 taxonomy 版本说明每条父子关系的来源

### Requirement: L1 消费者遵守证据陈述边界

Evidence Observer SHALL 只通过受治理的数据产品读取本来源，不得直接访问 Hugging Face 或物理表。事实层可以陈述 Claude 使用量的职业分布、自动化占比变化和可见任务覆盖变化；SHALL NOT 由本数据直接推断员工采用率、岗位替代数量或企业席位渗透。首版 SHALL NOT 将本数据直接注入 PEAD、Chain、Macro、Sector、Chief 或交易决策 workflow。

#### Scenario: Usage Share 上升
- **WHEN** 某职业的 Usage Share 环比上升
- **THEN** Observer MAY 陈述该职业占对应 Claude 产品使用量的份额上升
- **AND** SHALL NOT 将其改写为该职业员工采用率上升

#### Scenario: 交易 workflow 尝试直接消费
- **WHEN** 首版中 PEAD 或其他交易决策 workflow 请求该数据集作为决策输入
- **THEN** 系统 SHALL NOT 默认接入或赋予证据权重
- **AND** SHALL 要求另行定义消费者契约和决策边界

### Requirement: 首版回填与端到端验收可隔离复现

首版 SHALL 在隔离数据库和 artifact 目录中独立回填 2026-04、2026-05 的 Claude.ai 与 1P API 月度数据，并完成 source validation、ingestion、quality、availability、job profile、lineage 和 release preflight。2026-04 以前抽样周数据 SHALL 不并入连续月度序列。

#### Scenario: 2026-06-26 fixture 回填
- **WHEN** 使用固定的 2026-06-26 metadata 和数据 fixtures 执行隔离验收
- **THEN** 系统 SHALL 发现 commit-pinned release 并分别入库 2026-04、2026-05 两个产品的数据
- **AND** SQL、DataFrame、snapshot 和 job profile 对相同 observation 集合 SHALL 一致
