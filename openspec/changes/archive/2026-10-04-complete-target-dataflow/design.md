## Context

参见 [proposal.md](proposal.md) 的动机与两份 delta specs。目标图及 Agent 对接见 `docs/TARGET_WORKFLOW_DATAFLOW.md` §3–4、§12。当前 `src/ats/data` 已有统一 catalog、结构化/非结构化采集与数据产品、runtime 查询、独立 source/consumer rollout mode 和部分 cutover 对账；`src/ats/execution/state_api.py` 提供内部状态读取。它们是待验证的现有实现，不是需要重建的空白模块。

配置采用“一入口、两领域文件”：`config/data/catalog.yaml` 是 Data Catalog 唯一机器加载入口，只声明 schema/version 和对 `structured.yaml`、`unstructured.yaml` 的装配关系；`structured.yaml`（结构化来源/数据集）与 `unstructured.yaml`（非结构化来源/文档类型）是持久化来源定义的唯一权威文件。当前后者仍将细节委托给 `sources.yaml`、`news_sources.yaml`，且事件流程会按 symbol 动态登记 SEC/transcript source；这些是兼容/现状，不符合目标。Layer Analyst 的逐源 A/B/C 对照见 `docs/validation/UNSTRUCTURED_REFRESH_INVENTORY.md`，其中配置、部署启用、真实采集、发布和 Agent 读回应分别留证。旧 Evidence Observer 不再作为目标角色或独立消费者；它仍有价值的来源与中性数据产品归入 Layer 的产业链数据包。

`config/data/schedules.yaml` 的原始状态仅描述更新意图与预算，配置本身不创建 job。用户已指定本机 macOS `launchd` 为数据刷新控制器的部署环境；必须另以已加载服务、最近 heartbeat 和逐源运行记录证明绑定有效。Phase E 的 Schedule Calendar 调度分析任务，不等于持久化研究数据的 Refresh Controller。部分数据文档和历史 OpenSpec change 的状态可能滞后于当前代码，验收以运行证据为准。`tests/` 已按仓库约定排除 Git；可在本地运行检查，但可移植的验收入口、摘要和脱敏证据必须保存在可版本化位置。

逐源工作清单见 `docs/validation/UNSTRUCTURED_REFRESH_INVENTORY.md`。当前 Catalog 返回 10 个非结构化来源：5 个带日/周周期的文本来源（其中 Yahoo 仅为 IBKR 故障兜底）、3 个被旧 registry 误归类的月度数值序列、2 个未声明周期的 RSS 条目。清单是实施和取证索引，不以其文字状态代替运行验收。

## Goals / Non-Goals

**Goals:**

- 构建覆盖 Target Dataflow 每个关键节点/连线的可审阅清单，精确定位现有能力、缺口和 owner。
- 让数据刷新策略与真实执行绑定，并区分刷新尝试、准入、发布及消费者可见版本。
- 形成数据域 × 直接消费者的读取契约、旧新对账和可撤销的 Phase F 读取资格。
- 完整定义目标 §4 中 Data Products→Agent 的数据侧契约，并验证角色只能经批准的 Data Products、Runtime Data Gateway、Internal State API 或两条明确的 Task Projection 依赖取得输入。
- 对未通过项保持稳定读路由；让 Phase F 能逐路径消费门禁，而不是相信总开关。

**Non-Goals:**

- 不重做 Phase A 的中性证据写入或历史存储迁移，不重做 Phase C 的 Clerk、Phase D 的 Agent 业务逻辑、Phase E 的事件日历；但这些角色的数据读取契约和边界仍属于本 change。
- 不把 runtime 市场输入改造成持久化结构化数据，不允许 Agent 直连 Provider 补缺。
- 本 change 不实际切换 Phase F 的读取、调度或 live Trader 路由，不物理删除旧表或文件。
- 不在本 change 宣称 Workflow、Agent 与 Dataflow 的全系统联调已完成；待三侧实现稳定后可用独立集成验收 change 复核跨 Phase 行为，但不得替代本 change 的数据接口和隔离门禁。

## Decisions

### 1. 覆盖矩阵是声明，验证结果是证据，二者分开

