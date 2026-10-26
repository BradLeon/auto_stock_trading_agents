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
| U06 | `sec_official_body_daily`（sec_edgar_filing_body） | 未通过 | 〔自动证据，非结论〕任务未启用，控制器"从未运行"；sec_edgar_filing_body 最近一次 10-17 22:18 `failed`（source_unavailable ×7）；ASML、TSM 官方披露 `unreachable`（ConnectError） | 用户确认（AI 建议） | 2026-10-21 | 10-21 网络已恢复（sec.gov 200）；17:54 手动跑一次：发现 20、准入 4、隔离 4、12 条因每次运行请求预算（20 次）用完而未取；仍未启用定时任务，待你决定是否在 schedules.yaml 开启；10-26 复查：任务已启用并在入库（LRCX、TSM、ASML、SKHY 等官方材料已入库），但 10-25 两次定时运行失败、准入 0——原因是每次选中的 20 条里 8 条已入库、8 条永久没有新闻稿附件的申报每天重试、新申报排在最后预算用尽（SKHY 10-23 的 6-K 实为只有封面的短公告，非财报）。已改：已入库的 7 天内不再取、同一份 3 个不同日子都找不到就放弃、预算只算入库成功的申报（取不到正文的退还）、预算用完没轮到的记“延后”；SEC 正文任务改 09:30 UTC、日历发布确认改 10:00 UTC。仍为“未通过”，待新配置下的定时运行（今晚台北 17:30）结果 |
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
| D06 | `calendar_yahoo_earnings_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:37），发现 13；`ats events upcoming --days 30` 列出 LRCX 10-21 16:00、SKHY 10-27、GOOG/MSFT 10-28、FOMC 10-27/28、GDP/PCE 10-29 | 用户确认（AI 建议） | 2026-10-26 | 运行层面正常；待抽查：LRCX 10-21、SKHY 10-27、GOOG/MSFT 10-28 等日期对照公司 IR；原备注：日期需你对照 IR / 官网；用户 10-26 确认通过：LRCX 10-21 实际发布日与两家日历一致；SKHY 10-27、GOOG/MSFT 10-28 两家日期时段一致 |
| D07 | `calendar_fomc_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `failed`（16:38），两次尝试；错误 `URLError: SSL UNEXPECTED_EOF_WHILE_READING` | 用户确认（AI 建议） | 2026-10-21 | 10-21 17:25 窗口已 succeeded（发现 38 条），网络恢复后改为通过；原：TLS 被重置（根因 2） |
| D08 | `calendar_bls_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `failed`（16:38）；错误 `SSLError: curl (35) Connection closed abruptly`（www.bls.gov） | 用户确认（AI 建议） | 2026-10-21 | 10-21 17:25 窗口已 succeeded（发现 48 条），网络恢复后改为通过；原：TLS 被重置（根因 2） |
| D09 | `calendar_bea_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `failed`（16:38）；错误 `URLError: SSL UNEXPECTED_EOF_WHILE_READING`（www.bea.gov） | 用户确认（AI 建议） | 2026-10-21 | 10-21 17:25 窗口已 succeeded（发现 6 条），网络恢复后改为通过；原：TLS 被重置（根因 2） |
| D10 | `calendar_finalize_daily` | 通过 | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:37）；但日历库 133 个事件**无一**到过 released（见 E02） | 用户确认（AI 建议） | 2026-10-21 | 任务本身正常；日历库至今无事件到过 released，另见 E02；原备注：10-22 15:10 那次决定 LRCX 能否 released |
| D11 | `factset_month_end_ingest` | 通过 | 〔自动证据，非结论〕"从未运行"（首次 11-01）；最近 FactSet 指数报告 2026-09-25（10-02 入库）；宏观检查提醒"FactSet 报告 26 天未更新（>14）" | 用户确认（AI 建议） | 2026-10-23 | 采集、入库、发布闸门这条链路已实际跑通：10-23 手动采集了 FactSet 最新一期（2026-10-16），已入库，但发布闸门按设计拦下（4 个必需指标这期文字里找不到：负面指引家数、上调预期的行业数、前瞻 PE 的 10 年/5 年均值；且指数核心需独立复核，复核编号为空），所以 Macro 仍读已发布的 9-25 期。定时任务每月 1 日才跑、抓上月最后一期，首次运行 11-01，到时才有定时运行的证据。Macro 健康检查的 FactSet 提醒阈值已从 14 天改为 40 天（与任务 40 天新鲜度一致），此前每月后半段必然误报。 |

