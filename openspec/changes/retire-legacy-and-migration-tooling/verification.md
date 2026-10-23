# G0 新路径人工核验记录

> 怎么核验见同目录 [`g0-runbook.md`](g0-runbook.md)，编号一一对应。对应 tasks.md 第 3 组（G0）；任务编号按 ede7412 重排：RP=第 2 组，R2=第 4 组，R1=第 5 组，R3=第 6 组，R4=第 7 组。本文件进 Git，是 R1–R4 退役的前置依据（design.md D1，规格「退役执行必须以新路径人工核验通过为前置」）。

## 填写规则

- **结论**只填四种之一：
  - **通过**：按手册的通过标准看过，没问题。
  - **未通过**：不满足通过标准，需要修。依赖 G0 的退役条目全部暂停，直到修好、复核改为"通过"。
  - **不适用**：这一项在当前配置下不存在或不会发生。在备注写理由。
  - **接受缺口**：不满足通过标准，但你决定带着这个缺口进入退役。在备注写缺口是什么、为什么可以接受；"G0 结论"一节会汇总。
- **证据**：写你看到的东西，例如命令输出摘要、Obsidian 文件名、截图位置、提交号。证据栏里已有的 `〔自动证据，非结论〕` 是准备会话在 **2026-10-21 10:30–10:55（台北）** 只读运行命令得到的，**不代表结论**。你核验时可以保留、补充或覆盖。
- **核验人 / 日期**：每行都填。同一项看了多次（例如等真实触发后再看），日期写最后一次。
- **备注**：需要等真实触发的项写"待 MM-DD 触发"；登记了修复任务的写任务编号或会话链接。
- 改结论时不要删旧内容，在备注里补一句"MM-DD 改为 …，原因 …"。
- 时间都是台北时间。

