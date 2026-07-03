# 数据源状态（Data Sources）

PEAD 基本面分析 + 交易 Agent 的数据源清单：已接入并测试通过 vs 待接入。
最后更新：2026-07-03。

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
| **market** 行情 | yfinance（无需 key） | 日线 OHLCV(1y) + SMA/RSI/MACD/ATR 等 9 指标 | run 时现取 → 技术面分析师 | — |
| **fundamentals** 基本面 | yfinance + SEC EDGAR（`SEC_EDGAR_USER_AGENT`） | 估值比率 + **三大报表科目**（营收/毛利率/营业利润率/净利/EPS/CapEx/FCF/折旧/负债）含 **QoQ+YoY** + 近期 SEC filing 链接 | → `pead_dossier` | 报表来自 yfinance 季度表 |
| **macro** 宏观 | FRED（`FRED_API_KEY`）+ yfinance + CNN | UST10Y/2Y、Fed Funds、CPI YoY、失业率、非农、VIX、SPX/NDX、**Fear&Greed** | → 宏观分析师 | F&G 用完整浏览器 UA 绕过 418 |
| **earnings** 财报日历 | Finnhub（`FINNHUB_API_KEY`）→ yfinance 兜底 | 下次财报日 + **盘前/盘后(amc/bmo)** + EPS/营收预估 | → 调度 + 期权到期选择 | 券商级（聚合 IR 公告），动态无人工 |
| **consensus** 一致预期 | yfinance（无需 key） | 当季 EPS / 营收 一致预期（含 low/high）+ **分析师目标价**(mean/median/low/high/current) + **评级分布**(SB/B/H/S/SS 含近 4 月趋势) + **近 120 天升降级**(机构/评级/动作，最多 8 条) | → `pead_dossier.expectation_set` + prep 叙事/预期上下文 | Finnhub earnings 带预估、`/stock/recommendation`（免费）带评级，可交叉验证；Finnhub 目标价为付费端点 |
| **runup** 抢跑/距高 | yfinance（无需 key） | 财报前 20 日相对 SMH/QQQ 超额收益、距 52w 高 | → `pead_dossier.market_setup` | 透支判断 |
| **options** 期权 | **ThetaData 本地终端** → yfinance 兜底 | Expected Move、ATM IV、25Δ skew（BS 反解） | → `pead_dossier.market_setup` | ⚠️ 终端开着才准（IV≈真值）；终端没开走 yfinance 时 IV 退化，建议跑财报时开 `./scripts/start_thetadata.sh` |
| **news** 新闻 | Finnhub（`FINNHUB_API_KEY`）+ 策选 RSS | 标的 + 信号链公司新闻（标题/摘要/链接/时间），去重 + **LLM 分诊降噪**（Gemini Flash 打 materiality 分，噪音不进 LLM 上下文）+ **关键新闻正文增强**（≥0.65 分抓全文喂 monitor） | → `pead_events`（含 triage_score/category） | 连续监控用；X/社媒见待接入 |
| **research** 订阅研报 | Gmail IMAP（`GMAIL_ADDRESS`+`GMAIL_APP_PASSWORD`）+ Substack RSS | 高质量 newsletter 全文（SemiAnalysis 等，付费文只有邮件里全）→ LLM 提取 per-ticker insight（**含二阶传导**：如 Meta 出租算力→利空 MU/TSM），universe = targets+signal_chain | → `research_articles`/`research_insights`；material insight 注入 `pead_events` 流入 dossier | `ats pead research`；高置信推飞书。⚠️ Gmail 直连 993 被机场墙 → 走 **Gmail 过滤器自动转发到 QQ 邮箱**（`imap.qq.com` 直连），代码链路已实测通过、待真实自动转发邮件累积 |
| **transcript** 电话会纪要 | Tavily（`TAVILY_API_KEY`）→ 手动落档兜底 | 财报电话会全文（搜 fool/investing 抓正文） | → `var/transcripts/` / dossier.actuals | FMP 也支持但需付费层（免费 402）；也可 `--transcript <链接/路径>` |
| **documents** 官方文档 | SEC 8-K Ex99.1 + Tavily + 本地文件夹 | **财报新闻稿**（SEC，权威自动）+ **投资者 PPT**（Tavily，通用自动）+ 文件夹精选 | → score 的 actuals 抽取 | 文件夹 `信息源/<SYM>/` 有则优先用、自动补缺、不重复 |

**已验证（COHR 实测 2026-07-03）**：market(251 bar)、fundamentals(P/E 159 + 三表/CapEx/FCF/margins + 5 filings)、macro(F&G=32 / VIX 16 / UST10Y 4.48)、earnings(2026-08-11 amc, epsEst 1.65)、consensus(EPS 1.62 / PT 230~384~465 / 评级 4/13/4/0/0 / 升降级 8 条)、runup(vsSMH -13%)、options(yfinance 兜底 EM 31%/IV 107%；ThetaData 终端未开)、news(51 条)、**triage(51→保留15/丢弃36)**、**insights(SemiAnalysis EMIB-T 一文→5 条 per-ticker insight，经 `ATS_TEST_SENDER` 实测)**、transcript(Tavily 69K字)、documents(SEC 34K + deck 15K)。research 数据层链路已通、待真实自动转发邮件。