## 4. 数据产品

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| P01 | 信息文档包 → information | 通过 | 〔自动证据，非结论〕`g0_product information`：ok；提醒 ASML 7 天内无准入文档 | 用户确认（AI 建议） | 2026-10-23 | 结论 ok 仅表示无阻断问题（产品可读、行格式完整、标的范围非全空），不评价数据够不够。ASML 无新文档是 10-15 财报后的安静期，无需处理 |
| P02 | 公司研究包 → fundamental | 通过 | 〔自动证据，非结论〕ok；提醒 SKHY、COHR、LITE、AVGO 无财务行 | 用户确认（AI 建议） | 2026-10-23 | ok。财务覆盖已从试点扩到 15 家（含 TSM/ASML/SKHY 走 issuer_ir_files）；原备注“仅试点 GOOG、MU、LRCX”已过时 |
| P03 | 产业链数据包 → layer | 通过 | 〔自动证据，非结论〕ok；提醒 L1、L3 无命题（配置缺口，信心封顶 0.3）；L5、L6、L7 部分证人无观测 | 用户确认（AI 建议） | 2026-10-23 | ok。缺口两类：①L1、L3 未配置命题——评审是盲的、状态固定 steady、置信度封顶 0.3，属内容决策，需用户定命题；②L5 的 DOWJONES/SEMIANALYSIS、L6/L7 的 KR_US_SUMMIT 无观测——前两者文章未进证据抽取，KR_US_SUMMIT 是事件，有事件才有观测 |
| P04 | 产业链数据包（行业横截面）→ sector | 通过 | 〔自动证据，非结论〕ok；提醒 L3 全层无财务、L4/L6 多数无财务、19 只共识超 14 天、FactSet 行业分区不可用 | 用户确认（AI 建议） | 2026-10-23 | ok。缺口：①财务名单太窄（META、CRWV、SPCX、AAOI、CRDO、AXTI、SNDK、STX、WDC、AMAT、005930.KS 无治理过的财务，L3 全层为空，按同层中性处理）；②19 只一致预期超 14 天（每日任务只覆盖 10 个标的）；③FactSet 行业分区未发布（10-16 期被发布闸门拦下，见 D11） |
| P05 | 宏观数据包 → macro | 通过 | 〔自动证据，非结论〕ok；提醒台/韩出口最新期 2026-08 过旧（51 天）、FactSet 指数报告 26 天 | 用户确认（AI 建议） | 2026-10-23 | ok。FactSet 新鲜度提醒阈值已改为 40 天（原 14 天对月更来源必然误报）；台/韩出口已于 10-21、10-22 手动重采补到 2026-09 |
| P06 | 实时市场包（运行时）→ technical | 通过 | 〔自动证据，非结论〕10-21 上午检查 ok（全部标的有 4 天内日线） | 用户确认（AI 建议） | 2026-10-23 | ok。10-21 检查全部标的有 4 天内日线；技术分析师读路由已为 target（generation 5，10-21 03:16） |
| P07 | 六个分析师读路由固定在新路径 | 通过 | 〔自动证据，非结论〕`ats consumer-cutover status`：六个分析师均为 `target`；technical generation 5（10-21 03:16 切回） | 用户确认（AI 建议） | 2026-10-23 | 10-23 复查 `ats consumer-cutover status`：六个分析师（information、fundamental、layer、macro、sector、technical）读路由均为 `target`。原记录里 technical 为 legacy 已过时（10-21 已切回 target） |

