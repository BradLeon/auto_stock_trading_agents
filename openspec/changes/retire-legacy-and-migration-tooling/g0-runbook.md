# G0 新路径人工核验手册

> 对应 design.md D1 与 tasks.md 第 3 组（G0）。结果写在同目录的 [`verification.md`](verification.md)，编号一一对应。
> 本手册由 2026-10-21 的准备会话编写；手册里的每条命令都已在本机只读运行过一次。
> 按 ede7412 修订后的顺序，tasks.md 第 2 组 **RP（可读报告接续）在 G0 之前完成**：技术面、行业（含层级与 HTML）、每周产业链证据周报由新路径重新生成。这三类报告单独作为 **W10** 核验，要等 RP 完成、服务重启后的真实运行再做。
> 任务编号按 ede7412 重排后的 tasks.md：RP=第 2 组，G0=第 3 组，R2=第 4 组，R1=第 5 组，R3=第 6 组，R4=第 7 组。

---

## 0. 怎么用这份手册

### 0.1 这是在做什么

退役（删除旧代码、旧命令、旧机制）不可逆。G0 是删除前的最后一道门：由你亲自确认新路径上

- 44 个数据刷新任务在按时跑、收进来的数据对；
- 6 个分析师读到的数据产品合格、范围对；
- 7 个研究工作流按时间表和日历事件被唤醒、产出的内容对、数据没变时不重复请求模型；
- 检查、告警、阻断、重启补跑这些保障真的起作用；
- 运维文档照着做能完成日常检查。

**自动检查只能证明"在跑"，证明不了"跑出来的东西对"**，所以每一项最后由你下结论。任一项"未通过"，依赖 G0 的退役条目（R1–R4）都不执行。

### 0.2 规则

- 本手册的命令**全部只读**：不加载/卸载服务、不改 `var/` 下的数据、不发模型请求、不下单。
- 标 **💰** 的命令会发模型请求、产生费用，**非必需**，只在你想额外确认时用。
- 标 **⛔** 的命令是写操作，本手册里出现只是为了说明"不要运行"。
- 时间一律写**台北时间**（命令输出也已换算成台北时间，除非标了 UTC）。纽约现在是夏令时，比台北晚 12 小时；**11-01 起纽约改冬令时，纽约时间的任务在台北会晚 1 小时**。
- 定时任务需要 Mac 醒着。错过时刻后的补跑宽限：数据刷新 24 小时；信息简报、基本面例行 12 小时；技术面 30 分钟；宏观、行业、层次 48 小时。

### 0.3 准备（每开一个新终端做一次）

每开一个新终端，先执行下面这一行。

```bash
cd /Users/liuchao/Code/trading/auto_stock_trading_agents && source openspec/changes/retire-legacy-and-migration-tooling/g0-tools.zsh
```

这一行会进入仓库目录、激活 `.venv`、设置台北时区，并定义全部 `g0_` 函数（关掉终端就没了，不改任何文件）。函数的源码在 [`g0-tools.zsh`](g0-tools.zsh)，**不要把它们整段复制粘贴进终端**：长文本粘贴进终端时可能丢字符（出现过 `lambd"failed" …` 这种缺了一截的语法错误）。手册里其余命令都在这个终端里运行；如果在别的终端里运行带 `# …` 注释的命令，需要先执行一次 `setopt interactivecomments`。

> 说明：`g0_sources`、`g0_product technical` 要几十秒（前者扫全部来源，后者会实时取一次行情，不是模型请求）。`g0_latest`/`g0_show` 用 `sqlite3 -readonly` 只读打开 `var/ats.sqlite`。

### 0.3.1 现在就重采（会写数据）

不想等例行窗口时，用 `g0_rerun` 批量重采（写真实数据，不发模型请求）：

```bash
g0_rerun network --dry      # 先看会跑哪些：10-21 圈定的受网络断连影响的任务
g0_rerun network            # 确认后执行；也可以 g0_rerun U / S / D，或写任务名片段，如 g0_rerun consensus_ tw_ic
g0_history consensus_tsm 3  # 看某个任务每个窗口的结果、尝试次数和错误原文
```

- 每次重采有自己的窗口标签（如 `2026-10-21~manual-20261021T143000`），同一天可以反复跑，不占用例行窗口；未启用的任务不会跑。
- 数据是追加新版本：上游有变化就写入新的一版并成为最新，没变化记 `no_change`；旧版本保留作审计，不会被删除或改写。

### 0.4 建议顺序与时长

| 轮次 | 做什么 | 预计时长 |
|---|---|---|
| 第一轮（今天就能做） | §7 运行保障里的 A01（服务在跑）→ §1–§3 数据刷新 44 项（先 `g0_refresh` 看一张总表 10 分钟，再按类看内容约 60 分钟）→ §4 数据产品（20 分钟）→ §5、§6 看已有的运行记录与报告（60 分钟）→ §8 文档（30 分钟）→ §9 旧入口副作用对照（20 分钟） | 3–4 小时，可分几次 |
| 第二轮（等真实触发） | 按 0.5 的时间表，每次触发后 10–15 分钟看一次对应项 | 每次 10–15 分钟，跨度到 10-26（或 11-01 / 11-15，见下） |
| 收尾 | 在 verification.md 填结论与"G0 结论"一节，提交 | 30 分钟 |

月度任务与 FactSet：

- kr/tw 出口、DRAM 合约价（S16–S18）每月 15 日才跑。建议以 **10-15 那次真实运行**为证据，不必等 11-15。
- FactSet 月末摄入（D11）**从未真实运行过**，首次是 11-01。要么等 11-01，要么记为"接受缺口"。

### 0.5 需要等真实触发的项（台北时间）

