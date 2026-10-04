# data/refresh-orchestration Specification

## Purpose

将受管数据源的更新意图落实为可观察、可恢复的实际刷新运行，使持久化数据的定时、事件发现及按需补缺不会因为只配置了 cadence 而被误报为自动更新。

## Requirements

### Requirement: 更新策略必须绑定可执行的刷新所有者

系统 SHALL 为启用自动更新的每个受管数据集声明唯一的刷新所有者、触发类型、适配器入口、预算和新鲜度策略，并 SHALL 校验该所有者实际能够发起采集运行。受控外部调度器 MAY 充当所有者，但仅有静态配置而无有效调用绑定时，系统 MUST 报告 `unbound`，不得声称自动刷新已启用。事件日历负责调度 Workflow，不得被误计为受管研究数据的刷新所有者。

#### Scenario: 仅配置了更新周期

- **WHEN** 一个启用的数据集有 cadence 配置，但不存在可执行的刷新绑定或可核验外部调用
- **THEN** 数据集状态 SHALL 为 `unbound`，且其自动更新验收 SHALL 失败

#### Scenario: 外部调度器拥有刷新任务

- **WHEN** 受控外部调度器按数据集身份向受管持久化采集队列提交任务，并提供部署绑定和运行记录
- **THEN** 系统 SHALL 将其识别为触发 owner，将每次提交关联到数据集和来源，并 SHALL 仍由唯一 queue worker 执行采集

### Requirement: 刷新触发必须幂等且受来源策略约束

系统 SHALL 对手动、cadence、来源事件发现和允许的 cache miss 持久化刷新使用稳定触发身份，并 SHALL 全部先提交统一受管持久化采集队列，避免旁路调用或重复调用形成多个逻辑刷新。只有持有有效 queue task/lease 的 worker MAY 调用持久化采集 adapter；worker SHALL 在执行前应用来源启用状态、权限、请求预算、最短间隔和并发约束。不同来源的刷新状态 SHALL 独立，单个来源失败不得伪装为整个数据产品成功刷新。

#### Scenario: 重复触发同一刷新窗口

- **WHEN** 同一数据集、来源和刷新窗口被重复提交
- **THEN** 系统 SHALL 只保留一个逻辑运行，重试 SHALL 复用其身份并显示最终结果

#### Scenario: 刷新超出来源预算

- **WHEN** 一次刷新将违反该来源的权限或请求预算
- **THEN** 系统 SHALL 拒绝或延后该刷新，记录原因，且 SHALL NOT 调用来源适配器

#### Scenario: 新增/目标化入口的手动、定时和事件触发

- **WHEN** 本 change 新增或明确纳入目标路径的来源，可由操作员、Refresh Controller 或日历事件触发刷新
- **THEN** 这些新路径 SHALL 提交同一种受管 queue task 并由相同 worker 执行，且 SHALL NOT 直接调用 adapter 或写入持久层；既有 legacy caller SHALL 记录在差距清单，本 change 不要求修复或迁移它们

#### Scenario: Worker 崩溃后恢复

- **WHEN** worker 在持有任务 lease 时崩溃且任务没有进入合法终态
- **THEN** lease 到期后任务 SHALL 可由 worker 恢复或重试，且逻辑任务身份、attempt 历史和已产生副作用引用 SHALL 保持可追溯

### Requirement: 刷新结果必须与发布状态分离

系统 SHALL 记录触发、开始、完成、失败、跳过和重试的运行状态，以及来源、数据集、时间窗、原始资产、准入结果和发布版本引用。抓取成功但质量门拒绝发布、来源失败或结果过期时，系统 SHALL 保留旧可用版本及明确的缺口/陈旧状态，不得将抓取尝试当成新数据发布。

#### Scenario: 抓取成功但准入失败

- **WHEN** 适配器取得原始材料，而质量门将其隔离
- **THEN** 刷新运行 SHALL 指向原始材料和拒绝原因，数据产品 SHALL NOT 宣称该材料已发布

#### Scenario: 来源失败后读取

- **WHEN** 最近一次刷新失败而消费者请求数据
- **THEN** 消费者 SHALL 得到现有版本及其 as-of、陈旧/缺口状态，或得到显式不可用结果；系统 SHALL NOT 伪造当前版本

### Requirement: 刷新与按需读取必须保持清晰边界

系统 SHALL 将持久化结构化/非结构化来源的刷新与 Runtime Data Gateway 的即时查询分开。cache miss 仅在数据集策略允许时 MAY 触发受管采集；普通 Agent 读取 SHALL NOT 绕过准入直接写入数据层，runtime 行情或期权查询 SHALL NOT 被当成持久化数据集刷新。

#### Scenario: Agent 请求缺失的公司研究包

- **WHEN** 受管公司数据缺失且策略允许按需补缺
- **THEN** 系统 SHALL 通过受管刷新路径发起采集，并在准入前返回缺口或旧版本状态

#### Scenario: Agent 请求当前期权链

- **WHEN** Agent 请求当前期权链
- **THEN** 系统 SHALL 通过 Runtime Data Gateway 查询，且 SHALL NOT 生成持久化采集 queue task、raw artifact 或研究数据集刷新运行

### Requirement: 持久化数据来源必须显式登记在权威注册表

Data Catalog SHALL 仅通过 `config/data/catalog.yaml` 这一机器入口装配配置；该入口 SHALL NOT 自身定义 provider source 或 dataset。每个写入受管持久化数据层的结构化或非结构化来源 SHALL 分别在 `config/data/structured.yaml` 或 `config/data/unstructured.yaml` 中具有唯一、稳定的规范 source identity 及其数据集/文档范围。实体、ticker、报告期、文件 URL 和单次触发身份 MUST NOT 被用作逐实体动态 source identity 来替代规范登记。旧 registry 可用于迁移、兼容与 lineage，但不得作为新持久化来源的唯一注册依据。runtime-only 行情/期权输入不属于持久化 registry。