## 5. Workflow：定时

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| W01 | `information-brief`（research_schedules；event_routes `pead-daily`） | 通过 | 〔自动证据，非结论〕定时 10-20 20:00 应触发 → 23:18 运行 `complete`，11 只全部新算；10-19 20:00 那次 `complete`（全部复用）；更早有 4 次定时 `incomplete` | 用户确认（AI 建议） | 2026-10-24 | 用户 10-24 手动验证通过。过程中发现并修复：①ASML 简报在回看窗口无候选材料时什么也不发布，被记 missing（10-23 20:00 定时与 10-24 事件触发 incomplete），现发布明确的“无新增材料”简报；②LRCX/TSM/ASML 财报事件原先因 SEC 公告没有财季标签配不上，现按申报日期关联，三个事件均已 released 并 complete。10-21 一次 incomplete 为证据片段在原文里找不到（校验拦截），次日自愈 |
| W02 | `fundamental-routine`（同上） | 通过 | 〔自动证据，非结论〕定时 10-20 21:00 → 23:27 `complete`（信息简报复用 11、基本面新算 11）；10-19 09:48 一次 `incomplete`（LRCX blocked：financial input unavailable），09:52 起恢复 | 用户确认（AI 建议） | 2026-10-24 | 用户 10-24 手动验证通过。基本面事件评审：TSM、LRCX、ASML 均 complete（LRCX 原被 period-guard 中止——defeatbeta 转录稿正文读不出财季，现采用来源目录声明的财季）。注意：三份评审摘要开头为“无比较依据”，E02 需核对基线是否在财报前冻结 |
| W03 | `technical-review`（`technical-daily`） | 未通过 | 〔自动证据，非结论〕定时 10-20 22:00 → 23:29 **失败**：`RouteUnavailable: workflow has no explicitly authorized read window`（dispatcher.err.log），没有写入触发记录；10-19 22:00 `incomplete`（SKHY 未产出投影，其余 10 只新算） | 用户确认（AI 建议） | 2026-10-24 | 10-24 重验：定时触发 10-22、10-23 均 incomplete。原因：①SKHY 上市仅 75 根日线（SMA200 需 200），旧代码不发布投影被记 missing，现改为发布 unavailable 的数据缺失投影（34d54a0）；②调度进程 10-21 起没重启，10-23 22:00 的定时运行用的是旧代码，所以仍 missing，且单标的摘要把当天汇总覆盖成只剩 SKHY（“可计算标的 0 只”）；③逐标的并发汇总存在读-合并-写丢数据（手动运行 11 只只存下 8 只），现改为由当天全部投影重建摘要并加锁。调度进程已于 10-24 重启，下一次定时运行 10-26（周一）22:00，届时复验：触发 complete、`ats technical show` 列全部可计算标的并单列数据缺失 |
| W04 | `macro-review`（`macro-weekly`） | 通过 | 〔自动证据，非结论〕周六 10-17 09:00 的定时在 21:24 补跑 `complete`（负责人 21:23 切到 dispatcher）；`ats macro show` regime neutral（10-17）；Obsidian `宏观分析-宏观-2026-10-17-v2.md`（21:28）；同日 17:29–17:37 另有几次演练运行 | 用户确认（AI 建议） | 2026-10-24 | 用户 10-24 验证通过：周六 09:00 定时触发 complete |
| W05 | `sector-review`（`sector-weekly`） | 通过 | 〔自动证据，非结论〕10-17 09:30 的定时在 21:24 记录、21:28 运行 `complete`（层次复用 8、新算 1）；`ats sector show ai_hardware` 有 10-17 八层配置；新路径**不写** Obsidian 行业报告（见 L01） | 用户确认（AI 建议） | 2026-10-24 | 用户 10-24 验证通过：周六 09:30 定时触发 complete，运行为“新算 9”（8 层 + 行业） |
| W06 | `layer-review`（`sector-weekly` 中先补跑） | 通过 | 〔自动证据，非结论〕10-17 多次 `complete`（17:31 新算 8，其余复用 8）；8 层投影产出于 10-17 17:30 | 用户确认（AI 建议） | 2026-10-24 | 用户 10-24 验证通过：layer-review 没有单独的定时配置，由 sector-review 先补跑；10-24 09:30 那次 8 层全部新算（运行记为“新算 9”），均早于行业评审（09:40）完成。g0_runs 已改为能按依赖任务名搜到。10-25 追查 L1 报告“数据和命题没有被启用”：L1 层配置当时 `claims` 为空，三个证据观察器只作旁路描述。现已把观察器读数并入链证据引擎——`config/sectors/ai_hardware.yaml` 的 L1 声明 3 条命题，`data/sources/governed_observer.py` 把 BTOS/RPS/Ramp、Sacra/OpenRouter、前沿 benchmark 的读数变成带说话人/维度/方向/证据行的观测，含义由原判读器判，采集走 `chain_l1_governed_evidence_daily`（每天 09:00 UTC，无模型调用）。重跑 layer-review → sector-review：L1 生产化命题 supportive（3 簇、2 类立场），商业化与原始能力两条因只有第三方估算/单一说话人而 unknown，层信心 0.55；L1 投影已被行业评审读入。同日发现韩国/台湾/DRAM 三条第三方序列的观测自 08-12 起无新写入（没有定时任务调 `chain-source-collect`），已补每周一 09:30 UTC 三个任务并手动各跑一次补齐（新增观测 KR 12、TW 4、DRAM 6） |
| W07 | 复用：数据没变不重复请求模型 | 通过 | 〔自动证据，非结论〕10-19 20:00 信息简报 11 只全部复用；10-20 基本面例行运行中信息简报 11 只复用；10-17 层次/行业多次运行以复用为主。另：10-19 21:12–22:12 有约 10 次 TSM/ASML 事件评审的**手动**运行，每次都新算（强制重算，非调度行为） | 用户确认（AI 建议） | 2026-10-24 | 用户 10-24 验证通过：数据未变时复用（如技术面 10-23 的“复用 10/11”、简报与基本面的“复用”计数） |
| W08 | 调度负责人：7 个工作流归 Dispatcher | 通过 | 〔自动证据，非结论〕`ats dispatch owner-log`：7 个工作流最后一条均为 legacy → dispatcher（10-17 21:23，actor claude-executing-user-approved-switch） | 用户确认（AI 建议） | 2026-10-24 | 用户 10-24 验证通过：调度负责人为 dispatcher |
| W09 | 手动运行入口（event_routes `manual.allowed_task_ids`，7 个） | 通过 | 〔自动证据，非结论〕fundamental-event 有 19 次手动运行 `complete`（10-19）；information-brief 有 3 次手动 `incomplete`（10-19 09:44–09:46） | 用户确认（AI 建议） | 2026-10-24 | 用户 10-24 验证通过：手动运行入口白名单均有手动运行记录 |
| W10 | 可读报告：技术面汇总报告、行业报告（含层级与 HTML）、每周产业链证据周报（Dispatcher 路径生成） |  | 〔自动证据，非结论〕10-21 时新路径不生成这三类报告（技术面适配器 `write_report=False`、行业适配器 `write_reports=False`，证据周报随旧调度器下线）；Obsidian 目录里最近的技术面读数（10-17 17:30）、层分析（10-17 09:38）不是流水线产物 |  |  | **待 tasks.md 第 2 组（RP）完成后核验**；技术面报告还依赖 W03 先修好 |