| 时间 | 发生什么 | 对应核验项 |
|---|---|---|
| 每天 14:00–15:10 | 11 个共识预期、6 个日历任务 | S01–S11、D05–D10 |
| 每天 14:10、次日 02:10 | Dispatcher 消费日历事件（UTC 06:10、18:10） | E01–E04 |
| 每天 16:00–19:00 | SEC 索引、电话会稿、知识库、宏观指标、宏观新闻 | U04、U05、U07、U08、S15 |
| 每天 04:30 / 06:00 | IBKR 新闻（纽约 16:30）/ 产业链证据 ×4 | U01、D01–D04 |
| **今天 10-21 20:00 / 21:00 / 22:00** | 信息简报 / 基本面例行 / 技术面（工作日纽约 08:00 / 09:00 / 10:00） | W01、W02、W03（W03 预计失败，见 P07） |
| **10-22 04:00** | **LRCX 盘后财报发布**（纽约 10-21 16:00） | E02 |
| 10-22 15:10 | 日历"发布确认"：LRCX 财报稿或申报已准入时，事件变为 released | E02、D10 |
| 10-22 16:00 | OpenRouter 排行（每 7 天） | S20 |
| 10-23 02:10 | 日历消费：LRCX 已 released 时唤醒信息简报 + 基本面事件评审——**日历事件路径第一次真实端到端** | E02 |
| 10-23 15:20–15:50 | 前沿 AI 能力 ×4、AI 实验室收入（每 7 天） | S21–S25 |
| **10-24（周六）09:00 / 09:30** | 宏观评审 / 行业评审（先自动补跑层次评审） | W04、W05、W06 |
| RP（第 2 组）完成并重启后的下一个工作日 22:00 / 下一个周六 09:30 | 技术面汇总报告 / 行业报告、层级报告、HTML、产业链证据周报 | W10 |
| 10-24 14:30 / 15:30 | 公司财务 ×3（LRCX 第一次由控制器跑）/ Ramp AI 指数（每 10 天） | S12–S14、S19 |
| 10-26（周一）22:00 / 22:15 | TrendForce 新闻 / SemiAnalysis 周报 | U02、U03 |
| 10-28 04:00 起 | SKHY（10-27 纽约）、GOOG/MSFT（10-28 纽约）财报——E02 的第二、三次机会 | E02 |
| 11-01 12:30 | FactSet 月末摄入，首次 | D11 |
| 11-15 07:15 / 08:15 | 韩国出口 / 台湾出口、DRAM 合约价 | S16–S18（可用 10-15 证据代替） |

### 0.6 准备时已经发现的问题（只报告，未修）

这些是我跑只读命令时看到的，已写进 verification.md 对应行的"证据"栏。是否算"未通过"由你判断：

1. **技术面读路由停在旧路径，技术面评审在失败。** 10-20 09:36 旧的"每日健康检查自动切回"机制把 technical 的读路由切回了 legacy（5.5 小时后 29c6b4d 才去掉自动切回）。之后没有切回新路径的入口。10-20 22:00 的技术面定时运行报错"workflow has no explicitly authorized read window"，连触发记录都没写。照现状每个工作日都会失败。OPERATIONS 说"六个分析师固定 target"与实际不符。→ P07、W03、A07
2. **日历事件评审路径从未自动走通过。** 日历库里 133 个事件没有一个到过 released。ASML（10-14）、TSM（10-15）两次真实财报都停在"等材料"（`release_material_not_admitted`），10-19 是手动跑的事件评审。SEC 正文任务未启用，后台进程也连不上 SEC。照现状，LRCX 今晚的财报很可能同样停在"等材料"。→ E02、U06
3. **宏观事件开关关着，仍写了 75 条"等材料"的宏观触发记录。** 写"等材料"记录的那一步不看开关。真正执行时会看开关，所以不会跑宏观评审，但台账里有噪声；2025 年起的 FOMC/CPI/NFP 事件也都还是 planned。→ E03、E04
4. **FOMC、BLS、BEA 三个宏观日历来源 10-20 起刷新失败**（TLS 连接被重置，和 SEC 不可达像是同一类网络问题）。→ D07–D09
5. **"partial 当失败重试"的已知缺陷不只影响 ibkr_news**：TrendForce、SemiAnalysis 10-19 的周任务也是"首次 partial → 重试 RuntimeError → failed"。→ U02、U03
6. 台湾出口 10-15 解析失败；韩国出口没有新一期；宏观检查提示两者最新期都是 2026-08，已过 51 天。→ S16、S17
7. AI 实验室收入 10-16 失败（Sacra 页面方法变化 `methodology_drift`）。→ S25
8. FactSet 周度摄入已随旧调度器下线、改为月末摄入（343878f）。但宏观的数据检查仍按"每周一份"判时效（现提示 26 天未更新），`docs/agents/MACRO_ANALYST.md`、`docs/data/FACTSET_EARNINGS_INSIGHT.md` 也还写着"周六 08:10 周度摄入"。→ D11、M03
9. 生产文档库里有测试夹具文档（`file:…/tmp/pytest-*/…/t.txt`，状态 accepted），是测试写进了生产库。→ U 类通用、P01
10. OPERATIONS 写的 `ats events candidates` 会把 100 条已准入候选全列出来；要看待裁决冲突得加 `--status conflict`（现有 6 条）。→ E05、M01
11. 新路径里目前只有宏观和基本面写 Obsidian 报告；技术面、行业（含层级与 HTML）、产业链证据周报不写（inventory.md §4 的 Q1）。你已在 10-21 裁决恢复（ede7412，tasks.md 第 2 组 RP，G0 之前完成）。信息简报本来就不写报告。→ W10、L01

---

## 1. Dataflow：非结构化（U01–U08）

**核验什么：** 新闻、研报、电话会、申报、知识库这些文档有没有按时进来、能不能被准入，因为信息分析师和基本面分析师只读准入后的文档。

**怎么看：**

```bash
g0_refresh                     # 全部 44 个任务一张表（§1–§3 都用它），带“来源”列
g0_sources                     # 全部来源：①最近一次运行 ②候选累计去向 ③文档库存量（三段口径见下表）
g0_product information          # 信息分析师读的整包文档是否合格（见 P01）。只认 6 个分析师名，不认来源名
```

