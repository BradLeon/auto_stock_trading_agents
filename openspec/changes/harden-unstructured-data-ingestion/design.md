## Context

见 proposal.md。现有适配器直接把“获取成功”视作“内容可信”，而真实运行证明长度、电话会结构标记和最新 URL 都不足以判断公司、财季与文档类型。共享资产层已经具备逻辑文档、不可变版本、实体关联和分块检索，因此本设计保留其主结构，把修复集中在采集准入、来源路由和质量状态。

## Goals / Non-Goals

**Goals:**

- 让下游默认只能看到 accepted 文档，同时保留被拒候选及完整失败原因供审计。
- 以确定性来源键和目标财报事件约束实体、期间与时效，不用 LLM 充当数据准入闸。
- 使结构化纪要、实时新闻、正式披露和订阅研报各自使用最适合的来源与完整性策略。
- 保持现有 Agent 查询接口和历史文档可读取，并支持按阶段独立运行和验收。

**Non-Goals:**

- 不在本变更中修改 PEAD、Evidence、Sector 或交易决策逻辑。
- 不用向量相似度或 LLM 猜测公司身份/财季；它们可以做后续增强，但不能覆盖确定性失败。
- 不承诺 defeatbeta/Yahoo Finance 对所有全球实体完整覆盖；缺失必须由来源回退和质量报告显式呈现。
- 不在本变更中重写现有向量库或知识图谱。

## Decisions

### 1. 引入 candidate → validation → accepted/quarantined 状态机

适配器只负责产生候选和来源 provenance。中央 validator 根据文档语义执行 identity、period/event、type、completeness 四组检查，并返回全部 reason codes。accepted 才进入现有 `source_documents`/version/chunk 主路径；quarantined 保存候选元数据、可选原始隔离路径和原因，不进入默认产品查询。

选择中央准入而不是把规则散落在适配器内，是为了保证 Tavily、SEC、IR、IMAP 和结构化 Parquet 对同一种文档遵守同一契约。来源特有解析仍留在适配器，但不能自行绕过强校验。

### 2. 文档语义与载体格式正交，旧类型用映射兼容

新写入使用清晰语义；现有 `article/news/release/deck/transcript/filing` 通过映射读取。第一轮不批量改写历史 catalog，避免大规模迁移和路径变化；只有新版本写入新语义。查询层允许同时按旧值和新语义过滤。

替代方案是立刻迁移所有历史类型和路径，但真实数据中已有误分类，批量转换会把旧错误固化，因此不采用。

### 3. 财报事件 ledger 是期间与“最新”的唯一锚点

每个 covered entity 先解析最近已发生或明确目标的 earnings event（report date、fiscal year/quarter）。来源候选必须绑定这个 event，而不是搜索“latest”。事件优先来自结构化 earnings calendar/纪要记录，其次公司配置和官方 filing；冲突进入健康报告。

这也消除空字符串、`TODO`、`Q FY2026` 被当成“无法核对所以放行”的行为。无法形成完整 event 时，自动网页候选全部隔离。

### 4. defeatbeta 是结构化纪要主源，但不是最终权威

适配器通过 DuckDB 对远程 Parquet 做 predicate pushdown，按 symbol + fiscal year + quarter 精确查询；同时读取 `spec.json` 记录快照更新时间。保存时保留原始段落/speaker 结构的快照，并生成统一 Markdown 正文。

公司 IR/人工文件仍可作为权威覆盖。defeatbeta 缺失、快照陈旧或校验失败时按已声明回退顺序继续；Tavily 只发现 URL。数据集是第三方镜像，因此来源健康必须显示 snapshot lag，且不能静默覆盖官方材料。

### 5. 正式披露先按 accession/event 定位，再抓正文

SEC 查询从 earnings event 日期附近寻找 8-K/6-K/20-F/10-Q 候选，并校验 CIK、form、exhibit 名称及正文业绩语义。没有 `ex99` 或等价正式附件时不再回退到最大 HTML。IR deck 仅接受注册表声明的官方域名或人工文件，搜索引擎结果必须额外通过公司与期间校验。

Earnings release 与 periodic filing 是两个资产角色：前者是 8-K/6-K 中经确认的 EX-99.x 业绩附件，后者是 10-Q/10-K 的 primary document。二者分别保存 accession、form、源 URL 和独立 document id；不得用整份 10-Q 冒充 release，也不得因已有 release 而跳过 10-Q/10-K。

正式披露读取顺序为：精确事件键缓存 → 对旧 `unknown-release`/旧类型缓存做重新准入并迁移 → SEC 新下载。旧资产迁移必须重新核对 SEC provenance、公司、期间和业绩语义，避免把历史宽松路径留下的错误文件直接升级。

SEC 网络访问由单一传输边界负责有限重试和阶段化错误。附件发现优先 filing index；index 不可用时可从完整 submission 的 `<TYPE>`/`<FILENAME>` 确定 EX-99 与 primary document，不能按最大文件猜测。所有路径不可达时写入来源健康状态，缓存命中与本轮新下载分别计数。

SEC 官方 submissions 元数据优先承担候选发现与角色约束，并保留 `items`、`primaryDocument`、form、filing/report date、CIK 和 accession。第三方 filing parquet 作为可缓存镜像与降级入口，但不得因其快照缺行或暂时不可达而阻止查询 SEC 官方 submissions。美国国内发行人的 8-K earnings release 优先要求 Item 2.02，并结合 Item 9.01、附件描述和正文语义；“事件日最新文件”只能用于排序，不能单独成为准入依据。