## 6. Workflow：事件

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| E01 | 财报计划 → 信息简报 + 基本面例行（`earnings/plan`；`event_triggers.earnings: true`） | 通过 | 〔自动证据，非结论〕10-20 02:10 的日历消费为 LRCX、SKHY、GOOG、MSFT 的计划事件唤醒信息简报与基本面例行，均 `complete`；`ats pead show LRCX` 显示 `phase=prep` 的 Q1 FY2027 基线 | 用户确认（AI 建议） | 2026-10-26 | 用户 10-26 验证通过，未发现问题 |
| E02 | 财报发布 → 信息简报 + 基本面事件评审（`earnings/release`） | 通过 | 〔自动证据，非结论〕日历库 133 个事件**无一**到过 released；ASML FY2026Q3（10-14）、TSM FY2026Q3（10-15）的事件触发都停在 `waiting_material / release_material_not_admitted`；10-19 的事件评审是手动运行。`g0_event LRCX`：FY2027Q1 v2，10-21 16:00 纽约，planned，准入材料无 | 用户确认（AI 建议） | 2026-10-26 | **日历事件路径首次真实端到端**：LRCX 10-22 04:00 发布 → 10-22 15:10 发布确认 → 10-23 02:10 唤醒。准备时判断很可能同样卡在"等材料"。备选 SKHY 10-27、GOOG/MSFT 10-28。10-26 核对：LRCX/TSM/ASML 评审写“无比较依据”，原因是评审时的冻结读了已被评分阶段清空的档案（基线 10-19 其实已发布）并写空记录；已改为读财报前已发布的基线、已冻结的不覆盖；LRCX 重跑后冻结基线 7 个预期维度＋共识，比较依据变为“仅共识”（EPS +1.05%、营收 +1.06%）。随后查明更深的根因：同一季度被存成两份档案（日历写 `FY2027Q1`、财报前准备存为 `Q1 FY2027`），事件评审从空档案起步，所以报告第一、二节全空、实际值被抽成平台指标名（`financial.revenue.gaap…`）、记分卡各行“未提供数据”。10-26 已统一为 `Q1 FY2027` 写法（`data/period.dossier_label`，事件请求入口统一；`merge_dossier_aliases` 合并现有 LRCX/TSM/ASML 的重复档案及打分台账），抽取实际值时无基线则按记分卡维度命名并加“读数对不上任何维度即中止”的护栏；LRCX/TSM/ASML 重跑：记分卡七行均有读数（LRCX +1.15、TSM +1.65、ASML +1.38，预期差为正），对冻结基线与共识的差异均有；LRCX 报告 2.3 期权隐含预期仍为空（财报前未取到期权数据）。TSM/ASML 的预期集合在财报前（10-14/15 之前）已建，冻结有效。用户 10-26 核对 LRCX/TSM/ASML 重跑后的基本面报告通过：LRCX 由日历事件自动唤醒（10-24 02:10，该次在修复 dispatcher 后）；基线冻结、共识与基线差异、记分卡打分均已就绪 |
| E03 | FOMC 发布 → 宏观评审（`event_triggers.macro_sector: false`） | 通过 | 〔自动证据，非结论〕没有任何 macro-review 事件运行；但有 FOMC 等 `waiting_material` 事件触发记录（与 E04 合计 75 条，记录于 10-20 02:14），因为写"等材料"的步骤不看开关；执行前会看开关，所以不会跑 | 用户确认（AI 建议） | 2026-10-26 | 准备时新发现。2025 年起的宏观事件仍全部为 planned；用户 10-26 验证通过：宏观事件开关关着，没有 event 行执行；`waiting_material` 噪声接受 |
| E04 | CPI / PCE / NFP / GDP 发布 → 宏观评审（同一开关） | 通过 | 〔自动证据，非结论〕同 E03 | 用户确认（AI 建议） | 2026-10-26 | 用户 10-26 验证通过，同 E03 |
| E05 | 日历来源冲突与人工裁决 | 通过 | 〔自动证据，非结论〕`ats events candidates --status conflict`：6 条（NVDA FY2027Q3、TSM FY2026Q4、ASML FY2026Q4、LITE FY2027Q1、AVGO FY2026Q4、MRVL FY2027Q3），原因 `cross_source_trigger_fields_disagree`（Yahoo 与 Finnhub 字段不一致）；`g0_calendar` 总体 `conflict` | 用户确认（AI 建议） | 2026-10-26 | 最近的是 LITE 11-05、NVDA 11-17。用户 10-26 核对通过，保留 Finnhub + Yahoo 两家日历不动。冲突原因：Finnhub 与 Yahoo 对 7 场未来财报的日期或时段说法不一致（LRCX、MRVL、TSM 日期差一天；NVDA、LITE、AVGO、ASML 时段不同），系统按设计隔离 Yahoo 那条、沿用 Finnhub，不取平均。不是时区问题（美东盘后/台北下午发的财报换时区不会差一天），多半是公司尚未公告、两家都是估算。上周 LRCX、ASML、TSM 的实际发布日两家一致且都对；本周 SKHY（10-27）、GOOG、MSFT（10-28）两家日期时段一致，无冲突。最近一条冲突 LITE 11-05，届时对照公司公告核一次，不一致再用 `ats events override`；上线后可考虑日期一致时用 Yahoo 钟点补 Finnhub 缺失 |