单看某一项：`g0_refresh` 和 `g0_sources` 都既认**任务名**也认**来源名**的片段，例如 `g0_refresh ibkr_news`、`g0_sources trendforce`、`g0_sources sec_edgar`、`g0_refresh defeatbeta_earnings_transcript`。输入的词匹配不上时会明确提示，不会静默。

**任务 ↔ 来源 ↔ 谁读它**

| 任务（`g0_refresh` 的“任务”列） | 来源（`g0_sources` 里的名字） | 合格检查归谁 |
|---|---|---|
| `ibkr_news_daily` | `ibkr_news`、`yfinance_live_news` | information |
| `trendforce_news_weekly` | `trendforce_news` | information |
| `semianalysis_newsletter_weekly` | `semianalysis` | information |
| `defeatbeta_transcript_daily` | `defeatbeta_earnings_transcript` | information |
| `defeatbeta_sec_index_daily` | `defeatbeta_sec_filing_index`（文档库里叫 `defeatbeta`） | information |
| `sec_official_body_daily` | `sec_edgar_filing_body`（另有 `sec_official:*` 行，是各公司的官方披露探测） | information |
| `macro_theme_news_daily` | `macro_theme_news` | macro |
| `ai_hardware_knowledge_daily` | `ai_hardware_knowledge_corpus` | layer（检查各层知识笔记是否已发布） |

来源没有各自的合格检查：`g0_product information` 查的是 11 只标的近几天**所有来源合在一起**的已准入文档（有没有、版本/哈希/来源是否齐全），不区分是哪个来源来的。所以某个来源停了，它不一定报错。U 类每一项靠 `g0_refresh` + `g0_sources` 判断，P01 只是兜底。

**怎么读 `g0_sources` 的三段（口径不同，数字不会互相对应）：**

| 段 | 回答的问题 | 各数字的意思 |
|---|---|---|
| ① 最近一次运行 | 最近这一次跑了什么 | **发现**=来源这次列出的候选条数；**准入**=其中新放行的；**隔离**=被质量门拒绝的；已经入库且没变的候选两者都不算（所以 `no_change` 时发现很多、准入 0 是正常的） |
| ② 候选累计去向 | 历次运行的候选最后都去了哪 | accepted 放行、no_change 没变、deferred 因预算延后、quarantined 隔离（括号里是原因）、pending_human_review 等你审 |
| ③ 文档库存量 | 库里现在有什么 | **文档**=已存进库的篇数；**存库失败**=已存入、但标记为抓取失败的行数；**库内最新一篇存入**=库里最近一份文档的存入时间，不是任务最近运行时间 |

几个容易误读的地方：
- **来源连不上时，准入 0、隔离 0、存库失败 0 同时出现是正常的。** 没拿到正文就谈不上合格不合格，也没有一行入库，所以哪个数字都不动。要看 ① 里的状态（`failed`、`source_unavailable`、`unreachable`）。
- **`defeatbeta_sec_filing_index` 的发现、准入都是 100+，库里却只有二三十篇文档。** 它只登记申报的目录条目，不下载正文，状态 `succeeded / index_metadata_only`，不产生发布的文档。
- **`defeatbeta_earnings_transcript` 发现 111、准入 0**：111 篇早就在库里，没变，所以不重复准入。
- **库内最新一篇存入很旧**（例如知识库 09-25、电话会稿 10-16）：说明那之后没有新内容，不等于任务停了。任务有没有在跑看 `g0_refresh` 的“完成”时间。

**怎么读输出：**
- `g0_refresh` 的“最近结果”：`succeeded`（有新数据）、`no_change`（跑了但上游没新东西，也算正常）、`failed`、`从未运行`。“窗口”是这次该跑的那一期。

**通用通过标准：**
1. 最近一个应跑窗口的结果是 `succeeded` 或 `no_change`；
2. 有新内容的来源，文档库篇数随时间增长、库内最新一篇存入时间与频率相符（日更的不超过 2 天，周更的不超过 8 天）；`no_change` 的来源看 `g0_refresh` 的“完成”时间；
3. P01 的检查结论是 `ok`，也就是准入文档的版本、哈希、来源齐全；
4. 抽读 1–2 篇：用 §5 的 W01 打开一份信息简报，看它引用的文档是否真实、时间是否对得上。

| 编号 | 任务 | 何时跑（台北） | 来源 | 专项看什么 | 常见的"不通过但可接受" |
|---|---|---|---|---|---|
| U01 | `ibkr_news_daily` | 每天 04:30 | `ibkr_news`（+ `yfinance_live_news`） | 文档库 `ibkr_news` 篇数在涨 | **已知缺陷**：每个窗口都是 partial（正文预算 10 篇，每次选中几百篇），控制器把 partial 当失败重试，第二次抛 `queue_task_not_claimable`，记为 failed。数据其实进来了。要求 TWS 已登录。需你决定是否接受 |
| U02 | `trendforce_news_weekly` | 周一 22:00 | `trendforce_news` | 10-26 那次的结果；文档库 `trendforce_news` 篇数 | 同 U01 的 partial 缺陷（10-19 首次准入 16 篇、3 篇因预算延后，随后重试失败） |
| U03 | `semianalysis_newsletter_weekly` | 周一 22:15 | `semianalysis` | 10-26 那次的结果；文档库 `semianalysis` 最近抓取 | 同上；10-19 那次发现 0 篇、记为 partial |
| U04 | `defeatbeta_transcript_daily` | 每天 17:00 | `defeatbeta_earnings_transcript` | 财报后几天内出现该公司的电话会稿 | 非财报季多为 `no_change`，正常 |
| U05 | `defeatbeta_sec_index_daily` | 每天 16:00 | `defeatbeta_sec_filing_index` | 结果 `succeeded`，原因 `index_metadata_only`（只收申报目录，不收正文） | — |
| U06 | `sec_official_body_daily` | 每天 16:30（**未启用**） | `sec_edgar_filing_body` | `g0_refresh sec_official` 显示"从未运行"；`g0_sources sec_` 看 SEC 各项是否 `unreachable` | **已知缺陷**：本机 Clash fake-ip 让后台进程到 sec.gov 的 TLS 被重置，浏览器正常。任务因此未启用。影响外国申报人（TSM、ASML）的官方披露，也是 E02 拿不到"财报材料"的原因之一。可记"接受缺口"或"未通过" |
| U07 | `macro_theme_news_daily` | 每天 19:00 | `macro_theme_news` | 文档库篇数在涨；P05 不提示"宏观新闻过旧" | 配置里归在 derived 一组，按 design D1 视为非结构化 |
| U08 | `ai_hardware_knowledge_daily` | 每天 17:15 | `ai_hardware_knowledge_corpus` | `no_change` 是常态（知识文件没改就不变） | — |

