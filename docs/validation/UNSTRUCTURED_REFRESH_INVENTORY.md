# 非结构化来源逐源刷新与清洗清单

## 本机 launchd 逐源验收（2026-10-02，覆盖 2.2.9）

本节以本机 `gui/501/com.ats.data-refresh` 已加载的 launchd 进程和 `var/data_refresh.sqlite` / `var/persistent_ingestion.sqlite` 实际账本为准；不是 YAML 或 mock 验收。`com.ats.data-refresh` 由真实 launchd invocation 启动。先前 Frontier job 逐个使用 job filter；本次 IBKR 验收时 launchd 拒绝设置 filter 环境变量，临时将该 job 的 due 时间提前后 kickstart controller。账本仅出现 IBKR 的新运行记录，未运行其他来源；触发时间随即恢复为纽约时间 16:30。launchd 当前空闲、上次退出码为 0，filter 环境变量为空。`plutil -lint` 对 data-refresh 与 schedule plist 通过，controller 状态 `binding=active`。三个月度 job 为了逐源验收临时提前触发，验收后已恢复每月 15 日配置。

| Job / 来源 | launchd 最近真实结果 | 预算、权限与 freshness 口径 | 处置 |
|---|---|---|---|
| `ramp_ai_index_p10d_probe` / `ramp_ai_index` | 2026-09-24 `no_change / upstream_no_change`；官方数据可正常读取 | 5 requests/run、30s、最多 3 次尝试；SLO 14d；8 天内，按用户确认，上游无更新是通过而非失败 | 通过，启用 |
| `openrouter_rankings_p7d_ingest` / `openrouter_rankings` | 2026-10-01 `succeeded`，queue `1f30cfba-5c44-5735-bf9a-4bf115ed0ba7` | 14 requests/run、30s、最多 2 次尝试；`.env` 中 API 凭据由 worker 加载；SLO 10d；714 accepted、0 quarantine | 通过，启用 |
| `frontier_ai_capability_model_discovery_p7d` | 2026-10-02 `succeeded`，attempt 2；run `9cd54e561d687f23ae7fe423`、queue `3234a89c-f522-5bb2-9c1a-6d2cbf55c262`；103 discovered / 103 accepted / 0 quarantined；source check 84 models / 103 observations | 最多 11 个公开路由请求；总预算 64/run、30s；SLO 10d。该 launchd run 当时未获得 HTML 授权，六条路由逐条 fail-closed | 当时可运行路由通过；六条 HTML 的后续专项验收见下方补充记录 |
| `frontier_ai_capability_benchmark_probe_p7d` | 2026-10-02 `succeeded`，attempt 1；run `62e5bb269ef4dd9f49dab213`、queue `f47bdd39-f3e1-5b64-b24a-2ba95c0aed22`；103 discovered / 103 accepted / 0 quarantined | 总预算 64/run、30s；SLO 10d；该 launchd run 当时 HTML 授权关闭 | 当时可运行路由通过；六条 HTML 的后续专项验收见下方补充记录 |
| `frontier_ai_capability_full_audit_p7d` | 2026-10-02 `succeeded`，attempt 1；run `5ad9304b400e9e1dda507d31`、queue `5f04c36b-bfae-5c82-80c9-8a92fd2009e5`；103 discovered / 103 accepted / 0 quarantined | 总预算 64/run、30s；SLO 10d；该 launchd run 当时 HTML 授权关闭 | 当时可运行范围通过；六条 HTML 的后续专项验收见下方补充记录 |
| `frontier_ai_capability_official_release_probe_p7d` | 2026-10-02 `succeeded`，attempt 2；run `d59cde763d748c7459927efa`、queue `2b68c076-ba66-542d-a79f-cef6e44eac62`；`frontier_ai_public_model_cards` 560 discovered / 560 accepted / 0 quarantined，9 个公开组织 API 请求 | 1 run、30s；SLO 10d；仅登记公开模型卡存在性，不推断 release date、flagship 或能力分数；不是 closed-model release 完整清单 | 新的公开模型卡数据集验收通过，已启用；Anthropic 当前无公开模型卡结果，属于产品覆盖限制 |
| `frontier_ai_labs_revenue_p7d_refresh` | 2026-10-02 `no_change`，attempt 1；queue `3a9f10f0-ca16-5c39-8b18-1473af51798a`；Sacra、TickerTrends 两来源各有 source check 与 ingestion 记录 | Sacra 2 + TickerTrends 1 request/run、45s；SLO 10d；凭据由 worker 安全加载 | 按内容未变语义验收通过，已启用；沿用已有真实运行，未重复触发 |
| `kr_semiconductor_exports_monthly` / `kr_ecos_exports` | 2026-10-02 真实 queue `b9f12791-dbd7-507a-880c-a4d230ee1b7d`：18 discovered、0 新增、`no_change`；产品最新 2026-08，index 566.38 | 1 request/run、page size 10、30s、最多 2 次尝试；SLO 45d；上游最新月份为 2026-08 | 通过，启用；恢复 day 15 月度周期 |
| `tw_ic_exports_monthly` / `tw_mof_exports` | 首轮 queue `af8e1dbf-1678-5da5-a63c-a64e51a3d66a`、run `e56be3a2596f3d4e2ff9f80f` `parse_failed`；重试同一幂等任务于 09:19Z `no_change`，308 rows / 0 changed | 2 requests/run、60s、最多 2 次尝试；SLO 45d；产品最新 2026-08；失败与重试均留账 | 重试后验收通过，启用；恢复 day 15 月度周期。旧解析路径不在本次修复范围 |
| `dram_contract_price_monthly` / `trendforce_dram` | 2026-10-02 queue `9b70bb74-3e24-5140-9057-77b4c3dcd2fb` `succeeded`，3/3 accepted、0 quarantine；公开表读到 `2H Aug`，期间至 2026-08-31 | 1 request/run、30s、最多 2 次尝试；job SLO 45d 只记账，内容新鲜度按公开免费页面最新发布期判断；本次实际检测到新发布 | 通过，启用；恢复 day 15 月度周期 |
| `defeatbeta_sec_index_daily` / `defeatbeta_sec_filing_index` | 2026-10-02 queue `0772ba13-afa9-50f6-90fc-9f857931d342` `succeeded/index_metadata_only`；沿用全集 29/29 美股覆盖验收 | 3 requests/run、30s、160 rows/run / 4 per symbol、最多 2 次尝试；SLO 3d；只持久化 filing index metadata，不把它说成 filing body | 通过，启用；SEC 正文是独立可选非阻塞缺口 |
| `sec_official_body_daily` / `sec_edgar_filing_body` | disabled，未执行 launchd 来源请求 | 最多 20 documents/requests per run、30s；要求 `SEC_EDGAR_USER_AGENT`；SLO 3d。用户已接受正文允许为空 | 按明确例外保持关闭，不阻塞 2.2.9；不代表正文获取成功 |
| `defeatbeta_transcript_daily` / `defeatbeta_earnings_transcript` | 2026-10-02 queue `c4ee5a33-a8a6-5aa9-8aaf-cc102673bf6b` `no_change`；全集既有 29/29 美股、115 份历史/当前完整版本可读 | 3 requests/run、30s、160 rows/run / 4 per symbol、最多 2 次重试；SLO 3d 指上游 revision 检查，无新财报季版本是正常 no-change | 通过，启用 |
| `ai_hardware_knowledge_daily` / `ai_hardware_knowledge_corpus` | 2026-10-01 queue `5252f15d-d2b3-50f5-b6b5-54a7cc75977b` `no_change`；10/10 已发布版本 | 最多 10 files/run、0 外部请求；SLO 3d；no-change 是内容 hash 未变 | 通过，启用 |
| `trendforce_news_weekly` / `trendforce_news` | 2026-09-28 queue `db91891a-7adb-51c8-b313-0bb381902533` `succeeded` | 5 index pages、最多 16 body requests/run、最多 2 次尝试；SLO 10d；16/16 正文发布 | 通过，启用 |
| `semianalysis_newsletter_weekly` / `semianalysis` | 2026-09-28 queue `27dc989e-2c3e-52b3-bff3-83262db566f3` `succeeded` | 最多 32 items/run、最多 2 次尝试；`.env` Gmail 凭据预检通过；13/13 是注明 `partial` 的订阅预览；SLO 10d | 通过其“预览”数据契约，启用；不得宣称全文 |
| `ibkr_news_daily` / `ibkr_news` → `yfinance_live_news` | 2026-10-02 只读 TWS 诊断连接成功，8 个新闻 provider 可枚举；NVDA 实测 7 个 provider 返回新闻、DJNL 正常返回 0 条、无 API error。launchd run `51cc18dd0a021f7af45b` succeeded，queue `67e91500-371c-5929-8948-bb7aff3395a5`；8/8 注册标的查询、0 failed slices、46 discovered、3 accepted、43 quarantined；三份已发布新闻经 `admitted_documents` Data Product 读回，均关联 MU。Yahoo fallback 未触发 | IBKR 7-day lookback（本次含窗口重叠，记录 effective lookback 9d）/ 3-day slices / 10 body requests/run；8 个注册标的；SLO 3d。准入原因计数有重叠：body below minimum 39、entity association 未验证 31、budget deferred 36。Yahoo 10/entity、24 bodies/run，且要求人工标题/URL 审阅 | 通过，已启用；纽约时间每日 16:30。仅 3 条正文达到发布门槛，其余留 quarantine，不以标题数冒充发布数；Yahoo 条件式 fallback 与人工审阅门仍保留 |

