# 数据源状态（Data Sources）

PEAD 基本面分析 + 交易 Agent 的数据源清单：已接入并测试通过 vs 待接入。
最后更新：2026-06-17。

## 如何测试

```bash
PYTHONPATH=src .venv/bin/python scripts/check_data.py            # 全部
PYTHONPATH=src .venv/bin/python scripts/check_data.py news COHR  # 单个：<源> <标的>
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
| **consensus** 一致预期 | yfinance（无需 key） | 当季 EPS / 营收 一致预期（含 low/high） | → `pead_dossier.expectation_set` | Finnhub earnings 也带预估，可作交叉验证 |
| **runup** 抢跑/距高 | yfinance（无需 key） | 财报前 20 日相对 SMH/QQQ 超额收益、距 52w 高 | → `pead_dossier.market_setup` | 透支判断 |
| **options** 期权 | **ThetaData 本地终端** → yfinance 兜底 | Expected Move、ATM IV、25Δ skew（BS 反解） | → `pead_dossier.market_setup` | ⚠️ 终端开着才准（IV≈真值）；终端没开走 yfinance 时 IV 退化，建议跑财报时开 `./scripts/start_thetadata.sh` |
| **news** 新闻 | Finnhub（`FINNHUB_API_KEY`）+ 策选 RSS | 标的 + 信号链公司新闻（标题/摘要/链接/时间），去重 | → `pead_events`（去重日志） | 连续监控用；X/社媒见待接入 |
| **transcript** 电话会纪要 | Tavily（`TAVILY_API_KEY`）→ 手动落档兜底 | 财报电话会全文（搜 fool/investing 抓正文） | → `var/transcripts/` / dossier.actuals | FMP 也支持但需付费层（免费 402）；也可 `--transcript <链接/路径>` |
| **documents** 官方文档 | SEC 8-K Ex99.1 + Tavily + 本地文件夹 | **财报新闻稿**（SEC，权威自动）+ **投资者 PPT**（Tavily，通用自动）+ 文件夹精选 | → score 的 actuals 抽取 | 文件夹 `信息源/<SYM>/` 有则优先用、自动补缺、不重复 |

**已验证（COHR 实测）**：market(251 bar)、fundamentals(Rev 1,806M +20.5%YoY/CapEx/FCF/margins)、macro(F&G=40)、earnings(2026-08-11 amc)、consensus(EPS 1.62)、runup(vsSMH -7%)、options(ThetaData EM 35%/IV 101%)、news(84条)、transcript(Tavily 61K字)、documents(SEC 58K + deck 16K)。

---

## ⬜ 待接入 / 待测试

| 源 | 现状 | 增量价值 | 优先级 |
|---|---|---|---|
| **SEC XBRL Company Facts** | 未接（已验证可用：665 概念/全历史） | 结构化数字的**权威 as-reported + 超长历史**，防 yfinance 偶发错值；可替掉 yfinance 当权威层 | 🟡 中（有①够用，长期上） |
| **行业景气 / 产业链定量** | 无（行业分析师靠通用知识） | 渠道检查、价格、产能利用率等分部链路定量 | 🟡 中 |
| **X / 社媒**（Trump/Musk/Huang…） | 仅 stub（X API 受限/付费） | 重点账号实时信号 | 🟡 中（需选方案/付费） |
| **options IV（yfinance 兜底）改 BS 反解** | 兜底 IV 退化（≈0.2%） | 终端没开时也能拿到像样 IV/skew | 🟡 中（小改动） |
| **分析师评级 / 目标价** | 弱（yfinance 部分，无专源） | 评级变动、目标价分布（PEAD 透支判断） | 🟢 低 |
| **Reddit 情绪** | 未实现（`.env` 有 key 槽） | 散户情绪 | 🟢 低 |
| **内部人 / 机构 13F / 做空比例** | 未实现 | 持仓/做空结构 | 🟢 低 |
| **Day1-2 财报后漂移跟踪** | 未实现 | 记录财报后实际股价反应，校准 Scorecard 阈值 | 🟢 低（决策不依赖） |
| **Bloomberg/Reuters 高级新闻** | 用 Finnhub/RSS 替代 | 更全/更快的财经新闻 | 🟢 低（成本高） |
| **Aiera MCP（纪要）** | 环境挂载但未接 | 近实时纪要（替代 Tavily 抓取） | 🟢 低（需鉴权，headless 不稳） |
| **实时音频转写** | 评估后放弃 | 会中实时纪要 | ❌ 不做（产品级工程、收益小） |

---

## 存储机制

- **Context Memory `var/ats.sqlite`**：`pead_dossier`（PEAD 活体档案：叙事/预期/期权/抢跑/信号链/实际/Scorecard/决策）、`pead_events`（新闻去重日志）、`reports`/`decisions`/`trades`/`performance`（日常组合循环）。
- **`var/checkpoints.sqlite`**：LangGraph 暂停态（异步飞书审批跨进程 resume）。
- **`var/transcripts/<SYM>_<fiscal>.txt`**：手动落档纪要；**`信息源/<SYM>/`**（`docs_root`）：官方 PDF。
- **原始行情/基本面/宏观/期权/consensus 不单独落库**——每次 run 现取，分析产出落 dossier；`var/data_dumps/` 仅供人工查验。
- 查存储：`ats pead show <SYM>` / `sqlite3 var/ats.sqlite ".tables"`。

## key 一览（`.env`）

必填：`OPENAI_API_KEY`(OpenRouter)。已配：`FRED_API_KEY`、`FINNHUB_API_KEY`、`TAVILY_API_KEY`、`SEC_EDGAR_USER_AGENT`、`FMP_API_KEY`(付费纪要才用)、`FEISHU_BOT_WEBHOOK`+`FEISHU_APPROVE_*`。本地服务：ThetaData 终端（期权）。