---

## 2. Dataflow：结构化（S01–S25）

**核验什么：** 共识预期、财务报表、宏观指标、行业序列、AI 指标这些数字是否按时更新、最新一期是否对，因为基本面、宏观、层次、行业分析都直接用这些数。

**怎么看：**

```bash
g0_refresh consensus_          # 换成 company_financials / macro_series / exports / dram / ramp / openrouter / frontier
g0_ingest yfinance_consensus   # 某个来源最近几次入库：发现/准入/隔离/未变，以及“上游最新期是否已入库”
g0_company LRCX                # 某只票的共识预期与最新财务（抽查用）
ats macro show                 # 宏观评审里引用的指标读数（看 S15–S18 的数字是否进了分析）
```

各任务对应的来源 id（`g0_ingest` 的参数）：

| 编号 | 任务 | 何时跑（台北） | `g0_ingest` 来源 |
|---|---|---|---|
| S01–S11 | `consensus_{goog,nvda,skhy,tsm,asml,cohr,lrcx,lite,avgo,mrvl,msft}_daily` | 每天 14:00–14:30，每只隔 3 分钟 | `yfinance_consensus` |
| S12–S14 | `company_financials_weekly.{GOOG,MU,LRCX}` | 周六 14:30 | `defeatbeta_stock_statement` |
| S15 | `macro_series_daily` | 每天 18:30 | `macro_series_feeds` |
| S16 | `kr_semiconductor_exports_monthly` | 每月 15 日 07:15 | `kr_ecos_exports` |
| S17 | `tw_ic_exports_monthly` | 每月 15 日 08:15 | `tw_mof_exports` |
| S18 | `dram_contract_price_monthly` | 每月 15 日 08:15 | `trendforce_dram` |
| S19 | `ramp_ai_index_p10d_probe` | 每 10 天 15:30（下次 10-24） | `ramp_ai_index` |
| S20 | `openrouter_rankings_p7d_ingest` | 每 7 天 16:00（下次 10-22） | `openrouter_rankings` |
| S21 | `frontier_ai_capability_model_discovery_p7d` | 每 7 天 15:20（下次 10-23） | `frontier_ai_capability` |
| S22 | `frontier_ai_capability_benchmark_probe_p7d` | 每 7 天 15:25 | `frontier_ai_capability` |
| S23 | `frontier_ai_capability_official_release_probe_p7d` | 每 7 天 15:27 | `frontier_ai_capability_official_lab` |
| S24 | `frontier_ai_capability_full_audit_p7d` | 每 7 天 15:35 | `frontier_ai_capability` |
| S25 | `frontier_ai_labs_revenue_p7d_refresh` | 每 7 天 15:50 | `sacra_public_company_profiles`、`tickertrends_public_research` |

**通过标准：**
1. 最近一个应跑窗口 `succeeded` 或 `no_change`；
2. `g0_ingest` 里"上游最新期（已入库）"——上游有新一期时已经收进来，隔离为 0 或隔离原因你能接受；
3. **血缘可查**：每次入库都有来源、批次与上游版本标识（`g0_ingest` 每行就是一个批次）；分析用到的数据版本能在投影里看到（`g0_show <投影编号>` 的"依赖的数据版本"）；
4. **数字对**（抽查）：`g0_company LRCX` 的共识 EPS / 营收均值、最新报告期营收与 EPS，和你在 Yahoo Finance、公司财报上看到的一致；`ats macro show` 里的 CPI、非农、利差等读数与官方发布一致；
5. **缺口已登记**：没有数据的标的（例如 SKHY、COHR、LITE、AVGO 没有财务包）在 P02 的"提醒"里列出，并且你知道原因。

**常见的"不通过但可接受"：**
- S12–S14 只覆盖 GOOG、MU、LRCX（10-16 的试点范围；ASML 指标不全、SKHY 币种未映射，见 `config/data/schedules.yaml` 注释）。LRCX 是 10-19 加入的，控制器**还没跑过一次**，首次在 10-24。
- S16/S17：上游没发新一期时是 `no_change`；但台湾出口 10-15 是 `parse_failed`，宏观检查已提示两者最新期 2026-08 过旧——需要你决定。
- S25：上游页面方法变化（`methodology_drift`）时会失败，等人工确认后再入库，属设计行为；是否接受由你定。
- S19：Ramp 上游 2026-08 之后没有新一期，`no_change` 正常。

---

## 3. Dataflow：派生（D01–D11）

**核验什么：** 从文档里抽出的产业链证据、财报与宏观日历、FactSet 月末数据是否按时生成，因为层次、行业分析读证据，所有事件工作流靠日历唤醒，宏观与行业读 FactSet。

### D01–D04 产业链证据 `chain_evidence_daily.{TSM,ASML,MU,SKHY}`（每天 06:00）

```bash
g0_refresh chain_evidence
ats evidence show --entity TSM --limit 15      # 换 ASML / MU / SKHY；只读表格：指标、类型、立场、方向、期间 + 原文片段
```

- **通过：** 该公司最近一次财报后，出现期间对得上的证据（例如 TSM 10-15 财报后有 `2026-10-15` 的读数）；抽读 3 条原文片段，确认指标与立场判断没有张冠李戴。
- **可接受：** `no_change / already_extracted`（已抽过）、`no_print_in_window`（近 7 天没有新财报，MU、SKHY 现在就是）。