**处理层模型路由**（成本优化，`config/settings.yaml` llm.routing）：
- **Gemini 2.5 Flash**（便宜高频/纯抽取）：`news_triage`（新闻分诊）、`context_monitor`（monitor 折新闻进 thesis）、`actuals_extract`（财报实际值抽取）
- **Opus 4.8**（真金白银的判断，低频）：`manager`（日常调仓）、prep 定调（叙事/预期）、`pead-scorer`（打分驱动下单）、`research_extract`（二阶传导推理是核心价值）

---

## ⬜ 待接入 / 待测试

| 源 | 现状 | 增量价值 | 优先级 |
|---|---|---|---|
| **SEC XBRL Company Facts** | 未接（已验证可用：665 概念/全历史） | 结构化数字的**权威 as-reported + 超长历史**，防 yfinance 偶发错值；可替掉 yfinance 当权威层 | 🟡 中（有①够用，长期上） |
| **行业景气 / 产业链定量** | 无（行业分析师靠通用知识） | 渠道检查、价格、产能利用率等分部链路定量 | 🟡 中 |
| **X / 社媒**（Trump/Musk/Huang…） | 仅 stub（X API 受限/付费） | 重点账号实时信号 | 🟡 中（需选方案/付费） |
| **options IV（yfinance 兜底）改 BS 反解** | 兜底 IV 退化（≈0.2%） | 终端没开时也能拿到像样 IV/skew | 🟡 中（小改动） |
| **Reddit 情绪** | 未实现（`.env` 有 key 槽） | 散户情绪 | 🟢 低 |
| **内部人 / 机构 13F / 做空比例** | 未实现 | 持仓/做空结构 | 🟢 低 |
| **Day1-2 财报后漂移跟踪** | 未实现 | 记录财报后实际股价反应，校准 Scorecard 阈值 | 🟢 低（决策不依赖） |
| **Bloomberg/Reuters 高级新闻** | 用 Finnhub/RSS 替代 | 更全/更快的财经新闻 | 🟢 低（成本高） |
| **Aiera MCP（纪要）** | 环境挂载但未接 | 近实时纪要（替代 Tavily 抓取） | 🟢 低（需鉴权，headless 不稳） |
| **实时音频转写** | 评估后放弃 | 会中实时纪要 | ❌ 不做（产品级工程、收益小） |

---

## 存储机制

- **Context Memory `var/ats.sqlite`**：`pead_dossier`（PEAD 活体档案：叙事/预期/期权/抢跑/信号链/实际/Scorecard/决策）、`pead_events`（新闻去重日志 + triage_score/triage_category 分诊结果）、`research_articles`/`research_insights`（newsletter 元数据 + 提取的 insight）、`reports`/`decisions`/`trades`/`performance`（日常组合循环）。
- **新闻→决策闭环**：dossier 的 `narrative` 是唯一累积记忆——monitor 持续把分诊后的新闻 + 结构化维度变更折进它，prep 在财报前**读取并延续**（而非重置为种子），score 据此对基准打分。所以两条通道的产出能一路走到 Scorecard/下单，不会被 prep 冲掉。
- **`var/checkpoints.sqlite`**：LangGraph 暂停态（异步飞书审批跨进程 resume）。
- **`var/transcripts/<SYM>_<fiscal>.txt`**：手动落档纪要；**`信息源/<SYM>/`**（`docs_root`）：官方 PDF。
- **原始行情/基本面/宏观/期权/consensus 不单独落库**——每次 run 现取，分析产出落 dossier；`var/data_dumps/` 仅供人工查验。
- 查存储：`ats pead show <SYM>` / `sqlite3 var/ats.sqlite ".tables"`。

## key 一览（`.env`）

必填：`OPENAI_API_KEY`(OpenRouter)。已配：`FRED_API_KEY`、`FINNHUB_API_KEY`、`TAVILY_API_KEY`、`SEC_EDGAR_USER_AGENT`、`FMP_API_KEY`(付费纪要才用)、`FEISHU_BOT_WEBHOOK`+`FEISHU_APPROVE_*`。newsletter IMAP：`GMAIL_ADDRESS`+`GMAIL_APP_PASSWORD`+`GMAIL_IMAP_HOST`——**实际指向 QQ 邮箱**（`imap.qq.com`，Gmail 直连 993 被墙，用 Gmail 过滤器把 SemiAnalysis 等发件人自动转发到 QQ，QQ 授权码作密码）；`GMAIL_PROXY` 可选（走本地代理连 Gmail 时用，QQ 直连不需要）。本地服务：ThetaData 终端（期权）。