在受版本控制的清单中列出 Target 图节点/关键连线、数据域、直接消费者、实现/配置路径、唯一 owner、旧入口和拟执行验证。验证器读取清单与实际运行结果，将状态和缺口写入追加式证据记录；人不能仅改清单就把状态设为 `verified`。每条证据记录包含代码/config 指纹、数据范围、as-of、命令/环境摘要、结果、时间与前置证据引用。可发布脱敏摘要和重放命令到专项 change/运维文档；原始材料及敏感日志仍留在受管本地存储。

备选是单一人工 Markdown 表。它易审阅，但无法防止过期证据或遗漏连线，因此只作为由机器记录生成的人类视图。

### 2. Refresh Controller 复用现有采集入口，不另建第二套采集管道

给 catalog/schedule 配置增加可校验的刷新绑定：数据集、来源、trigger/cadence、执行 owner、权限/预算、freshness SLO。Refresh Controller 只负责判断 due work 并向统一持久化采集队列提交稳定任务，不直接调用 adapter；本 change 新增/目标化的受管手动 CLI、来源事件发现、定时触发和允许的 cache miss 也必须提交同一种队列任务。新路径的 queue worker 是调用对应 adapter 及写入 raw→gate→publish 链路的唯一执行 owner。其运行记录引用 queue item、采集、artifact、准入和发布 ID；不替代结构化/非结构化管道。Agent 读请求不得同步绕过队列和质量门。没有部署绑定/队列运行记录时标 `unbound`，不能宣称自动刷新。

用户明确限定本 change 的实现范围为新目标路径：已有旧 scheduler、旧 CLI、Agent/Workflow 直调和 legacy adapter caller 若有旁路，仅调查并记录 owner、入口和风险，不在本 change 修复、迁移或以其作为新路径验收门槛。新旧入口在覆盖清单中明确区分，避免将 legacy 现状误报为目标路径能力。

队列首期采用本地 SQLite durable ledger + lease worker，由本机 `launchd` 唤醒；它不是进程内存队列。任务身份至少绑定 `source_id + dataset/document scope + trigger kind + trigger window/event identity + policy version`，重复提交复用逻辑任务，失败按预算退避重试，崩溃后 lease 到期可恢复。手动命令可以 enqueue 后等待结果，但不能使用隐藏的 direct 模式绕过队列。Runtime Data Gateway 的价格、期权和券商即时读取不产生 queue item。

备选是直接接入 Phase E Calendar、允许各 CLI 直调 adapter 或新建常驻网络队列。前者混淆“数据更新”和“研究任务触发”，第二种留下不可审计旁路，第三种对单机首期运维过重；首期采用 launchd + SQLite durable queue，随后可替换部署 owner 而不改变任务契约。

#### 2.1 非结构化来源按实际材料类型和来源身份逐个绑定

- `factset_earnings_insight_doc`：使用统一的 FactSet 语义月报入口，使 PDF 文档与结构化 Index/行业结果共享一次抓取，但保留各自准入及发布状态。`com.ats.schedule` 是唯一周期 owner；生产环境设置 `ATS_FACTSET_SCHEDULE_SEMANTIC=1` 后注册 `factset_monthly_ingest`，按纽约时间月末触发并以 `factset-month-end:YYYY-MM` 幂等。不得另配周报 job 或要求等待自然周六。月报的 PDF、指标发布质量由 FactSet 专项的逐月验收记录证明，不能由调度触发成功推定。
- `trendforce_news`：将文章发现、正文获取与清洗从 `chain.articles.collect_articles` 的 LLM/观点提取中拆出，形成 data-only 受管写入入口。索引翻页、关键词过滤、正文容器/付费墙、最低字数、正文预算、去重与失败记录分别验收；Chain/Information 只读取已发布文档。
- `semianalysis`：复用已有 IMAP/RSS 研究采集与共享文档写入，但提供来源限定的生产运行入口，不以隔离验收命令冒充生产调度。全文邮件优先于 RSS 预览；游标仅在完整且持久化成功的批次推进，预览标为 partial，不能被消费者误当全文。
- `ibkr_news`：需要 TWS 可达与供应商权限，按已配置标的和切片读取。区别健康零新闻、来源不可达与部分切片失败；正文、标题实体关联、供应商原生 ID、重复转载分别留痕。只在不可达或失败切片时向 Yahoo 发出范围明确的受管 fallback 触发。
- `yfinance_live_news`：不作为独立日更主源；只消费 IBKR 的故障/切片范围。既有人工标题/URL 审阅策略不能被自动任务默认批准；未审材料保留候选和阻断原因，不发布为已准入版本。
- `kr_semiconductor_exports`、`tw_ic_exports`、`dram_contract_price`：虽然兼容 registry 把它们列在非结构化来源中，实际是数值序列。分别绑定 `kr_ecos_exports`、`tw_mof_exports`、`trendforce_dram` 的现有结构化受管入口和月度周期，不再另建文本管道；校验 Catalog 身份映射及计量口径、原始资产、修订 vintage。
- `SECEdgar8K-Coherent` 与 `SemiAnalysis` RSS 条目：当前无周期。前者先明确事件发现/实体与 SEC 官方文件身份，后者与小写 `semianalysis` 归并去重；在来源策略明确前不得被误报为独立自动刷新 job。