### D05–D10 日历 `calendar_{finnhub_earnings,yahoo_earnings,fomc,bls,bea,finalize}_daily`（每天 14:10–15:10）

```bash
g0_refresh calendar_
g0_calendar                            # 各日历来源最近一次刷新、错误原因、待裁决冲突数
ats events upcoming --days 30          # 未来 30 天的财报与宏观事件（已是可读文本）
```

- **通过：** 五个来源最近一次都是 `complete`；`ats events upcoming` 里的日期和盘前/盘后与公司 IR 页面、美联储/BLS/BEA 官网一致（至少核对 LRCX 10-21 盘后、SKHY 10-27、GOOG/MSFT 10-28、FOMC 10-27/28、GDP/PCE 10-29）；D10（发布确认）每天 `succeeded`。
- **已知：** FOMC、BLS、BEA 三个来源 10-20 起 TLS 失败（自动证据）。宏观事件开关现在关着（E03/E04），所以短期影响的是日历显示，不是评审；是否接受由你定。冲突裁决见 E05。

### D11 FactSet 月末摄入 `factset_month_end_ingest`（每月 1 日 12:30，首次 11-01）

```bash
g0_refresh factset
g0_product macro          # 宏观检查会提示 FactSet 指数报告的日期
```

- **通过：** 11-01 那次 `succeeded`，宏观检查不再提示"FactSet 报告过旧"，`ats macro show` / `ats sector show ai_hardware` 引用的 FactSet 报告日期变成 10 月末那一期。
- **现在：** 从未运行；最近一份 FactSet 指数报告是 2026-09-25（10-02 入库）。宏观检查仍按"每周一份"判断时效（14 天），所以月末摄入之间会一直提示过旧——这是设计不一致，需要你决定（另见 M03）。

---

## 4. 数据产品（P01–P07）

**核验什么：** 每个分析师真正读到的那一包数据是否合格、范围是否正确。数据刷新成功不等于分析师读得到、读得对。

```bash
g0_product information      # 换 fundamental / layer / macro / sector / technical
g0_product_log             # 健康状态与读路由的变化记录
ats consumer-cutover status   # 六个分析师的读路由（route 应为 target）
```

`ats consumer-cutover status` 输出是 JSON，只需看每段的 `consumer_id` 和 `route` 两行。

| 编号 | 数据产品 | 读它的分析师 | 范围对不对：看什么 |
|---|---|---|---|
| P01 | 信息文档包 | information | 11 只 PEAD 标的（`config/pead.yaml`）都在检查范围内；"提醒"里的"某票 7 天内无准入文档"你能解释 |
| P02 | 公司研究包 | fundamental | 有财务的只有 GOOG、MU、LRCX（试点）+ 共识覆盖 11 只；"无财务行"的标的你知道原因 |
| P03 | 产业链数据包 | layer | 8 层都在；L1、L3 "无命题（配置缺口）"你是否接受 |
| P04 | 产业链数据包（行业横截面） | sector | 各层"缺财务""共识超 14 天"的名单你能接受；FactSet 行业分区不可用的原因 |
| P05 | 宏观数据包 | macro | 指标序列覆盖、宏观新闻时效、韩/台出口、FactSet 四项 |
| P06 | 实时市场包（运行时，不入库） | technical | 技术面标的都能拿到 4 天内的日线 |
| P07 | 读路由固定在新路径 | 全部六个 | `consumer-cutover status` 里六个都是 `route: target` |

**通过标准：** 六个分析师的检查结论都是 `ok`；"提醒"都在你能接受的范围；P07 六个都是 target。

**自动证据要点：** 10-21 10:4x 检查时六个都是 `ok`；但 P07 **不满足**：technical 的读路由是 `legacy`（10-20 09:36 被旧的自动切回机制切走，见 0.6 第 1 条）。这一项需要你决定怎么处理（交给修复会话，还是记为未通过）。

---

## 5. Workflow：定时（W01–W10）

**核验什么：** 每个研究工作流在时间表的时刻被唤醒、跑完、产出的内容对；数据没变时直接复用，不重复请求模型。

**通用命令：**

```bash
g0_triggers information-brief 10        # 某工作流最近的触发：定时/事件/手动、应触发时刻、状态、原因
g0_runs 30                              # 最近 30 次运行：任务、范围、状态、每个标的是新算还是复用、失败原因
g0_runs 60 technical-review             # 只看某个工作流
g0_runs 150 sector-review               # 周度工作流离现在远，要把条数放大才翻得到
g0_latest information_brief             # 每个标的最近一份产出的时间与摘要
g0_show d865de3edfa8                    # 打开一份产出的全文（把编号换成 g0_latest 输出里的 12 位编号）
```

**怎么读 `g0_runs`：** 状态 `complete`=全部完成；`incomplete`=有标的缺产出（下面 └ 行写了是谁、为什么）；`failed`=整次失败。"新算 N"是这次真的请求了模型的标的数，"复用 N"是直接用了上一份产出的标的数。

**看报告的位置：**

| 工作流 | 可读产出在哪 |
|---|---|
| 信息简报 | 不写 Obsidian。用 `g0_latest information_brief` → `g0_show` |
| 基本面例行 / 事件评审 | Obsidian：`~/Documents/Obsidian Vault/量化投资/个股分析系统/基本面分析-<代码>-<期>.md`（看文件修改时间是否对上运行时间）；`ats pead show <代码>`；`g0_latest fundamental_expectation_update <代码>` / `g0_latest fundamental_event_review` |
| 技术面 | 10-21 时新路径**不写** Obsidian（目录里 10-17 的"技术面读数"不是流水线产的）；RP 2.2 完成后应每次运行写一份汇总报告。另可用 `ats technical show`、`g0_latest technical_review` |
| 宏观 | Obsidian：同目录 `宏观分析-宏观-<日期>[-vN].md`；`ats macro show` |
| 行业 / 层次 | 10-21 时新路径**不写** Obsidian 报告和 HTML；RP 2.3 完成后应写行业报告、层级报告与可视化页面，RP 2.4 完成后写每周产业链证据周报。另可用 `ats sector show ai_hardware`、`g0_latest sector_allocation`、`g0_latest layer_analysis` |