#### Scenario: 持久化消费者使用未登记来源

- **WHEN** Layer Analyst 或其他目标受管消费者请求的持久化来源尚未在两个权威 registry 之一登记
- **THEN** 覆盖审计 SHALL 将其标记为缺项，刷新/发布 SHALL NOT 将其视为已治理来源，且 Agent SHALL 得到显式缺口状态而非静默直连 Provider

#### Scenario: 来源仅存在于兼容 registry

- **WHEN** 一个旧来源仅出现在 `sources.yaml`、`news_sources.yaml` 或动态 source ID 中
- **THEN** 系统 SHALL 将其列入迁移对照并保留历史 lineage，但 SHALL NOT 将其报告为已完成权威注册

#### Scenario: 总入口重复定义来源

- **WHEN** `catalog.yaml`、legacy overlay 与领域 registry 对同一活跃 source/dataset 重复定义或产生冲突
- **THEN** catalog 加载 SHALL fail closed 并报告冲突位置，不得按加载顺序静默覆盖

### Requirement: 固定文档来源必须保留可重放身份和权威原文血缘

需要持久化的 SEC filing 与业绩电话会材料 SHALL 通过明确登记的固定来源链发现和获取，不得将通用搜索结果或新闻网页作为自动生产 fallback。系统 SHALL 保存固定来源 revision/更新时间标记、原始资产 hash、实体/财季、文档身份、发布时点及准入结果。SEC filing metadata 与 SEC 官方原文 SHALL 可区分并通过 accession/官方 filing URL 关联；电话会 SHALL 按 ticker、fiscal year 与 fiscal quarter 匹配。

固定 `data/US/` 来源的覆盖范围 SHALL 只计算美股目标标的，不以 `005930.KS` 作为该来源的缺口。`filing_url` 若为 SEC accession 目录，系统 SHALL 从同一 accession 的 SEC 官方 submissions 或 filing index/完整 submission 解析相应角色的文件并重新校验官方 URL，目录本身 MUST NOT 当作正文。固定数据集 SHALL 在每次检查时验证当前上游 revision 与 manifest/hash；当其仍是上游最新版本且目标财季可读时，单纯距上次发布已过多日 MUST NOT 造成陈旧拒绝。

#### Scenario: 固定来源缺少目标季度电话会

- **WHEN** 注册的 transcript 来源没有目标 ticker 与财季的完整材料
- **THEN** 运行 SHALL 返回明确缺失/不可用状态，不得自动改用搜索引擎或新闻网页正文发布为已准入 transcript

#### Scenario: SEC filing 索引记录指向官方原文

- **WHEN** 固定来源返回 SEC filing metadata
- **THEN** 持久化文档 SHALL 保留 accession 和 SEC 官方 filing URL，并独立记录 metadata 来源与原文获取/准入结果

#### Scenario: 固定数据集发布新修订

- **WHEN** 固定来源的数据集 revision 或 `spec.json` 更新标记变化
- **THEN** 系统 SHALL 记录新的 revision/hash 与采集时点，并 append 新候选或修订；不得覆盖历史来源快照或伪称内容未变化

#### Scenario: DefeatBeta 提供的是 accession 目录

- **WHEN** 可信索引行的 `filing_url` 只有官方 accession 目录而没有文件名
- **THEN** 系统 SHALL 由 SEC 官方 submissions 或 filing index/完整 submission 找到同一 accession 中与 form/文档角色相符的正文，仅在官方文件 URL 和原文通过准入后发布；官方站点不可达时 SHALL 保留索引但不发布正文

#### Scenario: 上一财报季后固定来源未更新

- **WHEN** 刷新成功确认最新上游 revision 与 hash 未变，且已发布电话会/索引覆盖要求的上一财报季
- **THEN** 运行 SHALL 报告 `no_change` 或有效读回，不得仅因上游更新时间超过固定天数报告迁移失败

### Requirement: SEC 官方原文失败必须可见但允许为空

`sec_edgar_filing_body` / `sec_filing_documents` SHALL 作为可选官方原文输入。获取或解析失败时，系统 SHALL 保留实际失败状态、原因、阶段、时间及 queue task/ingestion run 引用，并在无合格正文时返回空值与显式缺口。系统 MUST NOT 将失败改记为成功或健康 `no_change`，也 MUST NOT 生成虚假已发布文档、版本或血缘。已有合格历史版本 MAY 按既有 as-of 契约读取，但不得冒充本次刷新成功。此例外 SHALL NOT 改变结构化财务报表、电话会、SEC 索引和其他来源的验收要求。

#### Scenario: SEC 官方原文网络或解析失败

- **WHEN** SEC 官方原文获取或解析失败，且没有符合读取范围的合格正文
- **THEN** 来源运行 SHALL 保留失败记录，消费者 SHALL 得到空正文与错误/缺口信息；此来源缺失 SHALL 标注为已接受非阻塞缺口，不得自动启用来源 job 或触发实际切流

#### Scenario: 可选 SEC 正文实际取得但未通过准入

- **WHEN** SEC 正文已取得，但官方 accession/URL、身份、语义或质量校验不通过
- **THEN** 系统 SHALL 拒绝发布并保留原因，返回可选材料缺口；可选策略 MUST NOT 降低发布质量门或允许搜索结果替代原文