#### 2.2 一个加载入口、两个持久化领域注册表

- `catalog.yaml` 是 loader 的唯一入口，但不是第三份来源注册表；它不得重复声明 provider source 或 dataset。它只装配 `structured.yaml` 和 `unstructured.yaml`、校验 schema/version/交叉引用并输出统一只读目录。
- 将所有被受管数据层持久化的来源和数据集登记在 `structured.yaml` 或 `unstructured.yaml`。来源身份使用稳定 source ID；symbol、季度、文件 URL 等属于运行范围或来源项，不得拼入每标的一条动态 source ID 来代替注册。非结构化来源所需的 provider、adapter、数据集/文档类型、获取边界、预算、周期、fallback 条件、权限/保留政策和质量门应在 `unstructured.yaml` 有可审阅登记，不得仅靠 legacy overlay 才能解释来源。
- `sources.yaml`、`news_sources.yaml`、`config/settings.yaml` 和 `com.ats.schedule` 只用于发现旧 source ID、旧入口和迁移状态；迁移过程中可读但不再作为新持久化来源的注册真相。迁移必须保留历史 ID、alias、lineage 和安全回滚，不能直接删除已写入数据的来源身份。
- 对每个 Layer Analyst 来源制作 A/B/C 清单：A=目标 registry 已登记且旧采集入口/owner 仍有实际运行；B=目标 registry 已登记但旧定时流程未启用、无现行旧 owner 或只存在代码入口；C=旧消费者/采集代码实际依赖持久化材料而目标 registry 缺项或 source 身份不合格。配置有 cadence/schedule 不等价于运行，`platform` rollout 也不等价于来源最近成功或消费者已读回。
- 固定 SEC/电话会来源链：DefeatBeta [Yahoo Finance 数据集](https://huggingface.co/datasets/defeatbeta/yahoo-finance-data) 作为版本化发现/数据源候选，落盘时记录 dataset commit/revision、`spec.json` 时间/hash、原始文件 hash、ticker 与财季。其 `stock_sec_filing` 记录只作为 filing metadata/index；SEC 正文须用 accession/官方 `filing_url` 从 SEC EDGAR 获取，并作为独立原文来源保存。电话会从 `stock_earning_call_transcripts` 按 ticker + fiscal year + quarter 读取完整稿及 speaker/segment。不得让 Tavily/搜索引擎或新闻网页静默回退并进入已准入持久化材料；人工研究入口可以保留，但必须与生产发布路径隔离。
- DefeatBeta 当前仅有 `data/US/`；目标固定源的标的范围限美股，`005930.KS` 不再计作该来源的覆盖缺口，韩国/台湾产业链数值输入仍按各自来源管理。其 `filing_url` 实际可为 SEC accession 目录而非正文文件；正文获取复用 SEC 提取器的精确主文件、官方 filing index 与完整 submission 解析，并按官方 host/path 和文档角色校验。电话会/SEC 固定快照的新鲜度以每次刷新查到的最新上游 revision、manifest/hash 和目标财季覆盖为准；财报季间上游无新发布不因绝对日龄判为陈旧，来源检查失败或目标财季缺失则仍 fail closed。
- 把 Layer 使用的本地产业链知识文档作为 repository-managed curated corpus 登记，采用 Git revision、路径和内容 hash 跟踪，无需创建外部抓取 job。旧 Evidence Observer 的 Census、RPS、Anthropic、Ramp、Sacra、TickerTrends、OpenRouter 与能力基准来源不再形成独立 Agent 输入契约；经 Layer 职责审阅后保留的部分归入 `HIER_DATA`，不相关者继续作为一般数据产品或退役。TickerTrends 当前作为结构化营收观察输入；只有在明确保留原始文章正文时，才另登记非结构化文档 source，不能混淆两种产品。
- 旧采集状态以运行证据核验：FactSet 唯一 owner 仍是 `com.ats.schedule`，生产注册 `factset_monthly_ingest` 月末调度。配置中的 `factset_weekly_ingest` 是历史命名的启用开关；启用 `ATS_FACTSET_SCHEDULE_SEMANTIC=1` 时，实际 cron、job ID 和幂等触发键均为月末语义，不是周报周期。`weekly_review=false`，因此 SemiAnalysis 的旧 `research.ingest_configured` 代码仍在，但不是当前定时 owner。`news_sources.yaml` 的 `SECEdgar8K-Coherent` URL 是 Coherent RSS 占位值，不是 SEC feed；不得作为 SEC 覆盖证据。

#### 2.3 持久化采集统一入队

##### SEC、财务报表与电话会的提取复用（2026-09-26）

- 不再用 `fixed_sources.py` 混装 Provider 与管道职责。`pipelines/unstructured/document_ingest.py` 只负责队列后的采集编排、准入、发布与审计；`pipelines/unstructured/sec.py` 负责 SEC 来源策略；`pipelines/unstructured/transcripts.py` 负责电话会段落/财季校验；`sources/defeatbeta.py` 负责固定 revision 的 manifest 与 Parquet 读取。来源 ID、历史快照表和文档存储位置不因代码重命名而迁移。
- SEC 复用 `data/sec.py` 的已验证提取逻辑：精确主文件、filing index 中声明的文档类型、EX-99 财报稿识别及完整 submission SGML fallback。目录不得当正文；8-K/6-K 财报稿与周期性监管报告采用不同语义。官方 submissions 不是唯一可行解析路径，也不能把网络故障与内容缺失混为一谈。
- 用 task-local transport 注入新路径的官方 accession URL 边界、请求预算、响应大小、限速与重试；旧调用方默认行为不变，不全局 monkeypatch。失败保留来源/阶段/原因；未成功的官方正文不得发布。
- 财务数值复用 `sources/company_financials.py` 的映射、币种、ADR/拆股和期间语义。生产 DefeatBeta 地址改为 `data/US/stock_statement.parquet`，查询前固定 manifest revision，原始查询切片保留 revision、spec/file hash，不能拿“当前 manifest”说明另一个版本的查询。
- 电话会复用 `data/defeatbeta.py` 的说话人及 Prepared Remarks/Q&A 渲染；识别新旧 ordinal 字段，拒绝缺序号、重复和断裂，不把日历季度猜作财季。渲染变化正常追加新文档版本；运行发布引用只列本次最新版本，不混入全部历史版本。
- 新操作命令为 `ats data document-ingest --source ...`；旧 `fixed-ingest` 保留为同一路径的 CLI 兼容别名，二者均入队。SEC 网络实测若失败，保留错误及 job 未启用状态；按下述用户确认的可选输入例外记录“已接受非阻塞缺口”，不再要求真实正文抓取成功才能切流，也不能以测试通过冒充来源上线通过。

所有会产生持久化 raw、candidate、observation、document、fact 或发布版本的任务都使用同一 `PersistentIngestionTask` envelope，至少包含 `task_id`、稳定幂等键、`source_id`、dataset/document scope、trigger kind/ref、requested as-of、policy/config fingerprint、priority、attempt/lease 和父运行引用。生产 adapter 入口只接受 worker 持有的有效任务上下文；无法证明任务来源的直接调用 fail closed。

队列状态区分 `queued | leased | succeeded | no_change | quarantined | partial | retry_wait | failed | cancelled`，且运行结果继续与发布结果分离。手动、定时、事件、cache miss 的差别只体现在 trigger metadata 和优先级，不产生四套写入路径。事件改期或配置变化产生新幂等范围，旧任务按规则取消/失效并保留历史。队列只承载持久化采集，不承载 Agent 工作调度，也不承载 runtime 查询。

#### 2.4 文本采集、质量门与发布分层

当前 `ats data ingest` 是结构化入口，`source-acceptance` 是只读诊断，而旧 Chain 文章路径混合采集和 LLM 提取；三者不能直接互相冒充。为文本来源增加 data-only 的可调用采集入口，尽量复用 adapter、共享不可变文档仓库与既有来源质量规则。每个候选保留发现身份、原始材料或原始失败、正文清洗版本、长度/完整性、关联实体、去重键与来源权限；准入拒绝写 quarantine 和原因，人工待审单独标记。通用财报 `CandidateDocument` 的必填 fiscal period 不适合新闻/研究文章，不能为了通过验证伪造季度；文章应有独立但可追踪的准入契约，并复用同一受管文档版本与产品读取层。LLM 事实抽取在准入之后运行，不能成为抓取或质量门的隐式副作用。

控制器只宣布触发和采集结果；来源级 `no_change`、空而健康、partial、unreachable、unauthorized、budget_deferred、quarantined、pending_human_review、published 必须可区分。来源运行需关联 raw/candidate、准入决定、文档版本和发布引用；没有发布引用时仍返回旧版本及 as-of/缺口。凭据或使用条款未满足、TWS 不可达、付费墙、人工待审时 fail closed，不能用 mock 或静态配置把 job 标为生产已通过。

#### 2.5 Data Products 与目标角色逐边对接

以 `docs/TARGET_WORKFLOW_DATAFLOW.md` §4 为规范，覆盖清单不得只登记一个泛化 `WORKFLOW` consumer，而要将每条数据边落实为独立的产品契约：

| 角色 | 数据侧输入契约 | 非 Dataflow 依赖边界 |
|---|---|---|
| Layer | `HIER_DATA` + 已准入 `DOC_DATA` | 输出 `LayerAnalysis` 到 Memory |
| Information | 已准入 `DOC_DATA` | 输出 `InformationBrief`；不得从 Memory 伪装读取文档事实 |
| Sector | `HIER_DATA` | 只额外读取 `LayerAnalysis` |
| Fundamental | `COMPANY_DATA` + `HIER_DATA` | 只额外读取 `InformationBrief` |
| Macro | `MACRO_DATA` | 不读取其他分析师观点 |
| Technical | runtime `MARKET_DATA` | 不把实时查询写成研究持久化数据 |
| Chief | `PORTFOLIO_DATA` + `HISTORY_DATA` | 可读取六类分析投影，是唯一观点汇总者 |
| Risk | `PORTFOLIO_DATA` + runtime `MARKET_DATA` + `RISK_RULES` | 读取交易提案，不读取分析师观点 |
| Trader | 经审批的 execution authorization | 只写券商，不消费研究 Provider |
| Clerk | broker state + 决策/审批 Memory | 写交易账本并发布内部状态读模型 |

每个产品契约声明 read API、owner、schema/version、as-of/vintage、完整性/缺口语义、允许消费者、fallback 和旧入口。持久化研究包只能经 Data Products，实时包只能经 Runtime Data Gateway，组合/交易/绩效/决策历史只能经 Internal State API。`ats.memory.*` 不得作为已准入外部研究文档的正式读取 API；底层 repository/table 也不得暴露为 Agent 合同。

本 change 对上述数据边和禁止边签发数据侧资格，并验证两个明确 Task Projection 例外；不负责重写各 Agent 的推理、prompt 或输出业务逻辑。待 Phase D/E/F 实现稳定后，独立集成验收 change 可以验证完整 Workflow 时序、投影内容和跨进程恢复，但不能放宽这里的接口、来源和隔离要求。

旧 Evidence Observer 不出现在目标 consumer manifest 中。迁移时建立 tombstone/alias，审阅其数据源：与 Layer 的产业层级、实体关系、截面对比和命题证据有关的来源并入 `HIER_DATA`；其他来源保留为一般数据产品或按零消费者流程退役。不得把旧 Observer 和新 Layer 同时运行成两个分析角色。

### 3. 路径验收采用小范围可重放样本，再逐域扩围

对结构化财务/宏观/行业序列、非结构化公告/新闻/电话会、runtime 行情/期权、内部账户/交易状态分别建立样本与边界用例。每个持久化样本追踪 source→raw→gate/quarantine→版本/事实→product→consumer，并覆盖失败、陈旧、拒绝、修订/as-of 和隔离。旧新读对账必须固定实体、范围、vintage、as-of；分类差异为预期语义变化、数据覆盖差异或错误，未解释差异不得通过。对于 runtime/内部状态，仅验证查询 owner、时间戳、完整性和下游消费，不伪造持久化 artifact。

备选是只检查单元测试或只做一次端到端 happy path。它们都无法证明异常语义与消费者边界，因此不足以发放切流资格。

### 4. 门禁资格按数据域与消费者绑定，且可失效

#### SEC 原文可选输入例外（2026-09-26 用户确认）

- 范围仅为 `sec_edgar_filing_body` / `sec_filing_documents` 的官方原文（含该来源提取的监管报告和财报稿）。不扩展到 SEC 索引、结构化财务报表、电话会或其他财报来源；它们原有验收要求不变。
- 获取或解析失败仍保留 source/run 状态、错误原因、阶段、时间和 task/run 引用；未取得或未通过准入的正文返回空值及显式缺口，不将失败改成 `succeeded` / `no_change`，不生成虚假的文档、版本或证据。已有合格历史版本可按原 as-of 契约返回，不能冒充本次成功。
- 覆盖清单把“来源实际状态”与“是否阻塞切流”分开：来源可仍为 failed/unavailable、正文 0 发布；策略记录为 optional、non-blocking / 已接受非阻塞缺口。消费者的其他必需证据通过且空值/错误处理已验证时，不得仅因 SEC 原文缺失拒绝其 `eligible`。无需等待 SEC 网络恢复或真实原文成功才能签发资格。
- 当 SEC 原文实际存在时，官方 accession/URL、身份、语义、准入和血缘检查仍必须通过；可选并不允许发布错误材料或使用搜索结果填补正文。消费者不得把空值当作“没有风险”或已完成 SEC 审阅。
- 本例外不豁免其他来源失败、消费者边界/回滚缺证据、Workflow 必要分析或 Chief—Risk—Boss—Trader 审批要求，也不意味着 change 全部完成。SEC job 不因本例外自动启用；运行失败与受管重试策略保持独立。
- 本轮仅更新规划；需在 apply 阶段同步覆盖配置、资格计算及空值/错误可见性测试，记录例外对应的策略版本。不得只改 Markdown 就宣称运行时门禁已生效。

读取资格记录使用 `domain_id + consumer_id + contract_version` 作为范围，附清单版本、代码/config 指纹、验证运行 ID、最近 as-of、回退路由与演练证据；状态为 `eligible` 或带原因的 `ineligible`。资格检查只读，不隐式切换 `read_mode`。Phase F 的切流入口在切换前调用资格检查；任何依赖指纹变化或证据过期都 fail closed。现有 source/consumer 开关继续独立，且回退到已知稳定路由，不恢复已退役的 Workflow Memory 事实表。

备选是把资格写成全局布尔 flag。那会把一个消费者的通过错误推广到所有消费者，也无法安全处理 schema/来源更新。

### 5. 最小修复与退役登记遵循差距矩阵

只对 `needs_refactor`、`missing` 项创建有 owner、兼容迁移和回滚的修复任务。`verified` 项只保留回归证据；`blocked` 项注明外部条件与下游影响。旧入口需先证明无消费者、无未对账数据、回滚窗口结束，再标记可退役；本 change 不执行物理清除。若数据平台本身已有相同审计表/运行模型，扩展或复用它，而不是平行建表。

## Risks / Trade-offs

- [外部 scheduler 部署不可见，静态配置可能被误认成自动执行] → 需要部署绑定和最近运行/失败证据，否则 `unbound`，不签发资格。
- [FactSet 被新旧两个定时器同时触发] → 交接前不配置新 PDF job；记录旧 owner 停止、新 owner 启动、同一窗口不重复、回滚和 live Workflow 不受影响的证据。
- [两个 YAML 看似是注册表，但运行时仍从 legacy overlay 隐式补来源或按 symbol 动态登记] → 建立两文件来源完整性检查与 A/B/C 对照；新持久化来源必须显式登记，运行时动态实体范围不得改变来源身份。
- [catalog.yaml、structured.yaml 与 unstructured.yaml 被误解为三套 registry] → `catalog.yaml` 只允许装配元数据，source/dataset 定义只能存在于两个领域文件；加载器和 lint 拒绝重复定义及 legacy-only 活跃来源。
- [旧 scheduler、旧 CLI 或 Agent/Workflow caller 绕过队列] → 逐项记录 legacy owner、消费者和风险；本 change 只验证新目标路径的 queue task context、worker-only 写链及运行负例，不修复或迁移既有 caller。runtime gateway 单独豁免且验证零持久化副作用。
- [Data-Agent 覆盖只验证泛化 WORKFLOW 节点，遗漏具体角色输入或允许旧 Memory/底层表读取] → 对目标 §4 每条产品边建立独立 consumer contract 和禁止边扫描，缺任一边不得签发该角色资格。
- [旧 Evidence Observer 与 Layer 重复成为角色或数据消费者] → Observer 建立退役墓碑，来源逐项迁入 `HIER_DATA`、一般产品或零消费者退役清单，consumer manifest 只保留 Layer。
- [第三方固定数据集延迟、修订或缺期被误当作官方/完整材料] → 记录不可变 revision/hash 和 upstream update marker；SEC 正文独立校验官方 accession/URL，电话会严格按财季核对，缺失时显式报缺而非搜索替代。
- [通用 transcript loader 的搜索 fallback 将未经稳定来源登记的正文发布到持久化仓] → 固定源路径与人工搜索路径隔离，只有注册来源通过准入后才能生成已发布版本。
- [旧文章采集与 Chain LLM 混合，或只读验收被误当发布] → data-only 采集/文章准入单独实现，观点提取只读已发布资产，运行状态分别记载。
- [IBKR fallback、订阅预览和人工审阅边界被自动调度绕过] → 保留来源条件与授权、partial/人工待审标签；Yahoo 不独立全量扫描且不自动越过人工审阅。
- [逐域/消费者矩阵很大，验收耗时] → 先覆盖 Phase F 的直接读取消费者，并对未覆盖项保持 `missing` 或 `blocked`；不得用样本结论外推全域。
- [旧新语义不完全等价] → 固定相同 as-of 和范围，记录差异原因；未解释差异 fail closed。
- [外部来源或券商数据不可在 CI 中稳定取得] → 使用受管脱敏快照做可重放契约验证，并在真实环境保存新鲜度/执行证据；mock 不能单独证明生产刷新或切流资格。
- [回滚到旧路由可能依赖已退役数据] → 门禁要求实际演练稳定回退路径；不存在回退时保持不具备资格。
- [证据记录含原文或账户敏感信息] → 版本化文件仅保存 ID、hash、时间和脱敏摘要，原文留在受管存储。

## Migration Plan

1. 冻结目标图版本，盘点现有组件及来源/消费者路由，建立覆盖清单；按两份权威领域 registry 对 Layer Analyst 的持久化数据逐源分类 A/B/C，并给每项确定稳定 source ID、旧 owner/入口、目标 owner 和验收证据；登记 Evidence Observer 退役映射；先不改变读取行为。
2. 验证现有采集、准入、产品、runtime、内部状态路径。对已通过项登记证据；对缺口实施最小修复并复测。
3. 将持久化来源完整登记/收敛到 `structured.yaml` 或 `unstructured.yaml`，`catalog.yaml` 只装配两者，将旧 registry 留作迁移对照而非新 source 真相；建立 launchd 驱动的 durable queue，将手动、定时、事件和 cache-miss 持久化采集入口全部改为 enqueue，由唯一 worker 执行。按 A/B/C 清单逐源验证。SEC filing metadata 与官方正文、DefeatBeta transcript 固定版本各自验收；不通过搜索引擎补入正式持久化材料。未绑定或未授权数据集保持 `unbound`。FactSet 先保留旧 owner，确认安全窗口、无双重触发和可回滚后才交接新 owner，不变更 live Workflow 调度。
4. 按目标 §4 为每个角色建立 Data Product/Runtime/Internal State consumer contract，移除 Information 经 Memory 读取外部文档和 Layer 单一知识文件契约等不完整接口；迁移 Evidence Observer 来源并登记角色墓碑。按域/消费者做旧新对账、异常测试和回滚演练，发布或拒绝只读资格。所有现有消费者继续走原稳定路由。
5. 将资格查询接口和 runbook 交给 Phase F；Phase F 再分别处理读取、调度和交易切流。回滚本专项只需撤销资格或恢复刷新 owner/配置，不会触发交易路径变化。
