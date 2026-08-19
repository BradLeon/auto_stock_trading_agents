# 数据源状态（Data Sources）

PEAD 基本面分析 + 交易 Agent 的数据源清单：已接入并测试通过 vs 待接入。
最后更新：2026-08-19（+ 共享数据平台首期能力）。

## 如何测试

```bash
PYTHONPATH=src .venv/bin/python scripts/check_data.py            # 全部（不含 LLM）
PYTHONPATH=src .venv/bin/python scripts/check_data.py news COHR  # 单个：<源> <标的>

# 两个通道的 LLM 加工产物（按名单独跑，不进全量、避免误计费）：
PYTHONPATH=src .venv/bin/python scripts/check_data.py triage COHR    # ① 新闻分诊：每条打分 + 保留/丢弃
PYTHONPATH=src .venv/bin/python scripts/check_data.py insights COHR  # ② newsletter 提取的 per-ticker insight
# research 走 QQ 邮箱（Gmail 过滤器自动转发）；手动转发的测试邮件 From 是你自己（非原始
# 发件人），需用 ATS_TEST_SENDER 覆盖发件人过滤来验证整条链路：
ATS_TEST_SENDER=你的Gmail@gmail.com PYTHONPATH=src .venv/bin/python scripts/check_data.py insights COHR
```
每个源的**完整结果**落到 `var/data_dumps/<源>_<SYM>.json`（纪要为 `.txt`），可直接打开查看。
⚠️ 逐个测（yfinance 连续猛打会被限流，出现 "possibly delisted" 假错）。

---

## ✅ 已接入并测试通过

| 源 | provider / key | 提供的数据 | 存储 | 备注 |
|---|---|---|---|---|
| **market** 行情 | yfinance（无需 key） | 日线 OHLCV(1y) + SMA/RSI/MACD/ATR 等 9 指标 | run 时现取 → 技术面分析师（`agents/technical`，用日线收盘算 7 点评分） | — |
| **fundamentals** 基本面 | yfinance + SEC EDGAR（`SEC_EDGAR_USER_AGENT`） | 估值比率 + **三大报表科目**（营收/毛利率/营业利润率/净利/EPS/CapEx/FCF/折旧/负债）含 **QoQ+YoY** + 近期 SEC filing 链接 | → `pead_dossier` | 报表来自 yfinance 季度表 |
| **macro** 宏观 | FRED（`FRED_API_KEY`）+ yfinance + CNN | UST10Y/2Y、Fed Funds、CPI YoY、失业率、非农、VIX、SPX/NDX、**Fear&Greed** | → 宏观分析师 | F&G 用完整浏览器 UA 绕过 418 |
| **earnings** 财报日历 | Finnhub（`FINNHUB_API_KEY`）→ yfinance 兜底 | 下次财报日 + **盘前/盘后(amc/bmo)** + EPS/营收预估 | → 调度 + 期权到期选择 | 券商级（聚合 IR 公告），动态无人工 |
| **consensus** 一致预期 | yfinance（无需 key） | 当季 EPS / 营收 一致预期（含 low/high）+ **分析师目标价**(mean/median/low/high/current) + **评级分布**(SB/B/H/S/SS 含近 4 月趋势) + **近 120 天升降级**(机构/评级/动作，最多 8 条) | → `pead_dossier.expectation_set` + prep 叙事/预期上下文 | Finnhub earnings 带预估、`/stock/recommendation`（免费）带评级，可交叉验证；Finnhub 目标价为付费端点 |
| **runup** 抢跑/距高 | yfinance（无需 key） | 财报前 20 日相对 SMH/QQQ 超额收益、距 52w 高 | → `pead_dossier.market_setup` | 透支判断 |
| **options** 期权 | **ThetaData 本地终端** → yfinance 兜底 | Expected Move、ATM IV、25Δ skew（BS 反解） | → `pead_dossier.market_setup` | ⚠️ 终端开着才准（IV≈真值）；终端没开走 yfinance 时 IV 退化，建议跑财报时开 `./scripts/start_thetadata.sh` |
| **news** 新闻 | Finnhub（`FINNHUB_API_KEY`）+ 策选 RSS | 标的 + 信号链公司新闻（标题/摘要/链接/时间），去重 + **LLM 分诊降噪**（Gemini Flash 打 materiality 分，噪音不进 LLM 上下文）+ **关键新闻正文增强**（≥0.65 分抓全文喂 monitor） | 全部标题/摘要 → 共享文档资产；高价值全文 → 同一文档的新版本；事件状态 → `pead_events` | 相同 URL 跨 Provider/标的只保存和抓取一次；X/社媒见待接入 |
| **research** 订阅研报 | Gmail IMAP（`GMAIL_ADDRESS`+`GMAIL_APP_PASSWORD`）+ Substack RSS | 高质量 newsletter 全文（SemiAnalysis 等，付费文只有邮件里全）→ LLM 提取 per-ticker insight（**含二阶传导**：如 Meta 出租算力→利空 MU/TSM），universe = targets+signal_chain | 共享原文 → `source_documents`/`document_versions`；PEAD 兼容结果 → `research_articles`/`research_insights` + `task_projections`；material insight 注入 `pead_events` | `ats pead research`；PEAD 与 Evidence 共用一次采集结果，各自记录处理版本。⚠️ Gmail 直连 993 被机场墙 → 走 **Gmail 过滤器自动转发到 QQ 邮箱**（`imap.qq.com` 直连） |
| **transcript** 电话会纪要 | defeatbeta / Tavily / 手工文件 / FMP | 财报电话会全文 | 质量与报告期校验通过后 → 共享文档资产 → PEAD/Evidence 共用 | FMP 需付费层；也可 `--transcript <链接/路径>`；第二个 Workflow 不再重新抓取 |
| **documents** 官方文档 | SEC 8-K Ex99.1 + Tavily + 本地文件夹 | **财报新闻稿**、SEC/手工公告、10-K/10-Q/6-K、**投资者 PPT** | 共享文档资产 → score / Evidence | 文件夹 `信息源/<SYM>/` 有则优先；按财报期复用，不重复访问 SEC/Tavily |
| **industry** 行业知识 | 本地 Obsidian 笔记（`industry_notes.root`，策选白名单 md） | 稳定的**行业/产业链背景**（AI 硬件供应链分层框架、利润分布、周期护城河、AI Capex、L4-L6 估值）——判断标的**定位/护城河/周期/议价权** | → **prep 建 thesis** 时注入 narrative，经 `prior_narrative` 闭环传播到 monitor/score | 文件夹直读（复用 documents `_read_doc`）；每篇截断 12k；root 缺失静默跳过。**结构性背景**非实时报价，动态景气仍靠 news/research |