## 1. Dataflow：非结构化

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| U01 | `ibkr_news_daily`（ibkr_news + yfinance_live_news） | 通过 | 〔自动证据，非结论〕窗口 10-20 `failed / controller_RuntimeError`（10-21 06:01），近 7 天失败 7 次；来源状态 partial；文档库 ibkr_news 126 篇、失败 28，最近抓取 10-20 05:06 | 用户确认（AI 建议） | 2026-10-21 | 10-21 21:14 现场运行（修复后）succeeded/partial_budget_deferred：发现 429、准入 6、隔离 4、延后 419；TWS 自检正常。原“未通过”因 partial 误判，已修。遗留：每次只收约 10 篇正文，累计延后 936 条，是否调大待你看 token 花费后定 |
| U02 | `trendforce_news_weekly` | 通过 | 〔自动证据，非结论〕窗口 10-19：首次 `article_source_partial`（准入 16、budget_deferred 3），重试 `controller_RuntimeError` → failed；文档库 trendforce_news 68 篇，最近 10-19 22:05 | 用户确认（AI 建议） | 2026-10-21 | 10-21 21:10 现场运行：succeeded/partial_budget_deferred，发现 18、准入 16、延后 2。原“未通过”因 partial 误判，已修 |
| U03 | `semianalysis_newsletter_weekly` | 通过 | 〔自动证据，非结论〕窗口 10-19 `failed / controller_RuntimeError`，来源状态 partial、发现 0；文档库 semianalysis 23 篇，最近抓取 10-13 08:35 | 用户确认（AI 建议） | 2026-10-21 | 10-21 21:09 现场运行：succeeded，发现 9、准入 2，文档 23→25。此前两周发现 0 篇，推测是网络问题，现已恢复 |
| U04 | `defeatbeta_transcript_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `no_change`（20:27），发现 111、准入 0；文档库 118 篇，最近抓取 10-16 17:41 | 用户确认（AI 建议） | 2026-10-21 | no_change，111 篇已在库 |
| U05 | `defeatbeta_sec_index_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded / index_metadata_only`（16:38），发现 116、准入 116 | 用户确认（AI 建议） | 2026-10-21 | index_metadata_only 为设计行为；原备注：只收申报目录，不收正文 |
| U06 | `sec_official_body_daily`（sec_edgar_filing_body） | 未通过 | 〔自动证据，非结论〕任务未启用，控制器"从未运行"；sec_edgar_filing_body 最近一次 10-17 22:18 `failed`（source_unavailable ×7）；ASML、TSM 官方披露 `unreachable`（ConnectError） | 用户确认（AI 建议） | 2026-10-21 | 10-21 网络已恢复（sec.gov 200）；17:54 手动跑一次：发现 20、准入 4、隔离 4、12 条因每次运行请求预算（20 次）用完而未取；仍未启用定时任务，待你决定是否在 schedules.yaml 开启 |
| U07 | `macro_theme_news_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（20:28），近 7 天失败 1；文档库 90 篇，最近抓取 10-20 20:27 | 用户确认（AI 建议） | 2026-10-21 | 最近一次 succeeded；7 天内 1 次失败已恢复 |
| U08 | `ai_hardware_knowledge_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `no_change`（20:27）；文档库 10 篇，最近抓取 09-25（知识文件未改即不变） | 用户确认（AI 建议） | 2026-10-21 | no_change，知识文件未改 |

非结构化通用观察〔自动证据，非结论〕：生产文档库里有测试夹具文档（来源 `file:…/tmp/pytest-*/…/t.txt`，状态 accepted），即测试写进了生产库。信息文档包检查 P01 仍为 ok。是否登记为缺陷由你决定。

## 2. Dataflow：结构化

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| S01 | `consensus_goog_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:30）；yfinance_consensus 最近 3 次入库各准入 42 行 | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）；GOOG 估计季度 2026-09-30，EPS 均值 2.964 |
| S02 | `consensus_nvda_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:31） | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）；NVDA 估计季度 2026-10-31，EPS 均值 2.473 |
| S03 | `consensus_skhy_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:31） | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）；SKHY 估计季度 2026-09-30（币种为韩元量级，注意单位） |
| S04 | `consensus_tsm_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:32） | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）；但 TSM 10-15 已发 Q3，库里只有 0q（2026-09-30 已发布季度）预期，下一季度预期未采集（采集器只取 yfinance 的 0q） |
| S05 | `consensus_asml_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:32） | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）；同上：ASML 10-14 已发 Q3，下一季度预期未采集 |
| S06 | `consensus_cohr_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:33） | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）；COHR EPS 均值 1.965 |
| S07 | `consensus_lrcx_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:33）；`g0_company LRCX`：2026-09-30 季 EPS 均值 2.18（2.09–2.28）、营收均值 81.4 亿美元，目标价均值 375.9 | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）；LRCX 财报今天盘后，EPS 均值 2.18、营收均值 85.4 亿美元，请你对照 Yahoo Finance 抽查一次 |
| S08 | `consensus_lite_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:34） | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）（第二次重采成功 09:56）；LITE EPS 均值 4.217 |
| S09 | `consensus_avgo_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:34） | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）；AVGO 估计季度 2026-10-31，EPS 均值 3.828 |
| S10 | `consensus_mrvl_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:35） | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）（第二次重采成功 09:57）；MRVL 估计季度 2026-10-31，EPS 均值 1.101 |
| S11 | `consensus_msft_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:36） | 用户确认（AI 建议） | 2026-10-22 | 10-22 09:21–09:58 手动重采 succeeded（g0_rerun，此前 10-21 的失败是 TLS 被重置，已修分类并重采）；MSFT EPS 均值 4.716 |
| S12 | `company_financials_weekly.GOOG` | 通过 | 〔自动证据，非结论〕窗口 10-17 `no_change`（14:48），defeatbeta_stock_statement 发现 247–261 行未变 | 用户确认（AI 建议） | 2026-10-22 | 10-22 11:41 手动重采 no_change（上游无新数据，库内已是最新）；GOOG 最新期 2026-06-30；范围已扩到 PEAD 11 只+MU |
| S13 | `company_financials_weekly.MU` | 通过 | 〔自动证据，非结论〕窗口 10-17 `no_change`（14:48） | 用户确认（AI 建议） | 2026-10-22 | 10-22 重采 succeeded，且新增 SEC 官方日任务：MU 最新期 2026-09-03（SEC 10-K，13 项指标），数据商仍停在 2026-05-31；读取时由 SEC 供数 |
| S14 | `company_financials_weekly.LRCX` | 通过 | 〔自动证据，非结论〕控制器"从未运行"（10-19 才加入）；10-19 09:49 有一次非控制器入库，准入 272 行；`g0_company LRCX` 最新报告期 2026-06-30，季度 EPS 1.81、营收见输出 | 用户确认（AI 建议） | 2026-10-22 | 10-22 11:44 重采 succeeded，SEC 日任务也成功；LRCX 最新期 2026-06-30（LRCX 今晚发 FQ1 财报）；控制器路径已走通 |
| S15 | `macro_series_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（20:27），发现 850、准入 11、未变 839；宏观检查 ok | 用户确认（AI 建议） | 2026-10-22 | 窗口 10-21 succeeded（21:05）；10-22 窗口 18:30（台北）才到点；库内最新 2026-10-21；请你抽查 ats macro show 中 CPI、非农等读数对照官方发布 |
| S16 | `kr_semiconductor_exports_monthly` | 通过 | 〔自动证据，非结论〕窗口 10-15 `no_change`（08:14），上游 18 行无新增；宏观检查提醒：最新期 2026-08 已过 51 天 | 用户确认（AI 建议） | 2026-10-22 | 根因：ECOS 在 15 日之后才发布当月半导体出口指数（9 月数据 10-22 已有 733.03，15 日那次还没有），而任务只在每月 15 日跑，序列因此总慢一个月。10-22 13:27 手动重采已入库 2026-09；并新增每月 25 日第二次任务防止再慢一拍。注意：这是韩国央行的“半导体出口金额指数（2020=100）”，不是 MacroMicro 截图里的出口总额 |
| S17 | `tw_ic_exports_monthly` | 通过 | 〔自动证据，非结论〕窗口 10-15 `failed / command_or_result_failed`，入库记录 `parse_failed`；宏观检查提醒：最新期 2026-08 已过 51 天 | 用户确认（AI 建议） | 2026-10-22 | 10-21 22:23 手动重采 succeeded（准入 2 条），库内最新期已到 2026-09（318 行）；10-15 的失败是 TLS 被重置，原误标 parse_failed，已修 |
| S18 | `dram_contract_price_monthly` | 通过 | 〔自动证据，非结论〕窗口 10-15 `succeeded`（09:20），准入 3 | 用户确认（AI 建议） | 2026-10-22 | 采集本身没错：我们取的是 TrendForce 的“合约价”表，页面该表最新一期就是 2H Aug（该表 Last Update 2026-08-31），库内 2026-08-16 与上游一致。你截图里更新到 10-22 的是另一张“现货价”表（Session High/Low、每日更新），我们没有采集。缺口：现货价没有进库，若分析需要更及时的 DRAM 价格，需要新增现货价数据集（待你决定） |
| S19 | `ramp_ai_index_p10d_probe` | 通过 | 〔自动证据，非结论〕窗口 10-14 `no_change / upstream_no_change`；上游最新期 2026-08-01，已入库 | 用户确认（AI 建议） | 2026-10-22 | 10-14 no_change，上游最新期 2026-08-01 已入库，上游没有更新；下一次 10-24 |
| S20 | `openrouter_rankings_p7d_ingest` | 通过 | 〔自动证据，非结论〕窗口 10-15 `succeeded`（10-16 10:26 补跑），准入 714 | 用户确认（AI 建议） | 2026-10-22 | 窗口 10-15 succeeded（10-16 10:26 补跑），库内最新 2026-10-15，准入 714；下一次 10-22 16:00 |
| S21 | `frontier_ai_capability_model_discovery_p7d` | 通过 | 〔自动证据，非结论〕窗口 10-16 `succeeded`（15:36）；上游最新期 2026-10-16，已入库 | 用户确认（AI 建议） | 2026-10-22 | 10-16 succeeded，上游最新期 2026-10-16 已入库；下一次 10-23 |
| S22 | `frontier_ai_capability_benchmark_probe_p7d` | 通过 | 〔自动证据，非结论〕窗口 10-16 `succeeded`（15:37），准入 163 | 用户确认（AI 建议） | 2026-10-22 | 10-16 succeeded，准入 163；下一次 10-23 |
| S23 | `frontier_ai_capability_official_release_probe_p7d` | 通过 | 〔自动证据，非结论〕窗口 10-16 `succeeded`（15:38），frontier_ai_public_model_cards 准入 560 | 用户确认（AI 建议） | 2026-10-22 | 10-16 succeeded，frontier_ai_public_model_cards 最新 2026-10-16；下一次 10-23 |
| S24 | `frontier_ai_capability_full_audit_p7d` | 通过 | 〔自动证据，非结论〕窗口 10-16 `succeeded`（15:39） | 用户确认（AI 建议） | 2026-10-22 | 10-16 succeeded；下一次 10-23 |
| S25 | `frontier_ai_labs_revenue_p7d_refresh` | 通过 | 〔自动证据，非结论〕窗口 10-16 `failed`：首次 `command_or_result_failed`，重试 `controller_RuntimeError`；Sacra 检查 `methodology_drift`（10-16 16:40）；10-09 那次 partial（validation_failed 1） | 用户确认（AI 建议） | 2026-10-22 | 10-22 已修好并重采入库（发现 40 / 准入 35 / 隔离 0，立即再跑 no_change，无新版本）。修了四处：①Sacra 方法漂移误判；②季度数被误当全年；③“有数据入库、个别行被隔离”不再记失败；④TickerTrends 从“固定一篇文章”改为读公开归档里 2026-07-01 起涉及 ARR/收入的研究笔记系列（9-29、10-08、10-14、8-14 等都已进来）。按你的口径，他人口径与产品级、季度数都保留，只是各自带标签分序列：Bloomberg/FT 等口径记 media_reported 并写明出处；Claude Code、Codex、ChatGPT Ads 记 product_scope，不并进公司级、不相加；季度收入记 quarter 期间口径（如 Anthropic Q3 16.8B 来自 FT）；每周追踪值记具体日期（instant）。公司级主序列与趋势仍用月度口径，其余在 supplementary_rows 里并列。明确拒收并留痕的：增量（“增加 5.2B”“差额 6.3B”）、没写日期的“同期”数字、成本/融资类。遗留（不影响本项通过）：①只收标题/摘要含 ARR/revenue/run rate 的笔记，正文里偶然提到收入的其他文章不抓；②“约/超过”类数字保留并标 approximate/lower_bound，不当精确值；③Sacra 页面里的产品级数字仍按原规则排除（未改）；④评审报告/图表尚未展示 supplementary_rows |

结构化 S01–S25 小结（2026-10-22，AI 建议，待用户确认）：25 项均记“通过”，其中 S01–S25 里有 9 项是 G0 过程中发现缺陷、修复并手动重采后才通过的（TLS 重置被误标 parse_failed、重试 trigger 冲突、部分行被隔离即记失败、Sacra 方法漂移与季度数误当全年、SEC 预算缺失、韩国出口只在 15 日跑一次、TickerTrends 固定单篇文章）。
不影响通过、但需要你决定或后续处理的缺口：
- S18：现货价（TrendForce 每日表）没有数据集，合约价采集本身正确；
- S01–S15：仍有 19 个标的的一致预期过期、未来季度一致预期未采（你说“以后再说”）；
- S25：只抓标题/摘要含 ARR/revenue 的笔记；Sacra 页面里的产品级数字仍排除；报告与图表尚未展示 supplementary_rows。

## 3. Dataflow：派生

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| D01 | `chain_evidence_daily.TSM` | 通过 | 〔自动证据，非结论〕窗口 10-20 `no_change / already_extracted`（10-21 06:01）；`ats evidence show --entity TSM` 有 2026-10-15 财报后的读数（A14 节点、先进封装光罩尺寸、agentic AI CPU 需求等） | 用户确认（AI 建议） | 2026-10-23 | 10-23 抽查 10 条（见 d01-d04-sample.md）：✅3 ⚠️6 ❌1。财报会引文基本准确；问题是立场标注（TrendForce 转述的 TSM 自己的话被标成 competitor、机构估计被标成 incumbent）、一条动作被标成 up、一条引文是网页编辑导语而非管理层原话。需关注：该文档有 3 个版本、来源不同，证据行没记出自哪版 |
| D02 | `chain_evidence_daily.ASML` | 通过 | 〔自动证据，非结论〕窗口 10-20 `no_change / already_extracted`（10-21 06:01） | 用户确认（AI 建议） | 2026-10-23 | 10-23 抽查 7 条（ASML 名下只能回查到 7 条，其余 240 条来自旧编号文档）：✅5 ⚠️1 ❌1。数值/期间基本准确；一条 unit 字段塞进限定语，一条引文把 12-inch 写成 12 in（不逐字） |
| D03 | `chain_evidence_daily.MU` | 通过 | 〔自动证据，非结论〕窗口 10-20 `no_change / no_print_in_window`（10-21 06:01） | 用户确认（AI 建议） | 2026-10-23 | 10-23 抽查 10 条：✅9 ❌1。财报会数值全部准确；一条股价“收盘上涨 4.2%”方向被标成 flat。注意：MU 只有 57/178 条无法回查的旧编号证据 |
| D04 | `chain_evidence_daily.SKHY` | 通过 | 〔自动证据，非结论〕窗口 10-20 `no_change / no_print_in_window`（10-21 06:01） | 用户确认（AI 建议） | 2026-10-23 | 10-23 抽查 10 条：✅9 ⚠️1。全部来自新闻/研报（SKHY 暂无自己的财报会，10-27 财报后才会有）；一条“4 月”被补成 2025-04，年份没有依据 |
| D05 | `calendar_finnhub_earnings_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:36），发现 24 | 用户确认（AI 建议） | 2026-10-21 |  |
| D06 | `calendar_yahoo_earnings_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:37），发现 13；`ats events upcoming --days 30` 列出 LRCX 10-21 16:00、SKHY 10-27、GOOG/MSFT 10-28、FOMC 10-27/28、GDP/PCE 10-29 |  |  | 运行层面正常；待抽查：LRCX 10-21、SKHY 10-27、GOOG/MSFT 10-28 等日期对照公司 IR；原备注：日期需你对照 IR / 官网 |
| D07 | `calendar_fomc_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `failed`（16:38），两次尝试；错误 `URLError: SSL UNEXPECTED_EOF_WHILE_READING` | 用户确认（AI 建议） | 2026-10-21 | 10-21 17:25 窗口已 succeeded（发现 38 条），网络恢复后改为通过；原：TLS 被重置（根因 2） |
| D08 | `calendar_bls_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `failed`（16:38）；错误 `SSLError: curl (35) Connection closed abruptly`（www.bls.gov） | 用户确认（AI 建议） | 2026-10-21 | 10-21 17:25 窗口已 succeeded（发现 48 条），网络恢复后改为通过；原：TLS 被重置（根因 2） |
| D09 | `calendar_bea_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `failed`（16:38）；错误 `URLError: SSL UNEXPECTED_EOF_WHILE_READING`（www.bea.gov） | 用户确认（AI 建议） | 2026-10-21 | 10-21 17:25 窗口已 succeeded（发现 6 条），网络恢复后改为通过；原：TLS 被重置（根因 2） |
| D10 | `calendar_finalize_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:37）；但日历库 133 个事件**无一**到过 released（见 E02） | 用户确认（AI 建议） | 2026-10-21 | 任务本身正常；日历库至今无事件到过 released，另见 E02；原备注：10-22 15:10 那次决定 LRCX 能否 released |
| D11 | `factset_month_end_ingest` |  | 〔自动证据，非结论〕"从未运行"（首次 11-01）；最近 FactSet 指数报告 2026-09-25（10-02 入库）；宏观检查提醒"FactSet 报告 26 天未更新（>14）" |  |  | 待 11-01 12:30 首次运行，或到时记接受缺口；原备注：待 11-01 12:30 触发，或记接受缺口；宏观时效阈值仍按周度，见 M03 |

## 4. 数据产品

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| P01 | 信息文档包 → information |  | 〔自动证据，非结论〕`g0_product information`：ok；提醒 ASML 7 天内无准入文档 |  |  |  |
| P02 | 公司研究包 → fundamental |  | 〔自动证据，非结论〕ok；提醒 SKHY、COHR、LITE、AVGO 无财务行 |  |  | 财务仅试点 GOOG、MU、LRCX |
| P03 | 产业链数据包 → layer |  | 〔自动证据，非结论〕ok；提醒 L1、L3 无命题（配置缺口，信心封顶 0.3）；L5、L6、L7 部分证人无观测 |  |  |  |
| P04 | 产业链数据包（行业横截面）→ sector |  | 〔自动证据，非结论〕ok；提醒 L3 全层无财务、L4/L6 多数无财务、19 只共识超 14 天、FactSet 行业分区不可用 |  |  |  |
| P05 | 宏观数据包 → macro |  | 〔自动证据，非结论〕ok；提醒台/韩出口最新期 2026-08 过旧（51 天）、FactSet 指数报告 26 天 |  |  |  |
| P06 | 实时市场包（运行时）→ technical |  | 〔自动证据，非结论〕10-21 上午检查 ok（全部标的有 4 天内日线） |  |  |  |
| P07 | 六个分析师读路由固定在新路径 |  | 〔自动证据，非结论〕`ats consumer-cutover status`：information、fundamental、layer、macro、sector 为 `target`；**technical 为 `legacy`**（generation 4，10-20 09:36 被旧的 daily-health 自动切回，理由"daily data-product health check failed"：当时全部标的无 4 天内日线） |  |  | 准备时新发现。29c6b4d（10-20 15:05）去掉了自动切回，但没有把 technical 切回来。是 W03 失败的直接原因 |

## 5. Workflow：定时

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| W01 | `information-brief`（research_schedules；event_routes `pead-daily`） |  | 〔自动证据，非结论〕定时 10-20 20:00 应触发 → 23:18 运行 `complete`，11 只全部新算；10-19 20:00 那次 `complete`（全部复用）；更早有 4 次定时 `incomplete` |  |  | 今天 20:00 再看一次 |
| W02 | `fundamental-routine`（同上） |  | 〔自动证据，非结论〕定时 10-20 21:00 → 23:27 `complete`（信息简报复用 11、基本面新算 11）；10-19 09:48 一次 `incomplete`（LRCX blocked：financial input unavailable），09:52 起恢复 |  |  | 今天 21:00 再看一次 |
| W03 | `technical-review`（`technical-daily`） |  | 〔自动证据，非结论〕定时 10-20 22:00 → 23:29 **失败**：`RouteUnavailable: workflow has no explicitly authorized read window`（dispatcher.err.log），没有写入触发记录；10-19 22:00 `incomplete`（SKHY 未产出投影，其余 10 只新算） |  |  | 准备时新发现。原因见 P07；不修复则每个工作日失败。今天 22:00 预计仍失败 |
| W04 | `macro-review`（`macro-weekly`） |  | 〔自动证据，非结论〕周六 10-17 09:00 的定时在 21:24 补跑 `complete`（负责人 21:23 切到 dispatcher）；`ats macro show` regime neutral（10-17）；Obsidian `宏观分析-宏观-2026-10-17-v2.md`（21:28）；同日 17:29–17:37 另有几次演练运行 |  |  | 待 10-24 09:00 触发 |
| W05 | `sector-review`（`sector-weekly`） |  | 〔自动证据，非结论〕10-17 09:30 的定时在 21:24 记录、21:28 运行 `complete`（层次复用 8、新算 1）；`ats sector show ai_hardware` 有 10-17 八层配置；新路径**不写** Obsidian 行业报告（见 L01） |  |  | 待 10-24 09:30 触发 |
| W06 | `layer-review`（`sector-weekly` 中先补跑） |  | 〔自动证据，非结论〕10-17 多次 `complete`（17:31 新算 8，其余复用 8）；8 层投影产出于 10-17 17:30 |  |  | 待 10-24 随 W05 |
| W07 | 复用：数据没变不重复请求模型 |  | 〔自动证据，非结论〕10-19 20:00 信息简报 11 只全部复用；10-20 基本面例行运行中信息简报 11 只复用；10-17 层次/行业多次运行以复用为主。另：10-19 21:12–22:12 有约 10 次 TSM/ASML 事件评审的**手动**运行，每次都新算（强制重算，非调度行为） |  |  | 可对照 OpenRouter 后台的当日请求量 |
| W08 | 调度负责人：7 个工作流归 Dispatcher |  | 〔自动证据，非结论〕`ats dispatch owner-log`：7 个工作流最后一条均为 legacy → dispatcher（10-17 21:23，actor claude-executing-user-approved-switch） |  |  | `config/workflow/workflow_owners.yaml` 里写的默认值是 legacy，实际以库里记录为准（R1 会去掉该默认值） |
| W09 | 手动运行入口（event_routes `manual.allowed_task_ids`，7 个） |  | 〔自动证据，非结论〕fundamental-event 有 19 次手动运行 `complete`（10-19）；information-brief 有 3 次手动 `incomplete`（10-19 09:44–09:46） |  |  | 不必新跑；💰 新跑会产生费用 |
| W10 | 可读报告：技术面汇总报告、行业报告（含层级与 HTML）、每周产业链证据周报（Dispatcher 路径生成） |  | 〔自动证据，非结论〕10-21 时新路径不生成这三类报告（技术面适配器 `write_report=False`、行业适配器 `write_reports=False`，证据周报随旧调度器下线）；Obsidian 目录里最近的技术面读数（10-17 17:30）、层分析（10-17 09:38）不是流水线产物 |  |  | **待 tasks.md 第 2 组（RP）完成后核验**；技术面报告还依赖 W03 先修好 |

## 6. Workflow：事件

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| E01 | 财报计划 → 信息简报 + 基本面例行（`earnings/plan`；`event_triggers.earnings: true`） |  | 〔自动证据，非结论〕10-20 02:10 的日历消费为 LRCX、SKHY、GOOG、MSFT 的计划事件唤醒信息简报与基本面例行，均 `complete`；`ats pead show LRCX` 显示 `phase=prep` 的 Q1 FY2027 基线 |  |  |  |
| E02 | 财报发布 → 信息简报 + 基本面事件评审（`earnings/release`） |  | 〔自动证据，非结论〕日历库 133 个事件**无一**到过 released；ASML FY2026Q3（10-14）、TSM FY2026Q3（10-15）的事件触发都停在 `waiting_material / release_material_not_admitted`；10-19 的事件评审是手动运行。`g0_event LRCX`：FY2027Q1 v2，10-21 16:00 纽约，planned，准入材料无 |  |  | **日历事件路径首次真实端到端**：LRCX 10-22 04:00 发布 → 10-22 15:10 发布确认 → 10-23 02:10 唤醒。准备时判断很可能同样卡在"等材料"。备选 SKHY 10-27、GOOG/MSFT 10-28 |
| E03 | FOMC 发布 → 宏观评审（`event_triggers.macro_sector: false`） |  | 〔自动证据，非结论〕没有任何 macro-review 事件运行；但有 FOMC 等 `waiting_material` 事件触发记录（与 E04 合计 75 条，记录于 10-20 02:14），因为写"等材料"的步骤不看开关；执行前会看开关，所以不会跑 |  |  | 准备时新发现。2025 年起的宏观事件仍全部为 planned |
| E04 | CPI / PCE / NFP / GDP 发布 → 宏观评审（同一开关） |  | 〔自动证据，非结论〕同 E03 |  |  |  |
| E05 | 日历来源冲突与人工裁决 |  | 〔自动证据，非结论〕`ats events candidates --status conflict`：6 条（NVDA FY2027Q3、TSM FY2026Q4、ASML FY2026Q4、LITE FY2027Q1、AVGO FY2026Q4、MRVL FY2027Q3），原因 `cross_source_trigger_fields_disagree`（Yahoo 与 Finnhub 字段不一致）；`g0_calendar` 总体 `conflict` |  |  | 最近的是 LITE 11-05、NVDA 11-17 |

## 7. 运行保障

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| A01 | 三个服务在跑 |  | 〔自动证据，非结论〕`launchctl list`：com.ats.dispatcher PID 8699；com.ats.serve PID 8776（上次退出码 -15）；com.ats.data-refresh PID `-`、退出码 0；`/health` 返回 `{"ok":true}` |  |  |  |
| A02 | 发布时检查（刷新后） |  | 〔自动证据，非结论〕29c6b4d 之后 `consumer-cutover log` 没有新行（没有状态变化就不记）；data-refresh.out.log 里 `consumer_health` 0 行；隔离测试 `tests/test_consumer_health.py` 27 passed |  |  | 生产上还没有一条新机制的记录 |
| A03 | 读取前检查与阻断语义 |  | 〔自动证据，非结论〕生产运行记录里没有 `data_product_unhealthy`；隔离测试 `test_unhealthy_data_products_block_the_task_before_it_runs` passed |  |  |  |
| A04 | 飞书告警（状态变化时一次） |  | 〔自动证据，非结论〕隔离测试 `test_observe_logs_and_alerts_only_when_the_status_changes`、`test_a_failing_alert_does_not_break_observation` passed；生产上新机制尚未发过告警 |  |  | 请在飞书群历史里看 10-20 09:36 前后有没有 technical 的告警（旧机制） |
| A05 | `com.ats.dispatcher` 重启后加载新代码、启动补跑 |  | 〔自动证据，非结论〕进程启动于 10-20 16:10:51，晚于最后一次改 `src/` 的提交（64926c4，16:09:52）；10-20 20:00 信息简报在 23:18、21:00 基本面在 23:27 补跑成功，各一次 |  |  | 工作区里另有未提交的 `src/` 改动（其他会话），未加载，不影响本项 |
| A06 | 数据刷新控制器每小时运行与补跑 |  | 〔自动证据，非结论〕`g0_refresh` 显示最近一次定时运行 10-21 10:29，launchd 已加载；周/月任务过宽限后记 `misfire` |  |  |  |
| A07 | 日志无未处理异常 |  | 〔自动证据，非结论〕dispatcher.err.log：10-20 23:29 technical-review `RouteUnavailable` 一处 Traceback；data-refresh.err.log 只有一行无害的 MallocStackLogging 提示 |  |  | 与 P07、W03 同源 |

## 8. 运维文档

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| M01 | `docs/OPERATIONS.md` |  | 〔自动证据，非结论〕§2 的 7 条日常命令都能只读运行。不一致：§2/§4 说读路由"固定为 target"，technical 实为 legacy；`ats events candidates` 不加 `--status conflict` 会列出 100 条已准入候选；§9 的 partial 缺陷未提 TrendForce/SemiAnalysis |  |  |  |
| M02 | `docs/ARCHITECTURE.md`（§2–§4、§6–§9、§13） |  | 〔自动证据，非结论〕§6 "六个分析师读新路径"与 P07 不符；§6/§13 的日历事件唤醒在发布侧尚未自动走通（E02） |  |  |  |
| M03 | `docs/agents/*.md`（7 份） |  | 〔自动证据，非结论〕`MACRO_ANALYST.md`（约 497 行）与 `docs/data/FACTSET_EARNINGS_INSIGHT.md`（约 57 行）仍写周六 08:10 `factset_weekly_ingest` 与 08:50 `weekly_review`，二者已下线；`SECTOR_ANALYST.md` 仍以旧入口 `ats sector review` 写 Obsidian 为主要用法 |  |  |  |
| M04 | `README.md` |  | 〔自动证据，非结论〕约 204 行"各分析师的报告存成 Obsidian 笔记"，新路径只有宏观、基本面写 |  |  |  |

## 9. 旧入口副作用对照

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| L01 | 旧手动入口副作用对照确认（[`inventory.md`](inventory.md) 第四、五节） |  | 〔自动证据，非结论〕inventory.md（a1c89e3）：技术面、行业（含层级与 HTML）、产业链证据周报的可读报告新路径不再生成（Q1）；命题治理命令无新路径对应物（Q3）；宏观周报已补上。10-21 裁决（ede7412）：Q1 恢复，走 tasks.md 第 2 组 RP（G0 之前）；Q3 保留 |  |  | 待 RP 完成、重启后的真实运行；写明你核验的 inventory.md 版本（提交号） |

---

## G0 结论

> 全部 78 项都有结论、核验人、日期后再填本节。只要有一项是"未通过"，G0 就不通过。

- **核验日期：**
- **范围：** U01–U08、S01–S25、D01–D11（44 个数据刷新任务）；P01–P07；W01–W10；E01–E05；A01–A07；M01–M04；L01。共 78 项
- **结果统计：** 通过 __ 项；接受缺口 __ 项；不适用 __ 项；未通过 __ 项
- **已接受的遗留缺口**（每条写：编号、缺口是什么、为什么可以接受、谁/何时跟进）：
  -
- **未通过项与修复任务**（修好复核后移到上面或删去）：
  -
- **G0 结论：** 通过 / 不通过
- **签字：** __________　　日期：__________


g0_refresh:
任务                                                 来源                             窗口         最近结果           原因               完成(台北)      近7天失败
ramp_ai_index_p10d_probe                           ramp_ai_index                  2026-10-14 no_change      upstream_no_change   10-14 16:11 0
openrouter_rankings_p7d_ingest                     openrouter_rankings            2026-10-15 succeeded   10-16 10:26 0
frontier_ai_capability_model_discovery_p7d         frontier_ai_capability         2026-10-16 succeeded   10-16 15:36 0
frontier_ai_capability_benchmark_probe_p7d         frontier_ai_capability         2026-10-16 succeeded   10-16 15:37 0
frontier_ai_capability_official_release_probe_p7d  frontier_ai_capability_officia 2026-10-16 succeeded   10-16 15:38 0
frontier_ai_capability_full_audit_p7d              frontier_ai_capability         2026-10-16 succeeded   10-16 15:39 0
frontier_ai_labs_revenue_p7d_refresh               sacra_public_company_profiles, 2026-10-16 failed         controller_RuntimeError   10-16 17:41 1
kr_semiconductor_exports_monthly                   kr_ecos_exports                2026-10-15 no_change   10-15 08:14 0
tw_ic_exports_monthly                              tw_mof_exports                 2026-10-15 failed         command_or_result_failed   10-15 09:17 1
dram_contract_price_monthly                        trendforce_dram                2026-10-15 succeeded   10-15 09:20 0
consensus_goog_daily                               yfinance_consensus             2026-10-21 running   -           0
consensus_nvda_daily                               yfinance_consensus             2026-10-20 succeeded   10-20 15:31 0
consensus_skhy_daily                               yfinance_consensus             2026-10-20 succeeded   10-20 15:31 0
consensus_tsm_daily                                yfinance_consensus             2026-10-20 succeeded   10-20 15:32 0
consensus_asml_daily                               yfinance_consensus             2026-10-20 succeeded   10-20 15:32 0
consensus_cohr_daily                               yfinance_consensus             2026-10-20 succeeded   10-20 15:33 0
consensus_lrcx_daily                               yfinance_consensus             2026-10-20 succeeded   10-20 15:33 0
consensus_lite_daily                               yfinance_consensus             2026-10-20 succeeded   10-20 15:34 0
consensus_avgo_daily                               yfinance_consensus             2026-10-20 succeeded   10-20 15:34 0
consensus_mrvl_daily                               yfinance_consensus             2026-10-20 succeeded   10-20 15:35 0
consensus_msft_daily                               yfinance_consensus             2026-10-20 succeeded   10-20 15:36 0
company_financials_weekly.GOOG                     defeatbeta_stock_statement     2026-10-17 no_change   10-17 14:48 0
company_financials_weekly.MU                       defeatbeta_stock_statement     2026-10-17 no_change   10-17 14:48 0
company_financials_weekly.LRCX                     defeatbeta_stock_statement     -          从未运行          
macro_series_daily                                 macro_series_feeds             2026-10-20 succeeded   10-20 20:27 0
defeatbeta_sec_index_daily                         defeatbeta_sec_filing_index    2026-10-20 succeeded      index_metadata_only   10-20 16:38 0
sec_official_body_daily                            sec_edgar_filing_body          -          从未运行          
defeatbeta_transcript_daily                        defeatbeta_earnings_transcript 2026-10-20 no_change   10-20 20:27 0
ai_hardware_knowledge_daily                        ai_hardware_knowledge_corpus   2026-10-20 no_change   10-20 20:27 0
trendforce_news_weekly                             trendforce_news                2026-10-19 failed         controller_RuntimeError   10-19 23:05 1
semianalysis_newsletter_weekly                     semianalysis                   2026-10-19 failed         controller_RuntimeError   10-20 00:05 1
ibkr_news_daily                                    ibkr_news,yfinance_live_news   2026-10-20 failed         controller_RuntimeError   10-21 06:01 7
chain_evidence_daily.TSM                           chain_evidence_observer        2026-10-20 no_change      already_extracted   10-21 06:01 0
chain_evidence_daily.ASML                          chain_evidence_observer        2026-10-20 no_change      already_extracted   10-21 06:01 0
chain_evidence_daily.MU                            chain_evidence_observer        2026-10-20 no_change      no_print_in_window   10-21 06:01 0
chain_evidence_daily.SKHY                          chain_evidence_observer        2026-10-20 no_change      no_print_in_window   10-21 06:01 0
macro_theme_news_daily                             macro_theme_news               2026-10-20 succeeded   10-20 20:28 1
calendar_finnhub_earnings_daily                    finnhub_earnings_calendar      2026-10-20 succeeded   10-20 15:36 0
calendar_yahoo_earnings_daily                      yahoo_earnings_calendar        2026-10-20 succeeded   10-20 15:37 0
calendar_fomc_daily                                federal_reserve_fomc           2026-10-20 failed         command_or_result_failed   10-20 16:38 1
calendar_bls_daily                                 bls_release_calendar           2026-10-20 failed         command_or_result_failed   10-20 16:38 1
calendar_bea_daily                                 bea_release_schedule           2026-10-20 failed         command_or_result_failed   10-20 16:38 1
calendar_finalize_daily                            calendar_maintenance           2026-10-20 succeeded   10-20 15:37 0
factset_month_end_ingest                           factset_earnings_insight_doc   -          从未运行  



g0_sources:

① 最近一次运行（这次来源列出了多少候选、新放行多少、被质量门拒绝多少；时间为台北）
  ai_hardware_knowledge_corpus                      no_change         更新 10-20 20:27  发现 10 准入 0 隔离 0  
  defeatbeta_earnings_transcript                    no_change         更新 10-20 20:27  发现 111 准入 0 隔离 0  
  defeatbeta_sec_filing_index                       succeeded         更新 10-20 16:37  发现 116 准入 116 隔离 0  
  defeatbeta_transcript:AMD                  AMD    not_yet_available 更新 09-22 15:52  发现 0 准入 0 隔离 0  ["transcript_not_returned"]
  defeatbeta_transcript:MRVL                 MRVL   not_yet_available 更新 09-22 15:52  发现 0 准入 0 隔离 0  ["transcript_not_returned"]
  defeatbeta_transcript:TSM                  TSM    succeeded         更新 09-22 15:52  发现 1 准入 1 隔离 0  
  ibkr_news                                  DOWJON partial           更新 10-21 05:00  发现 0 准入 0 隔离 0  
  sec_edgar_filing_body                             failed            更新 10-17 22:18  发现 7 准入 0 隔离 0  {"source_unavailable": 7}  ← 来源连不上，正文没拿到：不算准入也不算隔离
  sec_official:earnings_release:AMD          AMD    succeeded         更新 09-22 15:52  发现 0 准入 1 隔离 0  
  sec_official:earnings_release:ASML         ASML   unreachable       更新 10-16 21:55  发现 0 准入 0 隔离 0  {"company_tickers:ConnectError": 3, "filing_metada  ← 来源连不上，正文没拿到：不算准入也不算隔离
  sec_official:earnings_release:MRVL         MRVL   quarantined       更新 09-22 15:52  发现 0 准入 0 隔离 1  
  sec_official:earnings_release:MU           MU     missing           更新 09-22 16:00  发现 0 准入 0 隔离 0  
  sec_official:earnings_release:TSM          TSM    unreachable       更新 10-19 06:55  发现 0 准入 0 隔离 0  {"company_tickers:ConnectError": 3, "filing_metada  ← 来源连不上，正文没拿到：不算准入也不算隔离
  sec_official:periodic_filing:AMD           AMD    succeeded         更新 09-22 15:52  发现 0 准入 1 隔离 0  
  sec_official:periodic_filing:ASML          ASML   unreachable       更新 10-16 21:55  发现 0 准入 0 隔离 0  {"company_tickers:ConnectError": 3, "filing_metada  ← 来源连不上，正文没拿到：不算准入也不算隔离
  sec_official:periodic_filing:MRVL          MRVL   missing           更新 09-22 15:52  发现 0 准入 0 隔离 0  
  sec_official:periodic_filing:TSM           TSM    unreachable       更新 10-19 06:55  发现 0 准入 0 隔离 0  {"company_tickers:ConnectError": 3, "filing_metada  ← 来源连不上，正文没拿到：不算准入也不算隔离
  semianalysis                               SEMIAN partial           更新 10-19 23:05  发现 0 准入 0 隔离 0  
  trendforce_news                            TRENDF partial           更新 10-19 22:05  发现 19 准入 16 隔离 0  {"budget_deferred":3}
② 候选条目累计去向（历次运行合计；一条候选只有一个去向）
  ai_hardware_knowledge_corpus       no_change 10
  defeatbeta                         accepted 2
  defeatbeta_earnings_transcript     accepted 2；no_change 225
  ibkr_news                          accepted 39；deferred 764（budget_deferred）；no_change 19；quarantined 120（body_below_minimum、budget_deferred、entity_association_not_verified）
  sec                                accepted 6；quarantined 4（identity_unresolved、period_mismatch、period_unresolved、release_semantics_missing）
  sec_edgar_filing_body              accepted 3；no_change 3
  semianalysis                       accepted 2；no_change 16
  tavily                             quarantined 11（identity_unresolved、official_domain_unconfigured、period_mismatch、period_unresolved）
  trendforce_news                    accepted 50；deferred 8（budget_deferred）；no_change 18；quarantined 7（body_below_minimum、budget_deferred）
  yfinance_live_news                 pending_human_review 29（human_title_url_review_required）；quarantined 315（body_below_minimum、body_unavailable、budget_deferred、entity_association_not_verified、human_title_url_review_required）
③ 文档库存量（已存进库的文档；“存库失败”=已入库但标记抓取失败的行，拿不到的候选不会入库，所以不计在内）
  ai_hardware_knowledge_corpus               文档   10  存库失败   0  库内最新一篇存入 09-25 17:25
  defeatbeta                                 文档   27  存库失败   0  库内最新一篇存入 09-22 16:00
  defeatbeta_earnings_transcript             文档  118  存库失败   0  库内最新一篇存入 10-16 17:41
  documents                                  文档    4  存库失败   2  库内最新一篇存入 10-19 06:55
  factset_earnings_insight_doc               文档    3  存库失败   0  库内最新一篇存入 10-02 11:19
  ibkr_news                                  文档  126  存库失败  28  库内最新一篇存入 10-20 05:06
  kr_customs                                 文档    1  存库失败   1  库内最新一篇存入 08-07 13:47
  macro_theme_news                           文档   90  存库失败   0  库内最新一篇存入 10-20 20:27
  manual                                     文档    3  存库失败   0  库内最新一篇存入 09-22 16:00
  newsletter:SemiAnalysis                    文档   15  存库失败   0  库内最新一篇存入 09-24 22:33
  sec                                        文档   31  存库失败   0  库内最新一篇存入 09-22 15:52
  sec_edgar_filing_body                      文档    6  存库失败   0  库内最新一篇存入 09-26 21:46
  semianalysis                               文档   23  存库失败   1  库内最新一篇存入 10-13 08:35
  trendforce                                 文档   25  存库失败   0  库内最新一篇存入 08-18 11:07
  trendforce_news                            文档   68  存库失败   0  库内最新一篇存入 10-19 22:05

## 上线后待办

- [ ] 产业链证据（`chain_evidence_daily`）当前是试点名单 TSM / ASML / MU / SKHY。G0 验证全部完成、正式上线后，在 `config/data/schedules.yaml` 的 `chain_evidence_daily.entities` 里扩充更多公司（每家一个任务，每次最多 8 次抽取调用）。扩之前先看 D01–D04 抽查发现的标注偏差是否已处理，并决定旧编号证据（约 60%）如何回查。