同一 filing 的全部 EX-99 与 primary document 都先形成带 description/type 的候选，再按业务角色分类。`press release`/`financial results` 候选可成为 company release；`presentation`、`financial statements`、`statutory interim report` 分别进入各自角色，禁止选择最大 EX-99。外国私人发行人的财报可能直接写在 6-K primary document 中；当它通过实体、事件、财期和 earnings 语义校验时，即使没有 EX-99 也可成为 company release。

财期绑定不再取正文第一个正则命中。解析器收集标题、正文开头、报告截止日和同比/环比语句中的全部候选，并以目标 earnings event、当前年度、`reports/results` 邻近度和 filing items 联合判定。比较期间不得覆盖主报告期；在 CIK、Item 2.02、事件日期和 release 角色均精确匹配时，文本只提供截止日而未写 Qn 的候选可由已验证 earnings event 完成绑定。

报告制度由 SEC filing history 判定并缓存，而不是由交易市场或公司名称猜测：近期 `10-Q/10-K/8-K` 表示 domestic，`6-K/20-F` 表示 foreign private issuer，`6-K/40-F` 表示 Canadian foreign private issuer；冲突状态进入健康报告。domestic 的季度/年度 regulatory filing 分别为 10-Q/10-K；foreign 的季度资产为包含 interim financial statements、operating and financial review 或 statutory interim report 的 6-K 正文/附件，年度资产为 20-F/40-F。外国发行人的中期 filing 可以晚于 earnings release，使用独立、受限的后向窗口和内容角色校验。

### 6. 新闻采用双速通道

IBKR 负责盘中增量：修正为 IBKR 接受的 UTC/时区日期格式，将连接、provider、合约、slice 与 article body 分层捕获失败，`None` 视为局部空结果。defeatbeta/Yahoo News 负责日级回填和历史正文，Finnhub/RSS 继续作为补充。

TWS 是运行依赖而不是持久源；断开时状态为 unreachable，恢复后游标重扫重叠窗口。readonly client id 与交易 session 隔离保持不变。

### 7. Newsletter 用持久游标取代短 lookback

为每个 mailbox/folder/sender 保存最后成功 UID/Message-ID 和运行水位。首次运行按配置 backfill 天数搜索；后续从水位增量读取并保留重叠窗口。只有本轮发现内容全部持久化后才推进水位。采集与下游处理分别记账，`max_articles_per_run` 不得限制入库。

完整性以可解释规则判断：邮件 MIME 正文长度、canonical post、已知 teaser/unlock 标记、正文结束位置。`partial/teaser` 仍可保存，但消费者必须显式允许才能当作完整研报使用。

### 8. Frontmatter 解析只消费文件头的一个闭合边界

解析器先确认文件以 `---\n` 开头，再查找紧随 YAML header 的首个独立 `\n---\n`，正文从该边界之后全部保留，不再对全文做多次 split。新增包含水平线、YAML-like 文本和无 frontmatter 文件的回归测试；读取后校验 catalog chars/hash，发现不一致时报 corrupt 而非静默截断。

### 9. 质量指标成为发布闸

每次阶段一运行输出 source × document semantic 的 discovered、accepted、quarantined、unreachable、zero-match、stale 和 reason-code 统计。真实源 smoke test 与确定性 fixture test 分开：CI 不依赖外网，人工阶段验收运行隔离数据库并检查质量阈值。

首次发布的最低标准：统一读取一致性 100%；自动 accepted 的 identity/period 抽样正确率 100%；失败候选不得进入默认 DataProducts 查询；来源不可达和零匹配可区分。覆盖率不设虚假 100% 目标，但每个缺口必须可见。

## Risks / Trade-offs

- [严格准入会降低短期覆盖率] → 明确展示 quarantined/missing，以“少而可信”替代静默误收；逐步补官方适配器。
- [远程 Parquet 体积与网络波动] → predicate pushdown、按快照时间缓存结果，并提供本地镜像配置；失败时局部降级。
- [Yahoo/defeatbeta 许可或字段变化] → 保留 adapter 边界、记录 schema/version，解析失败标记 source degraded，不污染资产。
- [历史误分类仍存在] → 默认新准入只约束新写入；提供离线审计/隔离命令，确认后再迁移旧资产。
- [Newsletter UID 在邮箱迁移后变化] → 游标同时记录 UIDVALIDITY、Message-ID 与 canonical URL；UIDVALIDITY 变化触发受控回填和去重。
- [官方 IR 域名维护成本] → 以实体注册表集中维护；未知域名只进入候选，不允许搜索引擎自动扩权。

## Migration Plan

1. 先修读取一致性并回归，确保任何后续采集都能完整读回。
2. 增加候选/隔离状态和兼容语义映射，但保持旧读取 API。
3. 接入 defeatbeta 结构化纪要并以隔离数据库验证当前 coverage universe；不立即关闭旧路径。
4. 收紧 transcript、SEC release 和 deck 准入；旧路径降级为候选发现。
5. 修复 IBKR 并启用新闻重叠游标；验证 TWS 开/关两种状态。
6. 上线 Newsletter 首次回填和增量游标；回填窗口以配置控制。
7. 运行一次完整隔离采集，质量报告达到发布闸后才切换生产默认源。
8. 对本次回归只运行正式披露专项：以独立目录验证 legacy release 迁移、earnings release、10-Q/10-K、失败状态、文件版本与默认查询；不重新采集其他非结构化来源。
9. 补充运行 AMZN/MSFT/KLAC 与 TSM/SKHY/ASML/NBIS 的正式披露专项，验证 Item 2.02、全部 EX-99 角色、6-K 主文档、外国中期 filing 与 20-F/40-F 路由；`stock_statement` 留待结构化数据层。

回滚时关闭各新 source/validator feature flag，旧读取和历史 catalog 保持可用；新隔离记录与不可变版本无需删除。