**已验证（COHR 实测 2026-07-03）**：market(251 bar)、fundamentals(P/E 159 + 三表/CapEx/FCF/margins + 5 filings)、macro(F&G=32 / VIX 16 / UST10Y 4.48)、earnings(2026-08-11 amc, epsEst 1.65)、consensus(EPS 1.62 / PT 230~384~465 / 评级 4/13/4/0/0 / 升降级 8 条)、runup(vsSMH -13%)、options(yfinance 兜底 EM 31%/IV 107%；ThetaData 终端未开)、news(51 条)、**triage(51→保留15/丢弃36)**、**insights(SemiAnalysis EMIB-T 一文→5 条 per-ticker insight，经 `ATS_TEST_SENDER` 实测)**、transcript(Tavily 69K字)、documents(SEC 34K + deck 15K)、**industry(5 篇/53K字，prep 叙事已用上"L3 分层/InP 垂直整合护城河"等合集概念)**。research 数据层链路已通、待真实自动转发邮件。

**处理层模型路由**（成本优化，`config/settings.yaml` llm.routing）：
- **Gemini 2.5 Flash**（便宜高频/纯抽取）：`news_triage`（新闻分诊）、`context_monitor`（monitor 折新闻进 thesis）、`actuals_extract`（财报实际值抽取）
- **Opus 4.8**（真金白银的判断，低频）：`manager`（日常调仓）、prep 定调（叙事/预期）、`pead-scorer`（打分驱动下单）、`research_extract`（二阶传导推理是核心价值）

---

## ⬜ 待接入 / 待测试