## 7. 运行保障

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| A01 | 三个服务在跑 | 通过 | 〔自动证据，非结论〕`launchctl list`：com.ats.dispatcher PID 8699；com.ats.serve PID 8776（上次退出码 -15）；com.ats.data-refresh PID `-`、退出码 0；`/health` 返回 `{"ok":true}` | 用户确认（AI 建议） | 2026-10-26 | 用户 10-26 确认通过：dispatcher、serve 在跑，data-refresh 状态 0，/health 正常 |
| A02 | 发布时检查（刷新后） | 通过 | 〔自动证据，非结论〕29c6b4d 之后 `consumer-cutover log` 没有新行（没有状态变化就不记）；data-refresh.out.log 里 `consumer_health` 0 行；隔离测试 `tests/test_consumer_health.py` 27 passed | 用户确认（AI 建议） | 2026-10-26 |生产上还没有一条新机制的记录；用户 10-26 核对飞书截图通过：10-25 19:31“数据产品健康检查未通过：sector（L1_app 投影读不出）”与 19:55“已恢复”各一条，与生产日志记录一致、同状态不重复。该次失败由我撤回的 L1 旁路提交留下的不兼容投影引起，检查按设计拦住并告警 |
| A03 | 读取前检查与阻断语义 | 通过 | 〔自动证据，非结论〕生产运行记录里没有 `data_product_unhealthy`；隔离测试 `test_unhealthy_data_products_block_the_task_before_it_runs` passed | 用户确认（AI 建议） | 2026-10-26 | 用户 10-26 确认通过：隔离测试（tests/test_workflow_dispatcher.py -k unhealthy）通过，生产无 data_product_unhealthy 阻断 |
| A04 | 飞书告警（状态变化时一次） | 通过 | 〔自动证据，非结论〕隔离测试 `test_observe_logs_and_alerts_only_when_the_status_changes`、`test_a_failing_alert_does_not_break_observation` passed；生产上新机制尚未发过告警 | 用户确认（AI 建议） | 2026-10-26 |请在飞书群历史里看 10-20 09:36 前后有没有 technical 的告警（旧机制）；用户 10-26 核对飞书通过：由好转坏、由坏转好各发一条，不刷屏；隔离测试 27 passed |
| A05 | `com.ats.dispatcher` 重启后加载新代码、启动补跑 | 通过 | 〔自动证据，非结论〕进程启动于 10-20 16:10:51，晚于最后一次改 `src/` 的提交（64926c4，16:09:52）；10-20 20:00 信息简报在 23:18、21:00 基本面在 23:27 补跑成功，各一次 | 用户确认（AI 建议） | 2026-10-26 | 工作区里另有未提交的 `src/` 改动（其他会话），未加载，不影响本项；用户 10-26 确认通过：进程启动于 12:07，晚于最后一次 src/ 改动；修复后重启为核验之外的必要重启 |
| A06 | 数据刷新控制器每小时运行与补跑 | 通过 | 〔自动证据，非结论〕`g0_refresh` 显示最近一次定时运行 10-21 10:29，launchd 已加载；周/月任务过宽限后记 `misfire` | 用户确认（AI 建议） | 2026-10-26 | 用户 10-26 确认通过：控制器最近定时运行 10-26 12:27 / 13:28，间隔约 1 小时 |
| A07 | 日志无未处理异常 | 通过 | 〔自动证据，非结论〕dispatcher.err.log：10-20 23:29 technical-review `RouteUnavailable` 一处 Traceback；data-refresh.err.log 只有一行无害的 MallocStackLogging 提示 | 用户确认（AI 建议） | 2026-10-26 |与 P07、W03 同源；用户 10-26 要求查根因：10-22 14:10、10-23 02:25/14:10 的 `IdentityConflict` 是同一场财报的“计划事件”记录与“财报日到了仍缺材料”的等待记录共用触发键（工作流+事件号+事件版本），后者内容不同被拒，一个事件出错还会让排在后面的事件丢等待记录；事件升版（材料入库）后键变化所以消失，并非已修好，本周 SKHY/GOOG/MSFT 会同样触发。已按方案 A 修复：同键已有记录就保留并跳过、不报错，不改键算法；补测试（无修复时失败）。其余 ERROR：10-20 技术面读窗口（P07/W03 同源，已恢复）、10-21 信息简报证据片段定位不到、10-24 02:10 基本面事件评审（旧进程未加载新代码，已修复） |