FactSet 不属于此 Refresh Controller 的 17 个 job：仍由唯一 `com.ats.schedule` 月末 owner 负责。2026-10-02 对 2026-09 月报的真实补跑及受管发布记录见 [FactSet 月报运行手册](FACTSET_SEMANTIC_RUNBOOK.md)。2026-10-02 已补齐五个 Frontier job 与 `ibkr_news_daily` 的 launchd 验收。其余启用的刷新 job 均有通过的最近真实运行；SEC 官方正文按用户确认保持 optional/non-blocking 且 disabled，Yahoo 仅作 IBKR 故障范围内的人工待审 fallback。Frontier 六个 HTML 路由已通过一次性受管 durable queue 真实采集与产品读回；2026-10-03 的精确六路由授权也已持久配置到 `com.ats.data-refresh` LaunchAgent，并通过隔离的单 job LaunchAgent、独立日期窗口完成授权后的真实 launchd 来源运行和发布读回（详见下方补充记录）。常规周度 cadence 未改变，下一自然窗口为 2026-10-09 07:25 UTC。

### Frontier 六个 HTML 路由专项验收（2026-10-02，用户批准的一次性采集）

六个路由均由 `frontier_ai_capability` adapter 从公开页面读取：AutomationBench-AA、SciCode、CritPt、MMMU-Pro、Toolathlon Verified、Humanity's Last Exam。最初代码路径对五个 Artificial Analysis 页面仍按旧 JSON-LD `Dataset.data` 形状解析，真实页面只有 Dataset 元数据而将分数放在 Next.js Flight 序列化的 `initialModels` 中；因此前五项曾报 `parser_drift`。现新增只解析结构化 Flight JSON 的入口，不执行 JavaScript、不抓视觉像素、不读取付费 API；严格匹配每个 benchmark 的 score field，字段缺失或数值单位超界继续 fail-closed。增加 `query_scope.benchmarks` 精确筛选，避免为了这六项重复请求其他五个 Frontier 路由。

| 路由 | 传输 | 页面 score 字段 / 解析器 | 真实解析数 | 页面 SHA-256 前缀 |
|---|---|---|---:|---|
| `automationbench_aa` | Artificial Analysis HTML / Next.js Flight | `automationBenchPartialScore` | 21 | `3aee602e9360` |
| `scicode` | Artificial Analysis HTML / Next.js Flight | `scicode` | 21 | `9070d96b4c45` |
| `critpt` | Artificial Analysis HTML / Next.js Flight | `critpt` | 21 | `f05dee9875c0` |
| `mmmu_pro` | Artificial Analysis HTML / Next.js Flight | `mmmuPro` | 15 | `178f1535c993` |
| `toolathlon_verified` | 官方 HTML leaderboard | 原有结构化表格解析器 | 65 | `b1b3980d3952` |
| `humanitys_last_exam` | Artificial Analysis HTML / Next.js Flight | `hle` | 21 | `2ca51bc0b467` |

每条路由单次读取各 1 request，共 6/64 budget；采集前只读诊断确认六项均无 parser drift。之后通过新 durable queue 手动任务 `a07f33a4-69a8-5faf-ba47-2b3277acef3e` 写入：ingestion run `2b4bbdc669b6676adffe23c8`，164 discovered / 164 accepted / 0 quarantined。Data Product 按 artifact 读回应为 21/21/21/15/65/21，合计 164；queue lineage 已关联 ingestion run、1 个 raw artifact、164 个 admission candidate 与 164 个 published observation。隔离重放测试 6 项通过，结构化 ingestion 回归与该解析测试合计 16 passed。第一次队列尝试发生在受限 shell 网络环境中，未发出网络请求且生成 0-coverage run，已将该 queue task 以 cancelled 留档；成功验收来自后续用户授权网络访问后的真实页面采集，并非 mock。

一次性采集由手动受管队列任务完成。2026-10-03 已将 `ATS_FRONTIER_AI_ALLOW_PUBLIC_STRUCTURED_PAGES=1`、`ATS_FRONTIER_AI_PUBLIC_TERMS_APPROVED=1` 和精确的 `ATS_FRONTIER_AI_PUBLIC_HTML_BENCHMARKS` 六路由名单持久配置到 LaunchAgent，并通过重载后的 `launchctl print` 核验生效；没有修改 `.env`，也没有设置全局 job filter。`launchctl setenv` 曾因权限被拒绝，因此改用 LaunchAgent plist 环境配置。常规周度窗口（2026-10-02）在授权前已记为 `succeeded`，刷新账本按稳定窗口身份阻止重复执行；未清除或改写该历史审计记录。为按用户要求立即验收授权后的实际部署，在 2026-10-03 建立仅含下述 Frontier benchmark probe 的临时单 job LaunchAgent，使用独立窗口身份运行，并在终态后恢复原常驻 LaunchAgent；常规周度 cadence 未变。

该独立 launchd run 为 `frontier_ai_capability_benchmark_probe_acceptance_20261003`，窗口 `2026-10-03`，状态 `succeeded`；queue task `4eb52313-00a5-50bb-bd80-6b1e2edb2496`，ingestion run `d9aefefe5a94963699c45bad`，raw asset `2fcb66032d6cdad978423ffd`。本次 164 条候选全部 accepted、0 quarantined；164 个 published observation ID 均由 `frontier_ai_capability_scores(include_vintages=True)` 按 ID 读回。六个 benchmark 分别为 AutomationBench-AA 21、SciCode 21、CritPt 21、MMMU-Pro 15、Toolathlon Verified 65、Humanity's Last Exam 21。任务账本从 `enqueued` → `leased` → `finished/succeeded`，refresh ledger 留存运行、队列、ingestion、raw、admission 与 published 血缘。运行后确认生产 LaunchAgent 已恢复加载且空闲，六路由授权变量仍在，未设置全局 job filter。临时验收计划只含该一个 job，不改变日常每周计划；下一常规周度窗口为 2026-10-09 07:25 UTC。