| 源 | 现状 | 增量价值 | 优先级 |
|---|---|---|---|
| **SEC XBRL Company Facts** | 未接（已验证可用：665 概念/全历史） | 结构化数字的**权威 as-reported + 超长历史**，防 yfinance 偶发错值；可替掉 yfinance 当权威层 | 🟡 中（有①够用，长期上） |
| **行业景气 / 产业链定量** | **定性已接**（industry 源：Obsidian 合集注入 prep）；定量仍缺 | 渠道检查、价格、产能利用率等分部链路**定量**数据（定性背景已有） | 🟡 中（只剩定量） |
| **X / 社媒**（Trump/Musk/Huang…） | 仅 stub（X API 受限/付费） | 重点账号实时信号 | 🟡 中（需选方案/付费） |
| **options IV（yfinance 兜底）改 BS 反解** | 兜底 IV 退化（≈0.2%） | 终端没开时也能拿到像样 IV/skew | 🟡 中（小改动） |
| **Reddit 情绪** | 未实现（`.env` 有 key 槽） | 散户情绪 | 🟢 低 |
| **内部人 / 机构 13F / 做空比例** | 未实现 | 持仓/做空结构 | 🟢 低 |
| **Day1-2 财报后漂移跟踪** | 未实现 | 记录财报后实际股价反应，校准 Scorecard 阈值 | 🟢 低（决策不依赖） |
| **Bloomberg/Reuters/The Information** | **评估后不接** | 头条 Finnhub 已聚合；深度靠 SemiAnalysis newsletter；非 HFT 不需秒级首发；Terminal ~$25k/年无廉价 API | ❌ 不接（如有付费邮件订阅，加发件人到 `newsletters.imap.senders` 走 channel-2 即可） |
| **Aiera MCP（纪要）** | 环境挂载但未接 | 近实时纪要（替代 Tavily 抓取） | 🟢 低（需鉴权，headless 不稳） |
| **实时音频转写** | 评估后放弃 | 会中实时纪要 | ❌ 不做（产品级工程、收益小） |

---

## 存储机制

- **共享文档资产**：正文保存在 `信息源/`（或 `ATS_DOCS_ROOT`）下；`source_documents` 保存逻辑文档目录，`document_versions` 保存不可变版本，`document_entities` 记录同一文档关联的多个公司，`document_chunks` 提供文本检索，`document_processing_runs` 区分各 Workflow 的处理状态和版本。所有业务来源统一经 `data.document_assets` 写入。
- **结构化观测**：`chain.sources` 已接入的数据写入 `measurement_series`/`measurement_points`，同一期间的上游修订并存，并可按 `as_of` 回放；同比、环比等派生值查询时计算。既有行情、基本面、宏观、期权和 consensus 仍是运行时现取，尚未整体迁移。
- **事实与任务解释**：`evidence_facts` 保存中性事实，`evidence_fact_projections` 保存 Evidence 的命题解释，`task_projections` 保存 PEAD / Sector 等带版本的任务视图。`evidence_observations`、`research_insights` 等旧表在兼容期继续双写。
- **Context Memory `var/ats.sqlite`**：除上述数据平台目录外，继续保存 `pead_dossier`、`pead_events`、`reports`/`decisions`/`trades`/`performance` 等领域状态和决策记忆。
- **新闻→决策闭环**：dossier 的 `narrative` 是唯一累积记忆——monitor 持续把分诊后的新闻 + 结构化维度变更折进它，prep 在财报前**读取并延续**（而非重置为种子），score 据此对基准打分。所以两条通道的产出能一路走到 Scorecard/下单，不会被 prep 冲掉。
- **`var/checkpoints.sqlite`**：LangGraph 暂停态（异步飞书审批跨进程 resume）。
- **`var/transcripts/<SYM>_<fiscal>.txt`**：手动落档纪要；**`信息源/<SYM>/`**（`docs_root`）：官方 PDF；**`半导体产业研究合集/`**（`industry_notes.root`）：行业知识 md（prep 注入，不落库）。
- **尚未迁移的原始源**：行情/基本面/宏观/期权/consensus 仍每次 run 现取，分析产出落 dossier；`var/data_dumps/` 仅供人工查验。这是当前迁移边界，不再作为长期数据原则。
- 查存储：`ats data health`、`ats data search "inference demand" --entity AMD`、`ats data series --source <source_id>`、`ats data company AMD`、`ats data claim <concept>`、`ats data lineage <projection_id>`。

## key 一览（`.env`）

必填：`OPENAI_API_KEY`(OpenRouter)。已配：`FRED_API_KEY`、`FINNHUB_API_KEY`、`TAVILY_API_KEY`、`SEC_EDGAR_USER_AGENT`、`FMP_API_KEY`(付费纪要才用)、`FEISHU_BOT_WEBHOOK`+`FEISHU_APPROVE_*`。newsletter IMAP：`GMAIL_ADDRESS`+`GMAIL_APP_PASSWORD`+`GMAIL_IMAP_HOST`——**实际指向 QQ 邮箱**（`imap.qq.com`，Gmail 直连 993 被墙，用 Gmail 过滤器把 SemiAnalysis 等发件人自动转发到 QQ，QQ 授权码作密码）；`GMAIL_PROXY` 可选（走本地代理连 Gmail 时用，QQ 直连不需要）。本地服务：ThetaData 终端（期权）。