| 编号 | 工作流（配置条目） | 时间表（台北） | 专项看什么 | 通过标准 |
|---|---|---|---|---|
| W01 | `information-brief`（research_schedules；event_routes `pead-daily`） | 工作日 20:00（纽约 08:00） | `g0_triggers information-brief`；`g0_runs 20 information-brief`；挑 3 只票 `g0_show` 读简报 | 最近一次定时触发 `complete`，11 只都有产出；简报里的事实、来源、时间你核对无误；没有买卖建议（角色禁止） |
| W02 | `fundamental-routine`（同上） | 工作日 21:00，依赖当天信息简报 | `g0_runs 20 fundamental-routine`；`ats pead show LRCX`；Obsidian 基本面文件 | 最近一次 `complete`；信息简报那一半显示"复用"（不重复跑）；预期更新的"确认/否定/新增"分类合理 |
| W03 | `technical-review`（event_routes `technical-daily`） | 工作日 22:00 | `g0_triggers technical-review`；`g0_runs 60 technical-review`；`ats technical show` | 最近一次定时触发 `complete`，每只都有读数；评分与均线位置和行情软件对得上 |
| W04 | `macro-review`（`macro-weekly`） | 周六 09:00 | `g0_triggers macro-review 100 \| grep schedule`（前面有几十条宏观事件的"等材料"记录，要过滤）；`g0_runs 150 macro-review`；`ats macro show`；Obsidian 宏观报告 | 10-24 那次 `complete`，有新报告文件（不覆盖旧文件）；regime、指标读数、证伪条件你认可 |
| W05 | `sector-review`（`sector-weekly`） | 周六 09:30 | `g0_triggers sector-review 5`；`g0_runs 150 sector-review`；`ats sector show ai_hardware` | 10-24 那次 `complete`；八层配置与理由你认可 |
| W06 | `layer-review`（`sector-weekly` 中由 Dispatcher 先补跑） | 随 W05 | `g0_triggers layer-review 5`；`g0_runs 150 layer-review`；`g0_latest layer_analysis` | 10-24 W05 之前 8 层都有新产出，或数据没变时显示复用；没有超配/低配结论（角色禁止） |
| W07 | 复用：数据没变不重复请求模型（ARCHITECTURE §8.2-5） | 跨工作流 | `g0_runs 40` 看"复用"计数；对照 OpenRouter 后台当天的请求量 | 同一天重复唤醒时（如补跑、事件+定时），第二次全是"复用"；没有找不到原因的重复"新算" |
| W08 | 调度负责人：7 个工作流都归 Dispatcher | — | `ats dispatch owner-log`（JSON，只看每个 `workflow` 的最后一条 `to_owner`） | 7 个工作流最后一条都是 `to_owner: dispatcher`，没有你不知道的切换 |
| W09 | 手动运行入口（event_routes `manual.allowed_task_ids`，7 个） | 手动 | `g0_triggers fundamental-event` 里 `manual` 行 | 白名单里的 7 个都能经 `ats workflow run` 跑；已有手动运行记录即可，**不必新跑** |

**💰 可选（会产生费用，非必需）：** 想亲手确认手动入口和复用时，可以跑
`ats workflow run --task macro-review --scope-kind portfolio --scope-id portfolio`。数据没变时应全部复用、不请求模型，但不能百分之百保证，所以列为可选。**不要**加 `--inputs-json '{"…": {"force_recompute": true}}'`，那一定会重新请求模型。

**常见的"不通过但可接受"：**
- 信息简报某只票 `incomplete`，多半是该票 7 天内没有准入资讯（数据缺口，不是逻辑缺陷）。
- 基本面例行某只票 `blocked`（例如 10-19 LRCX "financial input unavailable"）是数据产品缺口，被正确阻断。
- 机器睡眠导致晚跑，但在宽限内补跑成功（10-20 的信息简报晚了 3 小时 18 分钟，属于此类）。

**不可接受：** W03 目前的失败不属于以上情形（见 0.6 第 1 条）。

### W10 可读报告（**待 tasks.md 第 2 组 RP 完成后核验**）

**核验什么：** 技术面汇总报告、行业报告（含层级报告与可视化 HTML）、每周产业链证据周报由 Dispatcher 路径生成。旧手动入口退役后，这些报告只能靠新路径产出（inventory.md 第四节 Q1，10-21 裁决恢复）。

**什么时候看：** 第 2 组 RP 全部完成、`com.ats.dispatcher` 已重启之后：
- 下一个工作日 22:00 技术面定时运行之后，看技术面报告；W03 的读路由问题要先解决，否则技术面没有新读数，也不会有报告。
- 下一个周六 09:30 行业评审之后，看另外三类。

**怎么看：**

```bash
ls -lt "$HOME/Documents/Obsidian Vault/量化投资/个股分析系统" | head -20      # 按修改时间列出最新的报告文件
ats technical show                                                           # 与技术面报告对照
ats sector show ai_hardware                                                  # 与行业、层级报告对照
```

**通过标准：**
1. 文件修改时间落在对应定时运行之后，每次运行一份；新版本是新文件，不覆盖旧文件。
2. 技术面是一份汇总所有标的的报告，不是每只票各一份。
3. 报告内容与同一次运行的产出一致（读数、配置、证据结论对得上 `ats technical show` / `ats sector show`）。
4. 数据没变、运行全部复用时，不重复生成报告。

**常见的"不通过但可接受"：** 无。RP 未完成就要签 G0 时，本项记"未通过"或"接受缺口"，并写明哪类报告缺失。

---

## 6. Workflow：事件（E01–E05）

**核验什么：** 财报日历能在正确的时点唤醒正确的工作流：财报前建基线，财报后冻结基线、打分。宏观事件现在关着，要确认它确实不触发。

