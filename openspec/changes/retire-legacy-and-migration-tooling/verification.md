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
| U01 | `ibkr_news_daily`（ibkr_news + yfinance_live_news） |  | 〔自动证据，非结论〕窗口 10-20 `failed / controller_RuntimeError`（10-21 06:01），近 7 天失败 7 次；来源状态 partial；文档库 ibkr_news 126 篇、失败 28，最近抓取 10-20 05:06 |  |  | 已知缺陷：partial 当失败重试 → `queue_task_not_claimable`。需决定是否接受 |
| U02 | `trendforce_news_weekly` |  | 〔自动证据，非结论〕窗口 10-19：首次 `article_source_partial`（准入 16、budget_deferred 3），重试 `controller_RuntimeError` → failed；文档库 trendforce_news 68 篇，最近 10-19 22:05 |  |  | 同 U01 缺陷（准备时新发现）。待 10-26 22:00 触发 |
| U03 | `semianalysis_newsletter_weekly` |  | 〔自动证据，非结论〕窗口 10-19 `failed / controller_RuntimeError`，来源状态 partial、发现 0；文档库 semianalysis 23 篇，最近抓取 10-13 08:35 |  |  | 同 U01 缺陷。待 10-26 22:15 触发 |
| U04 | `defeatbeta_transcript_daily` |  | 〔自动证据，非结论〕窗口 10-20 `no_change`（20:27），发现 111、准入 0；文档库 118 篇，最近抓取 10-16 17:41 |  |  |  |
| U05 | `defeatbeta_sec_index_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded / index_metadata_only`（16:38），发现 116、准入 116 |  |  | 只收申报目录，不收正文 |
| U06 | `sec_official_body_daily`（sec_edgar_filing_body） |  | 〔自动证据，非结论〕任务未启用，控制器"从未运行"；sec_edgar_filing_body 最近一次 10-17 22:18 `failed`（source_unavailable ×7）；ASML、TSM 官方披露 `unreachable`（ConnectError） |  |  | 已知：Clash fake-ip 使后台进程连不上 SEC。也是 E02 缺财报材料的原因之一。需决定 |
| U07 | `macro_theme_news_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（20:28），近 7 天失败 1；文档库 90 篇，最近抓取 10-20 20:27 |  |  |  |
| U08 | `ai_hardware_knowledge_daily` |  | 〔自动证据，非结论〕窗口 10-20 `no_change`（20:27）；文档库 10 篇，最近抓取 09-25（知识文件未改即不变） |  |  |  |

非结构化通用观察〔自动证据，非结论〕：生产文档库里有测试夹具文档（来源 `file:…/tmp/pytest-*/…/t.txt`，状态 accepted），即测试写进了生产库。信息文档包检查 P01 仍为 ok。是否登记为缺陷由你决定。

## 2. Dataflow：结构化

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| S01 | `consensus_goog_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:30）；yfinance_consensus 最近 3 次入库各准入 42 行 |  |  |  |
| S02 | `consensus_nvda_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:31） |  |  |  |
| S03 | `consensus_skhy_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:31） |  |  |  |
| S04 | `consensus_tsm_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:32） |  |  |  |
| S05 | `consensus_asml_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:32） |  |  |  |
| S06 | `consensus_cohr_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:33） |  |  |  |
| S07 | `consensus_lrcx_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:33）；`g0_company LRCX`：2026-09-30 季 EPS 均值 2.18（2.09–2.28）、营收均值 81.4 亿美元，目标价均值 375.9 |  |  | 可与 Yahoo Finance 页面对照 |
| S08 | `consensus_lite_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:34） |  |  |  |
| S09 | `consensus_avgo_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:34） |  |  |  |
| S10 | `consensus_mrvl_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:35） |  |  |  |
| S11 | `consensus_msft_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:36） |  |  |  |
| S12 | `company_financials_weekly.GOOG` |  | 〔自动证据，非结论〕窗口 10-17 `no_change`（14:48），defeatbeta_stock_statement 发现 247–261 行未变 |  |  | 待 10-24 14:30 触发 |
| S13 | `company_financials_weekly.MU` |  | 〔自动证据，非结论〕窗口 10-17 `no_change`（14:48） |  |  | 待 10-24 14:30 触发 |
| S14 | `company_financials_weekly.LRCX` |  | 〔自动证据，非结论〕控制器"从未运行"（10-19 才加入）；10-19 09:49 有一次非控制器入库，准入 272 行；`g0_company LRCX` 最新报告期 2026-06-30，季度 EPS 1.81、营收见输出 |  |  | 待 10-24 14:30 首次由控制器运行 |
| S15 | `macro_series_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（20:27），发现 850、准入 11、未变 839；宏观检查 ok |  |  |  |
| S16 | `kr_semiconductor_exports_monthly` |  | 〔自动证据，非结论〕窗口 10-15 `no_change`（08:14），上游 18 行无新增；宏观检查提醒：最新期 2026-08 已过 51 天 |  |  | 可用 10-15 证据，或待 11-15 |
| S17 | `tw_ic_exports_monthly` |  | 〔自动证据，非结论〕窗口 10-15 `failed / command_or_result_failed`，入库记录 `parse_failed`；宏观检查提醒：最新期 2026-08 已过 51 天 |  |  | 需决定；下次 11-15 |
| S18 | `dram_contract_price_monthly` |  | 〔自动证据，非结论〕窗口 10-15 `succeeded`（09:20），准入 3 |  |  | 可用 10-15 证据，或待 11-15 |
| S19 | `ramp_ai_index_p10d_probe` |  | 〔自动证据，非结论〕窗口 10-14 `no_change / upstream_no_change`；上游最新期 2026-08-01，已入库 |  |  | 待 10-24 15:30 触发 |
| S20 | `openrouter_rankings_p7d_ingest` |  | 〔自动证据，非结论〕窗口 10-15 `succeeded`（10-16 10:26 补跑），准入 714 |  |  | 待 10-22 16:00 触发 |
| S21 | `frontier_ai_capability_model_discovery_p7d` |  | 〔自动证据，非结论〕窗口 10-16 `succeeded`（15:36）；上游最新期 2026-10-16，已入库 |  |  | 待 10-23 触发 |
| S22 | `frontier_ai_capability_benchmark_probe_p7d` |  | 〔自动证据，非结论〕窗口 10-16 `succeeded`（15:37），准入 163 |  |  | 待 10-23 触发 |
| S23 | `frontier_ai_capability_official_release_probe_p7d` |  | 〔自动证据，非结论〕窗口 10-16 `succeeded`（15:38），frontier_ai_public_model_cards 准入 560 |  |  | 待 10-23 触发 |
| S24 | `frontier_ai_capability_full_audit_p7d` |  | 〔自动证据，非结论〕窗口 10-16 `succeeded`（15:39） |  |  | 待 10-23 触发 |
| S25 | `frontier_ai_labs_revenue_p7d_refresh` |  | 〔自动证据，非结论〕窗口 10-16 `failed`：首次 `command_or_result_failed`，重试 `controller_RuntimeError`；Sacra 检查 `methodology_drift`（10-16 16:40）；10-09 那次 partial（validation_failed 1） |  |  | 需决定；待 10-23 15:50 触发 |

## 3. Dataflow：派生

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| D01 | `chain_evidence_daily.TSM` |  | 〔自动证据，非结论〕窗口 10-20 `no_change / already_extracted`（10-21 06:01）；`ats evidence show --entity TSM` 有 2026-10-15 财报后的读数（A14 节点、先进封装光罩尺寸、agentic AI CPU 需求等） |  |  |  |
| D02 | `chain_evidence_daily.ASML` |  | 〔自动证据，非结论〕窗口 10-20 `no_change / already_extracted`（10-21 06:01） |  |  |  |
| D03 | `chain_evidence_daily.MU` |  | 〔自动证据，非结论〕窗口 10-20 `no_change / no_print_in_window`（10-21 06:01） |  |  | 近 7 天无新财报，属正常 |
| D04 | `chain_evidence_daily.SKHY` |  | 〔自动证据，非结论〕窗口 10-20 `no_change / no_print_in_window`（10-21 06:01） |  |  | SKHY 10-27 财报后应有新读数 |
| D05 | `calendar_finnhub_earnings_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:36），发现 24 |  |  |  |
| D06 | `calendar_yahoo_earnings_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:37），发现 13；`ats events upcoming --days 30` 列出 LRCX 10-21 16:00、SKHY 10-27、GOOG/MSFT 10-28、FOMC 10-27/28、GDP/PCE 10-29 |  |  | 日期需你对照 IR / 官网 |
| D07 | `calendar_fomc_daily` |  | 〔自动证据，非结论〕窗口 10-20 `failed`（16:38），两次尝试；错误 `URLError: SSL UNEXPECTED_EOF_WHILE_READING` |  |  | 准备时新发现；疑似与 SEC 同类网络问题 |
| D08 | `calendar_bls_daily` |  | 〔自动证据，非结论〕窗口 10-20 `failed`（16:38）；错误 `SSLError: curl (35) Connection closed abruptly`（www.bls.gov） |  |  | 同上 |
| D09 | `calendar_bea_daily` |  | 〔自动证据，非结论〕窗口 10-20 `failed`（16:38）；错误 `URLError: SSL UNEXPECTED_EOF_WHILE_READING`（www.bea.gov） |  |  | 同上 |
| D10 | `calendar_finalize_daily` |  | 〔自动证据，非结论〕窗口 10-20 `succeeded`（15:37）；但日历库 133 个事件**无一**到过 released（见 E02） |  |  | 10-22 15:10 那次决定 LRCX 能否 released |
| D11 | `factset_month_end_ingest` |  | 〔自动证据，非结论〕"从未运行"（首次 11-01）；最近 FactSet 指数报告 2026-09-25（10-02 入库）；宏观检查提醒"FactSet 报告 26 天未更新（>14）" |  |  | 待 11-01 12:30 触发，或记接受缺口；宏观时效阈值仍按周度，见 M03 |

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