本次逐源触发前，`OPENROUTER_API_KEY` 和 Gmail 凭据均由 `.env` 安全加载，未输出凭据值。Frontier 的通用授权环境变量不再作为整个 job 的前置总闸，以免连带跳过无需该授权的公开 JSON/Git 路由；HTML 只读路由验收及是否持久化授权以本节补充为准。初次 discovery 只完成了发现态，随后已改为显式 `--ingest-new` 并重试入库。Official-lab 首次真实调用还暴露 CLI 未把 `--dataset` 传给 source 的缺陷：560 条 `public_model_card` 记录曾被接受到 benchmark dataset 下。CLI 现将 dataset 加入 ingestion scope，adapter 对错误 dataset 增加 fail-closed；正确的第二次运行已写入独立 `frontier_ai_public_model_cards`。第一次错误写入保留在审计历史，未物理删除；其指标不是 `frontier_ai_capability.score`，不进入 score matrix，但需在后续账本清理/数据质量核查中单独处理，不能把旧错误运行记作正确发布。相关历史段落是各自日期的快照；若与本节冲突，以本节为准。

## 2026-09-26 换 IP 后最新补验

本节优先于下文早期记录：台湾本次受管采集成功（9 新增/修订、299 未变、0 隔离），重复为 308 no_change；CSV 与产品 308 条数值逐条匹配，最新 2026-08 已读回。原“旧解析失败”不是当前结论，之前还存在 HTTPX 网络异常被泛化为 parse_failed 的分类问题，未修改旧路径。实际来源是电子零组件，历史 IC 产品标签口径偏窄。