## 8. 运维文档

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| M01 | `docs/OPERATIONS.md` | 通过 | 〔自动证据，非结论〕§2 的 7 条日常命令都能只读运行。不一致：§2/§4 说读路由"固定为 target"，technical 实为 legacy；`ats events candidates` 不加 `--status conflict` 会列出 100 条已准入候选；§9 的 partial 缺陷未提 TrendForce/SemiAnalysis | 用户确认（AI 建议） | 2026-10-26 | 更新：10-26 文档已按最新事实更新：§5 补第三方序列采集任务，§6 命令改为 `--status conflict` 并说明冲突的含义，§9 的 partial 加 TrendForce/SemiAnalysis、增 `IdentityConflict` 与基本面报告为空两行；“读路由固定为 target”现在成立（technical 10-21 已恢复） |
| M02 | `docs/ARCHITECTURE.md`（§2–§4、§6–§9、§13） | 通过 | 〔自动证据，非结论〕§6 "六个分析师读新路径"与 P07 不符；§6/§13 的日历事件唤醒在发布侧尚未自动走通（E02） | 用户确认（AI 建议） | 2026-10-26 | 更新：10-26：§6 已与现实一致（六个分析师读新路径成立；日历事件唤醒 E02 已走通），只改了“来源冲突”一句的含义 |
| M03 | `docs/agents/*.md`（7 份） | 通过 | 〔自动证据，非结论〕`MACRO_ANALYST.md`（约 497 行）与 `docs/data/FACTSET_EARNINGS_INSIGHT.md`（约 57 行）仍写周六 08:10 `factset_weekly_ingest` 与 08:50 `weekly_review`，二者已下线；`SECTOR_ANALYST.md` 仍以旧入口 `ats sector review` 写 Obsidian 为主要用法 | 用户确认（AI 建议） | 2026-10-26 | 更新：10-26 已改：`MACRO_ANALYST.md`、`FACTSET_EARNINGS_INSIGHT.md` 的周六 08:10/08:50 旧写法改为每月 1 日月末摄入与 40 天提醒；`LAYER_ANALYST.md` 补第三方数据/L1 观察器；`FUNDAMENTAL_ANALYST.md` 补冻结基线来源与统一财季写法。`SECTOR_ANALYST.md`“自动调度”与补跑命令也已改为 Dispatcher + `ats workflow run`；其余角色文档补了手动运行行（信息、层级、基本面、技术面），旧入口只在“会被拒绝”的说明里出现 |
| M04 | `README.md` | 通过 | 〔自动证据，非结论〕约 204 行"各分析师的报告存成 Obsidian 笔记"，新路径只有宏观、基本面写 | 用户确认（AI 建议） | 2026-10-26 | 更新：10-26 已改：Obsidian 报告覆盖宏观、基本面、技术面、层级、行业；日历冲突命令加 `--status conflict` |