**通用命令：**

```bash
g0_event LRCX                          # LRCX 财报事件：日期、钟点、状态（planned/released）、已准入的材料
g0_triggers fundamental-event 10       # 事件评审的触发：event 行就是日历唤醒
g0_triggers information-brief 20       # 事件也会唤醒信息简报
g0_triggers macro-review 5             # 宏观：确认没有 event 行在执行
ats pead show LRCX                     # 财报前基线（phase=prep）与财报后打分
```

| 编号 | 配置条目 | 通过标准 | 常见的"不通过但可接受" |
|---|---|---|---|
| E01 | event_routes `earnings / plan` → 信息简报 + 基本面例行（财报前 14 天内；例行模式在财报前 3 天内建基线）；research_schedules `event_triggers.earnings: true` | `g0_triggers fundamental-routine` 里有 `event earnings:<代码>:…` 且 `complete`；`ats pead show <代码>` 显示 `phase=prep` 的基线，叙事、估值、关键 KPI 你认可 | — |
| E02 | event_routes `earnings / release` → 信息简报 + 基本面事件评审（需已准入公告或申报） | **一次真实财报端到端**（首选 LRCX 10-21 盘后）：① `g0_event LRCX` 状态变为 `released`，准入材料含 `filing` 或 `company_release`；② `g0_triggers fundamental-event` 出现 `event earnings:LRCX:FY2027Q1:release … complete`；③ `g0_latest fundamental_event_review LRCX` → `g0_show`：基线是财报前冻结的那份（`baseline_as_of` 早于发布时刻）、三类差异（相对基线、相对共识、相对市场隐含预期）都有、Scorecard 打分与你对财报的理解一致；④ Obsidian `基本面分析-LRCX-2027Q1.md` 更新 | 电话会稿迟到时，后来会再出一个新版本，不覆盖早期版本——这是设计行为 |
| E03 | event_routes `fomc / release` → 宏观评审；`event_triggers.macro_sector: false` | 开关关着：`g0_triggers macro-review` 里**没有** `event` 行变成 `complete`，宏观只在周六跑 | 有 `waiting_material` 的 event 行不会执行（执行前会看开关），但它们本不该写——见 0.6 第 3 条，由你决定接受还是登记缺陷 |
| E04 | event_routes `cpi / pce / nfp / gdp` → 宏观评审（同一开关） | 同 E03 | 同 E03 |
| E05 | 日历来源冲突与人工裁决 | `ats events candidates --status conflict` 列出的每一条你都看过：要么用 `ats events override` 裁决（⛔ 写操作，裁决属于日常运维，不在本手册内执行），要么确认它离财报日还远、可以等 | 两个来源只差精度（一方多给钟点）会自动合并，不算冲突 |

**E02 的时间线（台北）：** 10-22 04:00 LRCX 发布 → 10-22 15:10 发布确认（需要 LRCX 的财报稿或申报已准入）→ 10-23 02:10 Dispatcher 消费 → 当晚完成。10-23 白天用上面四步核对。

**E02 很可能卡住，请提前知道（自动证据）：** 日历上没有任何事件到过 `released`。ASML（10-14）、TSM（10-15）两次真实财报都停在 `waiting_material / release_material_not_admitted`，10-19 的事件评审是手动跑的。"财报材料"需要准入的申报或财报稿，SEC 正文任务未启用（U06）。

如果 10-23 白天 `g0_event LRCX` 仍是 planned、`g0_triggers fundamental-event` 只有 `waiting_material`：E02 记"未通过"，原因写"缺财报材料来源"；或者等 SKHY（10-27）、GOOG/MSFT（10-28）再看一次。手动补跑一次事件评审（💰 会产生费用）只能证明评审逻辑，证明不了"日历自动唤醒"，不能代替 E02。

---

## 7. 运行保障（A01–A07）

**核验什么：** 出问题时系统会发现、会告警、会挡住受影响的任务、重启后能接着跑，这样退役后再出问题也看得见。

| 编号 | 对象 | 怎么看 | 通过标准 | 备注 |
|---|---|---|---|---|
| A01 | 三个服务在跑 | `launchctl list \| grep com.ats`；`curl -s -m5 http://127.0.0.1:8000/health` | dispatcher、serve 的 PID 列是数字；data-refresh 的 PID 是 `-`、状态 `0` 正常（它每小时被唤醒一次，不常驻）；health 回 `{"ok":true}` | ⛔ 不要 `bootstrap/bootout/kickstart` |
| A02 | 发布时检查（每次数据刷新跑完后） | `g0_product_log`；`grep consumer_health var/logs/data-refresh.out.log \| tail` | 有状态变化时日志里有一行，同一状态不重复记 | 29c6b4d（10-20 15:05）之后一直没有状态变化，所以生产上**还没有一条新机制的记录** |
| A03 | 读取前检查与阻断 | `g0_runs 60` 找 `blocked` 和 `data_product_unhealthy`；隔离环境：`python -m pytest tests/test_phase_e_dispatcher.py -q -p no:cacheprovider -k unhealthy` | 数据产品不合格时，该任务记 `blocked`、原因 `data_product_unhealthy`，整次运行 `incomplete`，别的任务照常 | 测试在临时目录里造一次"不合格"，不碰生产库、不请求模型，约 10 秒 |
| A04 | 飞书告警 | 飞书群历史里找"数据产品健康检查未通过 / 已恢复"；隔离环境：`python -m pytest tests/test_consumer_health.py -q -p no:cacheprovider` | 由好转坏、由坏转好各发一次，不刷屏 | 新机制在生产上还没触发过；测试约 30 秒 |
| A05 | `com.ats.dispatcher` 重启后加载新代码、启动补跑 | `ps -o lstart= -p $(launchctl list \| awk '/com.ats.dispatcher/{print $1}')` 与 `git log -1 --format=%ci -- src/`；`g0_triggers information-brief 5` | 进程启动时间晚于最后一次改 `src/` 的提交；睡眠错过的定时在宽限内补跑一次（不重复） | 不要为核验去重启；以 10-20 16:10 那次重启和当晚补跑为证据 |
| A06 | 数据刷新控制器每小时运行与补跑 | `g0_refresh` 第一行"控制器最近一次定时运行" | Mac 醒着时，离现在不超过 1 小时出头；错过 24 小时以上的窗口记 `misfire` 跳过 | 可以看但不要运行 `python -m ats.data.refresh tick --dry-run`：它只按时间判断"到点"，不查账本，已经跑过的窗口也会显示 `due`，容易误读 |
| A07 | 日志里没有未处理的异常 | `grep -n 'ERROR\|Traceback' var/logs/dispatcher.err.log \| tail -20`；`tail -5 var/logs/data-refresh.err.log` | 每条 ERROR 都能对应到已知问题 | 10-20 23:29 有一条 technical-review 的 `RouteUnavailable`（见 0.6 第 1 条） |