SEC 官方正文 NVDA/AMD 共 6 份发布并从产品接口读回，2 份 sec_missing 隔离、0 来源连接失败；NVDA 重复为 3 no_change。共享旧提取器由新受管 transport 调用，包含 filing index / complete submission 回退，不再是下面早期 submissions-only 描述。DefeatBeta 索引首轮失败后，新任务重试为 116 no_change、无缺失实体。隔离保留为 SEC 可选、非阻塞缺口；不等于全量正文或 Task 4.6 通过。台湾/SEC job 本轮未启用，未切换 Workflow。完整 task/run、hash、产品与剩余边界见 [专项验收记录](SEC_FINANCIAL_TRANSCRIPT_ACCEPTANCE.md#换-ip-后真实复验2026-09-26当前结论)。

另按用户已确认口径修正下文 DRAM 历史判断：截图公开免费表的 Last Update 为 2026-07-31；2026-08-31 是订阅材料更新，不应据此判定免费来源漏采。2026-10-02 产品读回确认新路径的 2H July session 观测期间结束于 2026-07-31，属于公开免费版最新数据期；不以旧 45 天 SLO 降级。

## 2026-09-26 新路径重验（早期记录，以上节最新补验为准）

本节当前口径（2026-10-02）：FactSet 使用月末月报调度；DefeatBeta `data/US/` 只验美股，`005930.KS` 不再是该固定源的覆盖需求；财报季间以每次检查到的最新上游 revision/hash 与上一财报季可读性判定，不按文件日龄阻断。SEC 官方正文缺失按用户确认属于可接受的非阻塞缺口。下文早期运行段落为历史证据，不能覆盖此处及各来源最新状态。

| 来源 | 本轮真实证据 | 当前结论 |
|---|---|---|
| `ibkr_news` | TWS 7496 可达；manual queue `7596d5e7-9c14-5412-8744-e33863d48128`，8 标的、44 候选、4 准入发布、40 质量/预算隔离；8 个 provider 可列举、无失败切片，Yahoo 未触发。该次运行总体状态曾记 `quarantined`，因旧状态优先级把混合结果误归类，现已修正为“有准入发布则 succeeded、逐候选隔离仍保留”并经本地回归 | 主源连接、权限、持久化与条件兜底边界通过；日更 job 已启用；候选质量仍保留告警，下一次真实运行需验证修正后的总体状态 |
| `openrouter_rankings` | `.env` 凭据由 queue worker 加载；task `f20d8a39-2dc8-5ded-a9ab-39d322b0593a`，714 accepted、0 quarantine；`openrouter_rankings_daily` 产品可查，source health 为 succeeded | 已启用周更 job；此前“缺 OPENROUTER_API_KEY”判断撤销 |
| `ramp_ai_index` | task `9eac6e3c-133d-5d70-a990-2a1be9b317bd` 真实读取官方页面，两个数据集均报告最新上游期 2026-08-01、scope failures 空、身份/hash 未变 | `no_change` 是正常上游未更新，来源检查通过，不要求凭空新观测 |
| `factset_earnings_insight_doc` | 生产唯一 owner `com.ats.schedule` 于 2026-09-30 启用月末语义入口；9 月月末任务因休眠于 10-02 补跑，首次 `partial`，修复后同一官方 PDF 经受管队列发布成功；Index 43 项/期间、IT 12/12 组读回 | 月末触发、幂等键和 misfire 已真实验证，不再等待周六。其他十行业 P2 与 Sector `registered_no_data` 是独立缺口，不影响 Index/IT 核心月报发布；详见 `FACTSET_SEMANTIC_RUNBOOK.md` |
| `defeatbeta_sec_filing_index` | task `e0f1beb1-1c57-5af5-941f-2fa678e87863` 对最新 HF commit `a46d686…` 重验，116/116 `no_change`、覆盖 29/29 美股、`snapshot_stale=false` | 固定 US 索引来源通过并启用日更；它仍仅是元数据，不是 SEC 正文 |
| `defeatbeta_earnings_transcript` | task `bdcf6c10-d74f-56e0-ae0f-6cb67e7df9ea` 对同一最新 commit 重验，111/111 `no_change`、覆盖 29/29 美股；既有 113 份完整版本可读 | 固定电话会来源通过并启用日更；缺少新财报季发布不算过期 |
| `sec_edgar_filing_body` | DefeatBeta `filing_url` 为 accession 目录，不是原文文件；新路径已改为 SEC submissions 同 accession/form 的 `primaryDocument` 解析，目录不再被当成正文。task `e7e75cdf-ebc8-55e1-8457-898d9d6898bb` 仍为 `ConnectError`；本机代理和直连 SEC 官方域名 TLS 握手均失败 | 正文仍 0 发布，日更 job 保持关闭；阻塞是 SEC 官方网络通路，不能以 HF 索引或搜索替代。目录字段是发现层设计差异，不代表已取得原文 |

上文是 2026-09-26 时点的历史补验结论，现由 2026-10-02 launchd 逐源验收更新：台湾受管新路径的首轮 parse failure 经队列重试后为 308 rows `no_change`；DRAM 新路径已从公开页面发布的 `2H Aug` 表解析并发布，观察期间截至 2026-08-31。不要把上述旧产品期或当时的关闭状态当作当前状态。来源/调度验收仍不自动授予 Layer 或 Phase F 消费者切流资格。

状态日期：2026-09-25。本表是 `complete-target-dataflow` 任务 2.2/3.2 的工作清单，不是来源验收或 Phase F 切流证书。兼容 registry 共 10 行，其中 7 行是文本/RSS 来源，3 行为结构化数值序列别名；旧别名已在 Catalog 中标记 `domain=structured` 并指向规范 source/dataset。5 个文本来源声明了 cadence，其中 Yahoo 是 IBKR fallback；2 个 RSS 无周期。标为“待绑定/待验收”不等于失败来源，也不代表已自动发布。每源必须分别留下真实运行、raw、准入或 quarantine、版本/事实、产品读取及异常证据，才能改变状态。

2026-09-25 registry 实施增量：新 Refresh Plan 只从 `config/data/unstructured.yaml` 的 target entries 解析非结构化 job 来源；`DataCatalog.unstructured_sources()` 仍提供兼容盘点视图，但 legacy-only 行不得因此进入新刷新计划。当前 target registry 将五个文章 source、DefeatBeta SEC 索引、SEC 官方正文、DefeatBeta transcript 和 repository-curated 知识语料分别登记，并列出 5 个持久化 dataset 与 source 的双向引用；RSS alias 归入 `semianalysis`，不单开 owner。Catalog lint 509 checks 全部通过。登记不等于 SEC 官方正文取得成功，也不等于全量 Layer 消费签收。

### 本机真实运行与是否启用（2026-09-25 增量）

新 `com.ats.data-refresh` 已以 `uv run --offline --no-sync python -m ats.data.refresh tick` 加载，`launchctl print` 核对程序及 `ATS_DOCS_ROOT=var/data/documents`，`refresh_controller_ticks` 于 09:24:51 UTC 记载真实 `launchd` tick/exit 0。策展语料由受管 manual task `8f3489e9-2299-599a-ac3c-f87545e42a7c` 首次写入 10 份版本，10/10 可由 Data Product 读回；随后 launchd job `ai_hardware_knowledge_daily` 于 09:27:21 UTC 取得 `no_change`、queue task `b78057e4-3256-5739-9203-cce8b64111b2`，没有新增版本。此 job 已启用。

| 来源 | 真实受管结果 | 产品读取、SLO 与启用结论 |
|---|---|---|
| `trendforce_news` | manual queue task `64c242e3-27e9-5c5e-82f9-da597b336868`：5/5 索引页成功，16 个正文候选、16 发布、0 隔离 | `admitted_documents` 读回 16/16；周更 job 已启用，下一次自然触发的 launchd 来源运行尚未到期 |
| `semianalysis` | 初次 sandbox 内 IMAP 受本机代理权限阻断、cursor 不推进；放开网络限制后 IMAP+RSS 两载体成功，13 候选、13 发布、0 隔离；受管 task `3e47ea00-b19e-5ac3-8513-6fb486ef171e` | 13/13 全部是明确标注的 `partial` 订阅预览，13/13 可由 Information Data Product 读回；周更 job 已启用，下一次自然 launchd 来源运行待到期。不得宣传为全文 |
| `ibkr_news` / `yfinance_live_news` | TWS `127.0.0.1:7496` 拒绝连接；早期 task `33c866f4-f5a5-52b5-8492-9c0e792f2afb` 曾误扫 PEAD 11 标的。修正后的真实受管 task `a7b3779b-1456-5ec9-80e6-7bd2908614ef` 只查询 policy 8 标的，Yahoo 66 候选、62 隔离、4 人工待审、0 发布；LLM/Agent/Workflow/订单副作用均为 0 | 故障范围与人工审阅门禁已实跑签收；TWS/IBKR 新闻权限、人工批准及自然 launchd 运行未签收，日更 job 关闭。不得把候选当成发布材料 |
| `kr_ecos_exports` | manual queue task `0cc8dcf7-be07-5ed5-9a5b-39dc5bb8c66a`：19 行，1 新增、18 未变、0 隔离 | 区域产品读回 2026-08 观测与 observation ID；月度 job 已启用，下一自然 launchd 来源运行待到期 |
| `tw_mof_exports` | manual queue task `0507496e-fda1-5960-8182-7f2f10262126`：`parse_failed`，0 准入 | 旧解析入口只登记不修；月度 job 关闭 |
| `trendforce_dram` | manual queue task `74c212d2-f1a7-5c30-850a-c5a5deb76cd3`：3 新增、0 隔离 | 最新观测期仍为 2026-07-16，超过 45 天 SLO；月度 job 关闭，不能因运行成功而误报新鲜 |
| `defeatbeta_sec_filing_index` | revision `a46d68650c1f90b7331608350dced8364047b3f7`，`spec.json` SHA-256 `0c9fe8654ea2933a9db596a3149c0445f5c1bd07633302d585bcb3815e3564c0`；全集 30 标的 × 4 行预算已提高至 160 并加入扩容超额守卫。首次全集 task `f0c421d8-24ba-55b5-90b4-85ae088337c8` 遇网络故障；重试 task `488df374-5c60-5856-976b-d3a39ff9d084` 准入 116 索引行，覆盖 29/30 标的；再次 task `04e7e3bf-fd88-5403-92c4-46797e1f9c74` 为 116 `no_change` 且显式 `coverage_missing_entities=['005930.KS']` | `data/US` 来源不覆盖韩股；索引仅元数据，0 SEC 正文。快照约 80.8h 超过 3d SLO、上游条款未签收，日更 job 关闭 |
| `defeatbeta_earnings_transcript` | 同一 revision；全集受管 task `e599161f-bc27-573e-80bf-ecc97389d7ca` 发现 111 行、109 新准入、2 `no_change`、0 隔离，覆盖 29/30 标的，显式缺 `005930.KS` | 连同先前 NVDA 版本，`admitted_documents` 读回 113 份完整文档、29 实体、0 缺版本/空正文；快照约 80.8h 超过 3d SLO，韩股与上游使用条款未签收，日更 job 关闭 |
| `sec_edgar_filing_body` | 对已准入 NVDA accession 的官方 URL 两次尝试，最近 task `5a6424a5-41ac-5b04-8ef6-cab2e358a85c` 仍为 `ConnectError`；代理 TLS EOF，直连 SEC TLS 亦失败 | 0 官方正文发布，任务重试等待；SEC 正文与索引分离的安全门有效。网络通路与真实正文质量未签收，日更 job 关闭 |

历史记录（2026-09-25）：原 PID 641 曾加载旧进程镜像；用户授权后备份原 plist 并重启为 uv 进程 PID 85818，旧周调度器加载受管队列 producer；同周任务 `c5ed535a-221c-5100-afd3-99cc1a639a72` 单次 lease 成功。该记录证明早期运行时迁移，不代表现行周期。现行 FactSet 月末调度、真实 misfire 与发布证据以 2026-10-02 的 [月报运行手册](FACTSET_SEMANTIC_RUNBOOK.md) 为准。

## Layer Analyst 与 Evidence Observer：新旧流程差异清单

### Layer Analyst 持久化输入签收（2026-10-02）

本次按实际 Layer 调用链签收，不把“目标图上的理想数据包”误写成当前代码已经完整实现。Layer 的持久化输入归入 `HIER_DATA`（产业链判断、第三方结构化信号、截面因子）及其可追溯 `DOC_DATA` 证据；实时价格/估值行情单独走 Runtime Data Gateway，不算持久化输入。FactSet Earnings Insight 当前由 Macro/Sector 消费，不是 Layer 输入；SEC 正文/电话会主要是 Fundamental 输入，除非其派生的已准入事实进入 Layer 证据观察表，不将全文采集状态直接计作 Layer 读回。

| Layer 输入组 | 权威 registry 与数据身份 | 旧 owner / 目标 owner | 本次写入、发布、读回证据 | A/B/C 与处置 |
|---|---|---|---|---|
| 产业链判据语料 | `unstructured.yaml`: `ai_hardware_knowledge_corpus` → `ai_hardware_knowledge`；10 个 `structure_notes` 成员 | 旧：Layer 配置文件直接读取；新：受管 repository-corpus job → `curated_knowledge_packet` | 当前产品 API 逐个读回 10/10；每个版本均带 `version_id` 与 SHA-256；无缺项 | **B，签收**。静态 Git 语料无外部刷新；旧文件保留作版本化输入源，不执行物理删除 |
| Layer 命题的第三方结构化信号 | `structured.yaml`: `tw_mof_exports/regional_tw_exports`、`kr_ecos_exports/regional_kr_exports`、`trendforce_dram/industry_dram_contract_price`；旧概念绑定仍在 `sources.yaml` | 旧：`ats.chain.sources` / `sources.yaml` 适配器语义；新持久层：受管 Structured Ingestion + Data Product。旧 YAML cadence 仅作迁移对照 | `RegionalProducts` 读回台湾/韩国 2026-08；台湾 308 条重采为 `no_change`。DRAM 在本轮 launchd 任务中更新到公开免费表 `2H Aug`，观察期间至 2026-08-31 | **B，签收为已发布快照**。台湾/韩国/DRAM 自动 owner 均已在 2026-10-02 逐源 launchd 实跑；保留月度 cadence。旧概念配置没有伪称为新 registry |
| 新闻/研报派生的命题证据 | `unstructured.yaml`: `ibkr_news`、`trendforce_news`、`semianalysis`；发布事实经平台 evidence observations 读取 | 旧：新闻/研报入口和 PEAD/Chain 事实提取散落；新：受管文档刷新、准入发布、平台只读 `UnstructuredReadRouter.observations` | 2026-10-02 `layer_assessments(..., allow_llm=False)` 只做输入回放：L2/L4/L5/L6/L7/L8 分别读到 94/50/80/101/62/71 个证据簇；6 个有命题的层共 26 条 assessment。无 LLM 判读，所有 verdict `unknown`，因此这是输入可读证据，不是分析结论验收 | **B，输入读回签收**。旧 `weekly_review=false` 分支不作为当前周期 owner；历史 `research.ingest_configured`/搜索回退仅登记，不改旧调用者 |
| 公司披露派生的命题证据 | `unstructured.yaml` 固定 SEC filing index/body、DefeatBeta transcript；结构化提取的 accepted evidence 另登记 `accepted_document_evidence/private_company_events` | 旧：PEAD/Observer 动态 source 与通用 transcript/search；新：固定来源采集 → 文档准入/事实观察 → 平台观察读 API | 同一 Layer 输入回放的 evidence clusters/文档引用可经平台观察 API 读取；SEC 原文空值例外不阻断。此结论不替代 SEC/transcript 独立来源覆盖验收 | **B，按产品输入签收**。旧动态 ID/search 路径只记录为遗留，不执行修复；源覆盖、正文例外见 SEC 专项验收 |
| 截面财务与一致预期 | `structured.yaml`: `company_financials`、`market_consensus`，consumer `sector_constituent_financials` / `sector_consensus`；市场价格和估值走 Runtime Gateway | 旧：财务/共识 Provider 兼容读取；新：财务 Data Product 只返回已发布会计数据，无账本值时显式 `no_coverage`；共识读取已发布快照并通过允许的 cache-miss 队列补缺；行情不持久化 | 按用户确认的美股范围排除 `005930.KS`，29 个实体中一致预期快照 29/29 有行；财务 accounting package 7/29 有已发布值、22/29 明确返回 `no_coverage`；不以实时 Provider 返回值冒充持久化财务 | **B，输入契约签收**。22 个 `no_coverage` 是已知且预期的覆盖状态，按用户确认不构成 2.2.11 验收缺口；读取端仍如实看见该状态，不伪造财务值 |

实际 Layer 输入回放中，6 个有命题层均通过目标平台观察 API 读到了非空证据；这 6 层共构造 26 条 assessment。`allow_llm=False` 只验证输入读取与证据聚类，所有 verdict 均为 `unknown`，没有执行 Agent 判断。L1 的三条专属 Evidence Observer 与 Chain claims 是不同运行路径；L3 当前没有启用的命题 claims，因此两层都不在这次 26 条 assessment 回放中。KB 产品 10/10 版本/hash 完整。Layer 财务输入中 22 个实体的 `no_coverage` 按用户确认是预期状态，不阻断本次输入签收。

### Layer 证据链逐层明细（2026-10-02 只读回放）

这里的“拿到证据链”指：配置的命题能在持久化平台观察接口中读到归属该命题的 observation，并形成 evidence cluster；它不表示证据充分、LLM 已判断或命题已被证实。cluster 数为观察记录分组数，不是独立来源数，也不能跨 Layer 相加为去重文档数。

| Layer | 当前证据入口 | 本次读回 | 缺失/限制及原因 |
|---|---|---:|---|
| L1 应用 / Token 经济 | 3 条专属 Evidence Observer：生产化与应用扩散、AI 商业化、原始能力前沿；与 Chain claims 分开 | 本次 Chain 回放不包含 L1。生产化 Observer 有独立端到端验收记录；Ramp 补充信号也有独立 Data Product 验收记录 | 本次未重跑三条 Observer，因此不把历史隔离验收等同于 2026-10-02 生产库逐条读回。L1 没有 Chain claims 是设计选择，不是这次读回失败 |
| L2 云与算力 | Chain claims | 5 条命题，94 clusters | 证据可读；`cloud_demand_still_accelerating` 有读数 8/10，静默 CRWV、MU；`cloud_moat_hard_assets` 5/6，静默 CRWV；`datacenter_buildout_constraint` 2/7，静默 AMZN、CRWV、GOOG、META、MSFT。另两条命题无静默证人。静默表示该命题下没有可归属观察，不表示这些公司的所有材料都缺失 |
| L3 数据中心电力与冷却 | 当前无 Chain claims，也没有启用的 Evidence Observer | 0 条命题、0 条 assessment | 这是配置/命题范围缺口，不是目标读取 API 报错。此前拆层观察曾从 VRT、ETN、GEV、BE 读到 418 条通用财务读数，但 418/418 未映射到命题；它们主要是 FCF、收入、净利、EPS 等，不能回答电力接入、交期、项目取消等本层独有问题。当前候选问题包括并网排队/交期、机电长单能见度、项目延期/取消、液冷渗透、现场发电由应急转常规；尚未审定为正式 claims |
| L4 互联与网络 | Chain claims | 4 条命题，50 clusters | 证据可读；`optical_component_supply_gap` 有读数 2/5，静默 MRVL、NVDA、TRENDFORCE；`rate_transition_pricing_power` 4/6，静默 NVDA、TRENDFORCE；`copper_ip_crosses_to_optical` 4/5，静默 NVDA；`interconnect_moat_distribution` 4/4，无静默证人 |
| L5 芯片设计 | Chain claims | 5 条命题，80 clusters | 证据可读；`xpu_order_momentum_outpacing_revenue` 有读数 8/12，静默 AMZN、GOOG、META、MSFT；`xpu_value_capture_per_capacity` 4/8，静默 AMZN、GOOG、META、MRVL；`xpu_generation_cadence_delivering` 5/9，静默 AMZN、GOOG、ORCL、TSM；`xpu_position_distribution` 4/4，无静默证人；`xpu_revenue_funded_by_customers` 3/12，静默 AMD、AMZN、CRWV、GOOG、META、MRVL、MSFT、ORCL、SPCX |
| L6 存储器 | Chain claims | 3 条命题，101 clusters | 证据可读；`hbm_supply_tight` 9/9、`hbm_share_and_pricing_power` 3/3 均无静默证人；`hbm_pricing_still_expanding` 5/6，静默 AMD |
| L7 晶圆制造与先进封装 | Chain claims | 4 条命题，62 clusters | 证据可读；`advanced_node_supply_gap` 有读数 5/8，静默 AVGO、MRVL、NVDA；`advanced_packaging_supply_gap` 5/8，静默 005930.KS、AMD、NVDA；两条 competitive-position 命题各 3/3，无静默证人 |
| L8 半导体设备 | Chain claims | 5 条命题，71 clusters | 证据可读；`wfe_cycle_still_expanding` 有读数 6/9，静默 005930.KS、MU、TSM；`process_content_still_rising` 5/7，静默 005930.KS、INTC；另外三条命题分别为 4/4、5/5、4/4，无静默证人 |

上述静默证人的共同技术含义是：截至本次回放，当前 claim → concept → entity/source 映射没有把可用观察归到该命题，或该公司/来源确实没有对应观察。仅凭 `silent_witnesses` 不能区分“上游没有披露/未采集”与“有文档但 claim 抽取/映射未命中”；需要逐实体查原始文档版本、提取记录及 quarantine 才能把两者分开。因此本表不把静默项擅自归因为来源缺失，也不将无静默证人等同于证据充分。所有 26 条 verdict 为 `unknown`，是本次明确使用 `--no-llm` 的结果，不是缺失数据的同义词。

### Layer 截面财务账本逐标的覆盖

“覆盖”专指新路径 `company_financials` 中存在可由 `sector_constituent_financials` 选中的完整、同期间会计报表包；一致预期、行情和旧 Provider 即时返回不算财务账本覆盖。集合取 AI Hardware Layer tickers/cohort 的 30 个唯一实体。按用户确认的美股范围排除韩国代码 `005930.KS` 后为 29 个美股实体。

| 状态 | 标的 | 新路径选中来源 | 最新完整报告期 | 读到的 Layer 派生指标 |
|---|---|---|---|---|
| 有覆盖 | AMD | `defeatbeta_stock_statement` | 2026-06-30 | gross margin、operating margin；该包未形成 revenue growth |
| 有覆盖 | AMZN | SEC + issuer disclosure 官方组合包 | 2026-06-30 | gross margin、operating margin、revenue growth |
| 有覆盖 | KLAC | `defeatbeta_stock_statement` | 2026-06-30 | gross margin、operating margin、revenue growth |
| 有覆盖 | MRVL | `yfinance_financials` | 2026-04-30 | gross margin、operating margin、revenue growth |
| 有覆盖 | MSFT | `defeatbeta_stock_statement` | 2026-06-30 | gross margin、operating margin、revenue growth |
| 有覆盖 | NVDA | `defeatbeta_stock_statement` | 2026-07-31 | gross margin、operating margin、revenue growth |
| 有覆盖 | TSM | `defeatbeta_stock_statement` | 2026-06-30 | gross margin、operating margin、revenue growth |
| 无覆盖 | 005930.KS | — | — | 韩国代码；不在用户确认的美股覆盖目标内 |
| 无覆盖 | AAOI、AMAT、ASML、AVGO、AXTI、BE、COHR、CRDO、CRWV、ETN、GEV、GOOG、LITE、LRCX、META、MU、SKHY、SNDK、SPCX、STX、VRT、WDC | — | — | 目标 `company_financials` accepted observations 查询为空，产品返回 `no_coverage` |

因此按美股目标口径，财务 accounting package 为 7/29 有值、22/29 显式 `no_coverage`。这 22 个是当前产品的真实返回状态；用户已确认其为预期范围，不要求本项继续追逐全覆盖，也不是逐标的解析失败结论。数据产品不以实时 Provider 值冒充持久化财务。若未来产品目标要求扩大财务覆盖，再单独立项补采并保持账本完整包要求（同一报告期含核心损益、现金流、资产负债、EPS、债务和一致币种）；本次 2.2.11 不以此为未完成项。

A/B/C 汇总：本次没有仅凭 YAML/schedule 认定为 A 的旧 owner；目标 Layer 输入均能映射到权威 registry，故无 C 类 source identity 缺项。逐项归入 B，并分别记录旧入口是否曾有真实运行、目标写入/发布、产品读回与 coverage。财务覆盖统计按用户已确认的美国市场范围排除 `005930.KS`：29 个美股实体中 7 个有已发布会计值、22 个显式 `no_coverage`，清单见上表。`no_coverage` 是预期状态，不代表缺 registry 身份，也不阻止 target input signoff；这项签收本身仍不等同于 Phase F 全面切流资格。

旧入口处置：新的 Layer 知识读取落在 `curated_knowledge_packet`；命题与事实读取由平台只读 router 返回，不将 Workflow Memory 当事实库；结构化月度产品按规范 source/dataset 读回。兼容模块、旧 `sources.yaml` 概念映射和 Observer/search 代码仍保留，按用户要求只记录、不修复、不物理删除。该处置证明 target Layer 取数不需要调用这些旧写入路径，但不等于整个 Evidence Observer 角色已退役；其角色 tombstone/映射由 Task 3.9 跟进。没有用 `schedule.yaml` 或 `platform` rollout 状态替代以上真实产品读回证据。

本节以 `config/data/structured.yaml` 和 `config/data/unstructured.yaml` 为目标注册表核对 Agent 实际消费的数据；`config/data/sources.yaml`、`news_sources.yaml`、PEAD 动态 source ID 和 scheduler 仅作为旧流程/迁移对照，不视为目标注册表的权威补充。注册、配置了 schedule、实际被 launchd 启用、成功持久化、被 Agent 读回是五种不同状态，不能互相推断。

### A. 已在目标注册表登记，且旧流程存在实际采集入口

| Agent / 数据 | 目标注册情况 | 旧流程与当前状态 | 对照/迁移动作 |
|---|---|---|---|
| FactSet Earnings Insight PDF / 行业材料（不属于 Layer 输入） | `unstructured.yaml` 有 `factset_earnings_insight_doc`；相关结构化指标另在 `structured.yaml` 登记，二者不能互相替代 | `com.ats.schedule` 是唯一周期 owner，生产注册 `factset_monthly_ingest`，按纽约时间月末进入受管队列；PDF/指标共用一次来源抓取、独立准入发布 | 2026-09 月报于 10-02 补跑；同一官方 PDF 最终发布，Index 43 项/期间、IT 12/12 组通过。其它十行业 P2 与 Sector `registered_no_data` 独立保留；无需等待自然周六，也不另开 PDF job |

### B. 已在目标注册表登记，但旧定时流程未启用、无现行旧 owner，或仅有代码入口

| Agent / 数据 | 目标注册情况 | 旧流程/新调度现状 | 对照/迁移动作 |
|---|---|---|---|
| Layer/Information：TrendForce 新闻文章 | `unstructured.yaml` 有 `trendforce_news` | data-only `article-ingest` 真实运行 16/16 发布，目标周更 launchd job 已启用；旧 `research.ingest_configured` 位于关闭的 weekly-review 分支，非当前周期 owner | 10-02 Layer 命题观察读回已覆盖 TrendForce 来源证据簇；后续周更任务仍按 2.2.9 监测 |
| Layer 命题：韩国/台湾出口、DRAM 合约价 | `structured.yaml` 有 `regional_kr_exports`、`regional_tw_exports`、`industry_dram_contract_price` | 新结构化产品统一使用规范 source/dataset；旧 alias 与 `sources.yaml` 只保留概念绑定/迁移对照 | 台湾、韩国 2026-08 产品读回；DRAM 2H July 的数据期间截至 7/31。自动周期启用与真实 launchd run 状态见 2.2.9，不将 schedule 声明算作运行证据 |
| Layer/Information：IBKR News 与 Yahoo fallback | `unstructured.yaml` 登记 `ibkr_news` 和 `yfinance_live_news`，后者明确为失败切片 fallback | 新日更 job 与 TWS 权限/候选准入按 2.2.9 独立验收；target Layer 只读已发布观察，不自行触发新闻 Provider | 10-02 Layer 命题观察读回已包含 DOWJONES 来源观察；IBKR 刷新稳定性仍未签收，不因读到历史观察而宣称当前刷新通过 |
| Layer/Information：SemiAnalysis 研究 | `unstructured.yaml` 有 `semianalysis` | 旧 `research.ingest_configured` 位于关闭的 weekly-review 分支；新入口按稳定 source ID 管理 | 已发布 preview 和 Layer 平台观察可读；10-02 Layer 命题观察读回含 SEMIANALYSIS 来源。目标预览/完整度状态和周期验收仍见 2.2.9 |
| Evidence Observer：Anthropic、Ramp、OpenRouter、前沿能力基准 | 对应数据集和 provider source 均在 `structured.yaml`；`schedules.yaml` 为其中多项声明了刷新定义 | `com.ats.data-refresh` 当前仅确认 Ramp 探针启用；其它 job 是否启用必须逐项查 controller ledger / launchd 实际状态，不能仅看 YAML schedule | 为每个数据集比对目标 job、旧采集入口、实际 run、source vintage、accepted data 和 Observer 使用的 as-of 快照；按源保留/替换旧入口 |
| Evidence Observer：美国企业/员工采用率 | `ai_enterprise_adoption_us` / `us_census_btos`、`ai_worker_adoption_us` / `rps_genai_adoption` 已登记 | 结构化数据由受管数据层读取；没有证据表明 `com.ats.schedule` 是其持久化 owner | 对照新 `structured` ingest 的实际 run ledger、accepted observation 与 Observer 快照；不可用 Agent 成功运行代替采集验收 |
| Evidence Observer：Sacra / TickerTrends 收入估算 | 两个结构化 source 均登记并写入 `frontier_ai_labs_revenue` | 受管周更 job `frontier_ai_labs_revenue_p7d_refresh` 已启用；2026-10-02 launchd 真实检查为 `no_change`，两来源各有 source check 与 ingestion 记录 | 当前周期验收通过；TickerTrends 仍按数值研究数据处理，如需持久化其原始文章正文，需另行登记非结构化 source |

结构化 Layer 输入的当前 Data Product 读回：`RegionalProducts` 有台湾 2026-08 与韩国 2026-08 数据；台湾受管采集和原始 CSV 对账已于 2026-09-26 成功。DRAM 原始 session 标为 `2H Jul`，观测期间终止日为 2026-07-31，符合公开免费表的最新数据期；UI 的“Last Update”日期不应混作付费用户可见数据截止期。FactSet 月报 Index/IT 核心读回属于 Macro/Sector，不计入 Layer 输入签收。Layer 命题证据读回和截面数据覆盖状态见上方 2026-10-02 签收表。

### C. 基线时旧消费者/采集代码存在、目标注册表缺项；现行状态见末栏

| Agent / 数据 | 基线缺口 | 现行 A/B/C 状态与剩余动作 |
|---|---|---|
| Layer：SEC earnings release、10-Q/10-K 等原文 | `structured.yaml` 的 `sec_companyfacts` 是结构化 XBRL/事实源，不代表 SEC 文档正文已登记；旧流程按 symbol 动态造 source ID | **B（索引及可选正文已登记）**：固定 `defeatbeta_sec_filing_index` 和 `sec_edgar_filing_body` 双来源。US 索引 116 行覆盖目标美股 29/29；已发布 NVDA/AMD 六份官方正文，个别 SEC 原文缺口按用户政策保留为非阻塞隔离。旧动态 ID 只作迁移对照，不进入目标 job |
| Layer：业绩电话会 transcript | 旧事件流程用 `defeatbeta_transcript:{symbol}` 动态 ID，通用 loader 可搜索回退 | **B（已登记、US 目标范围覆盖通过）**：固定 `defeatbeta_earnings_transcript` 的 115 个完整文档经产品读回、覆盖 29/29 美股，财季与 speaker/segment 准入通过；韩国不属于该 DefeatBeta 固定源范围。自动持久化没有搜索 fallback；旧动态和搜索路径只登记，不修 |
| 旧 PEAD `Evidence Observer` transcript/搜索路径 | 旧 `pead_score_window` 的 observe 分支会调用通用 `transcript.fetch`，在来源不完整时再走搜索候选，并把结果写入观察事实；其 source identity、采集 run 和逐版本读回没有统一登记在 `unstructured.yaml`。本项是 legacy gap，本 change 仅登记、不改旧分支 | 依既定方向由固定 DefeatBeta transcript 来源及正式准入文档替代；在来源注册、财季匹配、raw/version 血缘和 Layer/Information 消费验收前，不把旧 Observer 成功运行计作目标路径证据。 |
| Layer：AI 硬件产业链本地知识文档 | 10 份 `config/knowledge/*.md` 原先没有集合级 source identity | **B→已签收**：`ai_hardware_knowledge_corpus` 登记为 repository-managed 语料；受管队列写入 10/10，不触发外部抓取；Layer `curated_knowledge_packet` 与 `build_context` 实际读回，版本与 hash 可追溯；launchd 日检 `no_change`。旧 Git 文件直读保留迁移对照 |
| `news_sources.yaml` 中 `SECEdgar8K-Coherent` | 名称像 SEC feed，URL 实际为 `https://www.coherent.com/rss` 占位值；不能作为 SEC 来源登记或采集证据 | 禁止将它映射成 SEC。确认它是否为有效 Coherent 公司 RSS；若保留，改成准确名称并单独验收，否则从旧对照表标记为废弃占位项 |

#### Layer 本地知识语料逐文件对照（C 类）

以下文件均由 `config/sectors/ai_hardware.yaml` 的 `structure_notes` 引用。基线时它们为 C；本次已登记 `ai_hardware_knowledge_corpus`，十份成员均经受管写入、版本/内容 hash 与 Layer 新 Data Product 读回，当前为 **B 类已签收的静态语料**。表内原始逐篇缺口保留为迁移取证历史，不再表示当前未登记。

| Layer 范围 | Git 持久化输入 | 旧入口/当前证据 | 目标入口与剩余验收 |
|---|---|---|---|
| L1_app | `config/knowledge/Token经济.md` | `ai_hardware.yaml` 的 L1 `structure_notes`；旧 Layer/sector context 可读 | 已登记单一 corpus member，受管发布并经 Layer 产品读回；旧直读不作新路径证据 |
| L2_cloud | `config/knowledge/云服务.md` | L2 `structure_notes` | 同上；版本变化由不可变文档版本保留 |
| L3_power_cooling | `config/knowledge/电力冷却.md` | L3 `structure_notes` | 同上；缺文件时产品显式返回 missing |
| L4_optical | `config/knowledge/光互联.md` | L4 `structure_notes`，亦被“衬底”说明引用 | 同上；共用一个规范 member，不按层重复 source ID |
| L4_optical | `config/knowledge/铜连接.md` | L4 `structure_notes` | 同上 |
| L5_chip_design | `config/knowledge/芯片设计.md` | L5 `structure_notes` | 同上 |
| L6_memory | `config/knowledge/HBM存储.md` | L6 `structure_notes` | 同上 |
| L7_foundry_pkg | `config/knowledge/先进封装代工.md` | L7 `structure_notes` | 同上 |
| L8_equipment | `config/knowledge/半导体设备.md` | L8 `structure_notes` | 同上 |
| Cross-layer | `config/knowledge/资本开支链.md` | 多个 layer 的 `structure_notes` 共用 | 同上；一个 member 可供多个 Layer 读取，不重复定义来源 |

统一旧 owner 为 Layer 配置/知识文件直读链；target owner 为 `ai_hardware_knowledge_corpus` 的受管队列与 `sector_inputs.curated_knowledge_packet` 只读产品。10/10 写入、发布和读回已验证，历史版本保留于不可变文档版本；旧路径仅作为对照，不执行物理删除。回滚到已知稳定路由仍须在 Task 4.4 单独演练，不能由此声称切流资格。

#### DefeatBeta 固定源的使用边界

该 Hugging Face 数据集卡片声明数据定期更新并在 `spec.json` 记录更新时间，许可标记为 `odc-by`；其中电话会数据包含逐段 speaker/content，SEC 文件部分提供 filing 元数据和官方 `filing_url`。因此它可作为固定、可版本化的发现/电话会来源，但不能把第三方数据集的 SEC filing 行误当成 SEC 原文。刷新时记录 Hugging Face commit/revision、`spec.json`、文件 hash 与报告期；SEC 正文仍通过 accession 对应的 SEC EDGAR 原始文件获取并保留官方 URL。逐源验收其覆盖、延迟、历史修订、条款适用范围和缺失表现后，才能确定为生产 source。来源页：[DefeatBeta Yahoo Finance dataset](https://huggingface.co/datasets/defeatbeta/yahoo-finance-data)。

数据集卡片当前只发布 `data/US/` 市场文件。本 change 的 DefeatBeta 固定源目标范围为 29 个美股，当前索引与电话会已覆盖 29/29；韩股 `005930.KS` 已从目标范围移除，不再作为 2.2.12 的缺口。不要把美国 SEC 文件规则硬套到韩国申报，也不要用搜索结果自动补位；若未来明确扩展到韩国，再另行选择、登记并验收固定来源。

韩国 [OpenDART 原始申报文件 API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003) 与 [三星官方 IR 页面](https://www.samsung.com/global/ir/financial-information/earnings-release/) 是可能的未来来源，不在本 change 的当前验收范围；韩国取数需求已移出本固定来源任务，不登记、不启用，也不影响 2.2.12 完成。

#### 每个差异行的迁移签收字段

每个 source/dataset 都要补齐：`target_registry_id`、旧入口/旧 owner、目标入口/目标 owner、持久化位置、触发/周期、真实运行状态、raw/hash、准入/隔离结果、revision/as-of、Agent 消费路径、旧入口退役条件、回滚方案、逐源验收证据。未找到旧入口要明确填 `none found`；未知状态填 `unverified`，不能推定为已完成。

| 来源 | Catalog 周期与性质 | 当前采集/清洗入口及 owner | 必须逐源解决的事项 | 启用和验收状态 |
|---|---|---|---|---|
| `factset_earnings_insight_doc` | monthly month-end；PDF 研报；每次最多 1 个官方 PDF 请求、60s timeout、3 attempts；licensed internal research；最新月报优先 | `com.ats.schedule` 唯一 owner，`factset_monthly_ingest` 使用 `factset-month-end:YYYY-MM` 幂等键；仅 queue worker 执行语义解析与写入 | 2026-09 月报月末自然触发因休眠延迟 15.6 小时后补跑；首次 partial 后从同一原件受管重跑发布成功。Index 43 项/期间、IT 12/12 组通过；Sector `registered_no_data`、其他十行业 P2 独立延后，不阻塞核心月报发布 | 月末调度、误差补跑和核心产品发布通过；继续观察之后月份的版式与 misfire 行为 |
| `trendforce_news` | weekly；网页文章；5 index pages、最多 16 article bodies/run；公开页但正文/付费墙须判定；SLO 10d | data-only `article-ingest` → 共享不可变文档仓；周一 launchd job 已启用 | 真实 5/5 索引页、16/16 正文发布和 Information Data Product 读回已通过；后续正文/付费墙变动仍 fail-closed | 手动真实来源已签收；自然 launchd 周更运行尚未到期 |
| `semianalysis` | weekly；订阅研究/预览；最多 32 items/run；需 Gmail IMAP 凭据或 RSS；SLO 10d | `article-ingest` 用 target registry IMAP/RSS；旧 `research.ingest_configured` 所在 `weekly_review=false`；新周一 launchd job 已启用 | 真实 13/13 `partial` 预览发布、Information 读回、完整批次游标与预算门已验证；没有全文的事实须持续显示 | 手动真实来源已签收；自然 launchd 周更运行尚未到期 |
| `ibkr_news` | daily；券商新闻；最多 10 body requests/run；需运行中 TWS 与 provider 权限；SLO 2d | data-only 入口复用 adapter；每日 launchd job 配置了 IBKR 主源及条件式 Yahoo fallback，默认关闭 | 8 个标的/7 天/3 天切片、标题与实体关联、部分切片失败、健康零新闻与不可达区别、文档版本；仅不可达/失败切片激活 Yahoo fallback；必须有 TWS/provider 授权运行记录 | 隔离故障切片→Yahoo 路径通过；job 未启用；TWS 权限及真实 launchd 验收待 2.2.9 |
| `yfinance_live_news` | daily fallback policy；每实体最多 10 条、总正文最多 24；需满足 IBKR 故障条件；人工标题/URL 审阅；继承 IBKR SLO | 仅由 `ibkr_news` data-only run 条件调用；无独立 job；人工批准选项不暴露给 schedule | **不得独立日更主扫**；只接受 IBKR 不可达或失败切片传入的标的范围；标题/URL 复核、主体关联、正文质量、去重与人工发布门禁。待审候选保留 quarantine raw，不写已发布版本 | 隔离验证只触发失败标的 NVDA，候选状态为 `pending_human_review` 且 published=0；真实 TWS、人工批准与来源验证未完成 |
| `kr_semiconductor_exports` | monthly；数值序列；structured catalog 1 request/run、page_size 10、30s timeout；SLO 45d | 规范 `kr_ecos_exports` → `regional_kr_exports`；每月 15 日 launchd job 已启用 | 真实受管运行 19 行、1 新增、18 未变；区域产品读回 2026-08 观测及 observation ID | 手动真实来源已签收；自然 launchd 月更运行尚未到期 |
| `tw_ic_exports` | monthly；数值序列；2 requests/run、60s timeout；SLO 45d | 规范 `tw_mof_exports` → `regional_tw_exports`；月度 job 保持关闭 | 本机真实受管运行 `parse_failed`、0 准入；旧解析入口问题只记录不修 | 阻断：解析失败与来源读回未签收 |
| `dram_contract_price` | monthly；数值序列；1 request/run、30s timeout；SLO 45d | 规范 `trendforce_dram` → `industry_dram_contract_price`；月度 job 保持关闭 | 本机真实运行 3 新增，但最新观测 2026-07-16，超过 SLO；需确认上游是否停更/付费墙或源发布日期 | 阻断：来源结果陈旧，不能以本次运行成功冒充 freshness |
| `SECEdgar8K-Coherent` | 无 cadence；RSS 占位 URL；无 request budget / SLO | `news_sources.yaml`，无刷新 owner | 先确认 feed URL 有效及 SEC filing identity/event cadence、重复/修订、原文落盘与实体关联，再决定绑定 | unbound；不冒充“有周期”来源 |
| `SemiAnalysis` | 无独立 cadence；与 IMAP 主源同名；无独立 budget/SLO | 旧 `news_sources.yaml` research feed；已并入权威 `unstructured.yaml` 的 `semianalysis` source | 已按稳定身份归并、IMAP 优先及跨载体去重；不创建第二个刷新 owner | 旧 alias 已归并，不作为独立刷新任务或第二个来源 |

## 逐源完成门槛

历史实施记录（截至 2026-09-25）：SemiAnalysis 的 data-only article-ingest 入口、凭据预检与预算门已配置；IMAP 凭据缺失会标记 `credentials_missing`/partial，不推进游标。当时本机已由真实 IMAP/RSS、准入与 Information 读回后启用周更 job，但自然 launchd 周期尚未到期。其后于 2026-09-28 的 launchd 实际运行以及其它所有 job 的逐源结论，统一见本文件顶部“本机 launchd 逐源验收”；2.2.9 已完成逐源审计。

1. 固定 catalog ID、唯一执行 owner、触发类型/时区/周期、最短间隔、请求预算、凭据及授权检查；fallback 来源单列条件，不独立扫全量。
2. 执行入口只能调用受管 Data 管道；发现/抓取、正文清洗、准入/隔离、事实提取和消费者读取分层，不能以 Chain/Agent 运行顺带采集作为刷新完成证据。
3. 每次运行保存稳定触发键、来源状态、候选数、原始资产和质量结果；`no_change`、空结果、部分结果、不可达、权限不足、付费墙、人工待审各有不同状态。
4. 仅从已准入、可追溯的文档版本发布产品；修订 append-only，保持 known-at/as-of；抓取成功但质量门未过时保留旧发布并显式报告陈旧/缺口。
5. 在隔离存储重放正常、重复、来源失败、正文不足、拒绝、修订、预算耗尽和消费者读取；真实来源另留脱敏运行证据。仅通过该源验收后才启用 launchd job。涉及许可、人工复核或 TWS 连接的来源缺条件时保持禁用。

## 当前调度边界

历史状态截面（截至 2026-10-01）：刷新账本记录了 2026-09-28 TrendForce 新闻与 SemiAnalysis 定时任务成功、10-01 策展知识库 `no_change`、DefeatBeta SEC 索引/电话会成功及 OpenRouter 成功；当时 IBKR 两次尝试后失败，台湾/韩国/DRAM 的真实 launchd 月度验证尚未执行。上述月度来源已于 2026-10-02 完成逐源验收，IBKR 已按规则关闭；以本文件顶部的“本机 launchd 逐源验收”及 FactSet 月报手册为当前记录。`source-acceptance` 只读诊断不能代替持久化证据。

历史记录（2026-09-25）：FactSet 当时的周更回调已改为受管队列 producer，并验证同周幂等。该周期随后由 FactSet 专项替换为月末语义调度；当前 owner、月末运行、误差补跑和产品发布以本清单顶部及 `FACTSET_SEMANTIC_RUNBOOK.md` 为准。事件日历外部来源仍是每来源独立 queue task，手动和 scheduler 入口均先入队，再由 queue worker 调用 adapter。