## 9. 旧入口副作用对照

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| L01 | 旧手动入口副作用对照确认（[`inventory.md`](inventory.md) 第四、五节） | 通过 | 〔自动证据，非结论〕inventory.md（a1c89e3）：技术面、行业（含层级与 HTML）、产业链证据周报的可读报告新路径不再生成（Q1）；命题治理命令无新路径对应物（Q3）；宏观周报已补上。10-21 裁决（ede7412）：Q1 恢复，走 tasks.md 第 2 组 RP（G0 之前）；Q3 保留 | 用户确认（AI 建议） | 2026-10-26 | 核验版本：inventory.md 提交 d6cb6d5（先于它的重写为 8ae5524、8a89fd9，修复为 4588a19）。用户 10-26 逐行阅读第四节全部 ok；核对中发现并补上行业周报 `行业分析-…md` 新路径漏写（8a89fd9）；Q3 命题治理与 kbperturb 确认保留不迁移；Q4：R4 缩小为 risk_manager、risk_validator.apply_guardrails、pead/monitor、pead/research 四项，pead 框架本体（prep/score/report/units/triage/outputs）保留；因 Risk/Chief/Clerk 新路径尚未试用，只清退用不着的 Risk，其余留待后续清退。恢复的技术面报告随 W10 复核 |

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