⛔ 不要为核验 A02–A04 去改生产数据、手动造不合格，design D1 写的"人为制造一次不合格"请用上面的隔离测试代替。

---

## 8. 运维文档（M01–M04）

**核验什么：** 退役后只剩新路径。如果文档写错，出问题时你会照着错的文档操作。

**做法：** 照着文档完成一次"日常检查"（OPERATIONS §2 的 7 条命令都是只读的）。逐条记下"照写能不能跑、输出和文档说的是否一致"。

| 编号 | 文档 | 重点看 | 自动证据发现的不一致（供你核对） |
|---|---|---|---|
| M01 | `docs/OPERATIONS.md` | §1 服务表、§2 每天看什么、§3 时间表与手动运行、§4 数据产品健康、§5 数据刷新、§6 事件日历、§9 已知问题 | §2/§4 说六个读路由"固定为 target"，technical 实际是 legacy；§2 的 `ats events candidates` 不加 `--status conflict` 会列出全部已准入候选；§9 的 partial 缺陷只写了 ibkr_news，TrendForce、SemiAnalysis 也受影响 |
| M02 | `docs/ARCHITECTURE.md` §2–§4、§6–§9、§13 | §6 实施状态与现实是否一致；§8.2 复用规则；§13 日历 | §6 "6 个分析师读新路径"——technical 不是；§6 写"日历事件唤醒"，但事件评审尚未自动走通过 |
| M03 | `docs/agents/*.md`（7 份角色文档） | 每份的"怎么运行""产出在哪"是否已改成 Dispatcher + `ats workflow run` | `MACRO_ANALYST.md`（约 497 行）与 `docs/data/FACTSET_EARNINGS_INSIGHT.md`（约 57 行）仍写"周六 08:10 factset_weekly_ingest、08:50 weekly_review"，二者都已下线；`SECTOR_ANALYST.md` 仍以 `ats sector review`（旧入口）写 Obsidian 报告为主要用法 |
| M04 | `README.md` | 给新读者的整体说明是否和现在一致 | 约 204 行写"各分析师的报告存成 Obsidian 笔记"，但新路径只有宏观和基本面写 |

**通过标准：** 日常检查照文档能做完；发现的不一致都已登记为文档修订项（R6 收尾会改），或你确认不影响使用。

---

## 9. 旧入口副作用对照确认（L01）

**核验什么：** R1 会删掉研究分析师的旧手动命令。旧命令里可能做了新路径没做的事（写 Obsidian 报告、推飞书、写库），删了就悄悄没了。

**看哪里：** [`inventory.md`](inventory.md) 第四节"旧手动入口的副作用对照"（a1c89e3）和第五节的问题。你在 10-21 已裁决（ede7412）：

- **Q1 可读报告**：技术面、行业（含层级与 HTML）、每周产业链证据周报**要恢复**，由 tasks.md 第 2 组 RP 在 G0 之前完成；宏观周报已由新路径生成。
- **Q3 命题治理命令**：`ats evidence claims/report/propose/proposals/review/kbreview`、`ats sector kbperturb` **保留**，不在 R1 删除。

**怎么核对：** 逐行读第四节。"已承担"的行，挑一两条对照新路径的真实产出（§5 的报告位置表）。恢复的三类报告单独在 **W10** 核验。保留的命令跑一次 `--help` 确认还在（例如 `ats evidence --help`）。

**通过标准：** 第四节每一行要么是"已承担"，要么已按裁决处理：恢复的报告看 W10 是否通过；保留的命令仍可用。

> 若你核验时 inventory.md 仍在修改，以你核验当时提交的版本为准，在 verification.md 备注里写上该版本的提交号。

---

## 附：配置条目与核验项对照（覆盖检查）

| 配置 | 条目 | 核验项 |
|---|---|---|
| `config/data/schedules.yaml`（44 个任务） | 非结构化 8 / 结构化 25 / 派生 11 | U01–U08 / S01–S25 / D01–D11 |
| `config/workflow/research_schedules.yaml` | `event_triggers.earnings` | E01、E02 |
|  | `event_triggers.macro_sector` | E03、E04 |
|  | `workflows.information-brief` / `fundamental-routine` / `technical-review` / `macro-review` / `sector-review` | W01 / W02 / W03 / W04 / W05 |
| `config/workflow/event_routes.yaml` | `manual.allowed_task_ids`（7 个） | W09 |
|  | `scheduled.pead-daily` | W01、W02 |
|  | `scheduled.technical-daily` | W03 |
|  | `scheduled.macro-weekly` | W04 |
|  | `scheduled.sector-weekly` | W05、W06 |
|  | `events[0]` earnings/plan | E01 |
|  | `events[1]` earnings/release | E02 |
|  | `events[2]` fomc/release | E03 |
|  | `events[3]` cpi/pce/nfp/gdp release | E04 |

另有 W10 可读报告（技术面汇总、行业含层级与 HTML、每周产业链证据周报），对应 tasks.md 第 2 组 RP，不来自上面的配置条目。

核验项总数：U 8 + S 25 + D 11 + P 7 + W 10 + E 5 + A 7 + M 4 + L 1 = **78**。