D01–D04 结论补充（2026-10-23）：用户决定接受 d01-d04-sample-v2.md 的水准，不再继续优化召回（不做“候选句清单”方案）。
当前生效配置：模型 `deepseek/deepseek-v4.1-flash-1017`（OpenRouter）+ 新提示词（发布方/说话人区分、抽取范围与示例）+ 引文逐字校验。
已知并接受的局限：①召回不稳（同一文档两次可差一倍，文章偶发 0 条）；②方向/立场仍有少量偏差；③约 60% 的旧证据来源编号无法回查（见 d01-d04-sample.md）。

P01–P07 结论补充（2026-10-23）：`g0_product` 的 ok 仅表示“无阻断问题”，提醒是数据缺口，不影响结论。按“上线后能否自己消失”分三类：
- 到时间会自己好：ASML 无新文档（财报后的安静期）。
- 只改配置、不用开发：扩财务采集名单（美股走 SEC）、给 19 个标的补一致预期每日任务、扩产业链证据名单。
- 光靠上线或配置解决不了：①005930.KS 无财务（SEC 不覆盖，需另找来源或接受缺口）；②FactSet 行业分区（10-16 期缺 4 个必需指标且需独立复核，11-01 的 10 月最终期未必能放行，需实测）；③L1、L3 命题（内容决策，需用户定）；④L5/L6/L7 部分证人无观测（文章类证据抽取未覆盖，或从命题中去掉该证人）。

## 上线后待办

- [ ] 产业链证据（`chain_evidence_daily`）当前是试点名单 TSM / ASML / MU / SKHY。G0 验证全部完成、正式上线后，在 `config/data/schedules.yaml` 的 `chain_evidence_daily.entities` 里扩充更多公司（每家一个任务，每次最多 8 次抽取调用）。扩之前先看 D01–D04 抽查发现的标注偏差是否已处理，并决定旧编号证据（约 60%）如何回查。
- [ ] 上线后处理 P01–P07 提醒里“光靠上线解决不了”的四项：005930.KS 财务来源、FactSet 行业分区放行条件（11-01 实测）、L3 命题（L1 已于 10-25 补齐）、L5/L6/L7 缺失证人。名单类缺口（财务、一致预期、证据）随上面的扩名单待办一并改配置。
- [ ] **下阶段任务：旧路径 / 旧入口的下线与清退**（即本变更的 R1–R5）。清退范围里**不含** `ats evidence claims/propose/proposals/review/kbreview` 与 `ats sector kbperturb`：10-26 复核确认它们是人在回路的治理与开发工具，不是分析师入口，保留且不迁移（理由见 inventory.md 4.1 与第五节 Q3）。
- [ ] 上线后择期一次性过一遍命题治理：`claim_proposals` 里有 1 条 2026-08-06 起一直 pending 的候选命题，没归属的观测至少 500 条；用 `ats evidence proposals`、`ats evidence kbreview` 看一遍，决定采纳、拒绝或接受不用（采纳后仍须手工写进 `config/sectors/ai_hardware.yaml`）。
