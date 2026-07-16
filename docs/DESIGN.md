# 多 Agent 交易系统 — 设计文档

> 状态：**v0.3** · 最后更新：2026-07-16（v0.1 基线 2026-06-13，v0.2 2026-07-05）
> v0.2 核心变化：PEAD-first 架构定型；行业分析师/宏观策略师/风控官/Trader/Chief 全部落地；
> **决策收口 Chief**（分析师只产研究、不出单）；周期+事件双触发；级联注入原则明确化。
> v0.3 核心变化：**chief 决策图化**（LangGraph 单漏斗：所有下单路径汇入一张图，
> boss_review interrupt 是唯一审批闸口，异步审批全路径打通）；v0.1 遗留日循环删除；
> chief 每日无条件收口。workflow/触发矩阵详见 `docs/WORKFLOWS.md`。

---

## 1. 背景与目标

个人投资者的多 agent 美股交易系统：分析师团队产出研究 → **Chief 统一决策** → 真人（Boss）
审批 → Trader 执行，风控官全程硬约束。

| 约束 | 决定 |
|---|---|
| 决策权 | 分析师不出单；**只有 Chief 产生 TradeDecision**；只有 Boss（真人）放行 |
| 审批 | 单一审批点：决策图 `boss_review` interrupt（CLI/飞书），任何下单必过 |
| 周期 | swing/position（日/周级），非高频；**PEAD 财报事件驱动为 MVP 主线** |
| 券商 | IBKR（paper 7497 → live 7496 仅配置切换，下单前 LIVE 二次确认） |
| 组合 | 3-15 个美股标的，AI 硬件产业链为主（有意集中，风控设高天花板） |
| LLM | OpenRouter 多模型路由：Opus 4.8 做判断、Gemini Flash 做高频分诊/抽取 |

## 2. 总体架构（v0.2）

```
                    周期触发(cron: 每交易日) + 事件触发(events.yaml + 财报临近)
                                          │
   ┌──────────────────────────────────────┼──────────────────────────────────────┐
   │  分析师层（平级，各自取数→分析→存档；可读上游已发布报告，不可修改，不出单）      │
   │                                                                              │
   │  宏观策略师(周一+FOMC/CPI/NFP) ──feed──▶ 行业分析师(周一+行业会议) ──inject──▶ │
   │      └─▶ macro_reviews                     └─▶ sector_reviews                │
   │                                                        │                     │
   │  PEAD 基本面分析师(每日 monitor/research；财报前 prep；财报后 score)           │
   │      └─▶ pead_dossier（Scorecard + 交易【建议】，不直接出单）                  │
   │                                                                              │
   │  风控官(每日快照，确定性无LLM) ─▶ risk_reviews（6层画像/破限/risk_state）      │
   │  Trader(确定性无LLM) ─▶ 实时 portfolio / fills / performance                  │
   └──────────────────────────────────────┬──────────────────────────────────────┘
                                          ▼
              Chief 首席（唯一决策者，每交易日收口 + score 后手动 --chief）
              读全部存档: dossier + sector_reviews + macro_reviews + risk_reviews
                        + 实时 portfolio + 战绩反馈
                                          ▼  TradeDecision[]
              chief 决策图 (graph/chief.py)  ═══ 单一执行漏斗 ═══
                ① 6层风控硬闸(risk_gate: block/clip) → ② 决策先落库(persist_decision)
                → ③ Boss 审批(boss_review interrupt, CLI同步/飞书异步checkpoint)
                → ④ IBKR 下单(trader) → ⑤ trades(含上下文JSON)+fills 落库
```

所有下单路径（每日收口 `scheduled` / 财报事件 `pead-chief` / 手动 `chief` /
trader CLI `manual`·`stored-decisions`）汇入这一张图；v0.1 单日循环
（ingest→四分析师→manager→trader）已删除（git 历史留档）。

## 3. 角色与决策权矩阵

| 角色 | 实现 | 产出（写） | 可读 | 决策权 |
|---|---|---|---|---|
| **宏观策略师** | `agents/macro/`（权益策略师范式，Opus） | macro_reviews（regime/利率路径/**sector_tilts**） | 定量盘+Tavily+FactSet | ❌ 不出单 |
| **行业分析师** | `agents/sector/`（L1-L6 分层，Opus） | sector_reviews（层评审/**company_calls**） | 快照+PEAD档案+宏观评审(上游) | ❌ 不出单 |
| **PEAD 分析师** | `agents/pead/`（prep/monitor/score） | pead_dossier（叙事/预期/Scorecard/**建议**） | 数据源+行业/宏观评审(上游注入) | ❌ 只出建议 |
| **风控官** | `risk/`（**确定性无LLM**） | risk_reviews（6层画像/breaches/risk_state） | portfolio+价格+存档 | ❌ 硬闸门（否决/裁剪，不产生交易） |
| **Trader** | `trader/`（**确定性无LLM**） | trades/fills/performance | IBKR | ❌ 纯执行+记录 |
| **Chief 首席** | `agents/chief/`（Opus） | cycles/decisions（chief-*） | **全部存档+实时portfolio** | ✅ **唯一产生 TradeDecision** |
| **Boss** | 真人（CLI/飞书审批） | approval | 审批卡片（含风控破限） | ✅ **唯一放行** |

**隔离原则**（v0.2 明确化）：分析师平级——各自独立分析、只写自己的存档；**可以读取**
其他分析师**已发布**的报告作为上游背景（自上而下级联：宏观→行业→PEAD，如投行策略师
报告全公司可读），**不能修改**对方产出、**不能出单**。级联注入点：
`sector/assemble.py`(宏观背景块)、`graph/pead.py prep_fetch`(行业+宏观块)、
`pead/monitor.py`(regime hints)，全部由 `pead.yaml` 开关控制。

## 4. 触发矩阵

| 角色/动作 | 周期型 | 事件型 |
|---|---|---|
| PEAD monitor+research | 每交易日 | `pead:<SYM>` 日历事件（行业会议等） |
| PEAD prep | — | 财报前 ≤`prep_days_before`(3) 日 |
| PEAD score | — | 财报后（bmo 当日 / amc 次日） |
| 行业分析师 review | 每周一 | `sector[:name]` 日历事件（行业会议/发布会） |
| 宏观策略师 review | 每周一 | `macro` 日历事件（**FOMC/CPI/NFP**/政府报告） |
| 风控官快照 | 每交易日收盘 | derisk/破限 → 飞书告警 |
| Trader 绩效快照 | 每交易日收盘 | — |
| **Chief 收口** | **每交易日（调度末位，读全部新鲜产出）** | score 完成（手动 `--chief`）/ 手动 `ats chief run` |

事件日历：`config/events.yaml`（date/kind/label/triggers），`scheduler._event_triggers()`
每日检查，命中触发对应分析师**额外**跑一次；置于 pead_daily 之前使刷新级联进当日
monitor 与当晚 Chief。维护：每季度补下季 FOMC/BLS 日期（`ats events upcoming` 会提示过期）。

调度顺序（`scheduler._daily`，mon-fri cron + NYSE session 过滤）：
`宏观周 → 行业周 → 事件触发 → PEAD(research→monitor/prep/score) → 绩效+风控快照
→ Chief 收口（每交易日无条件，读全部新鲜产出；安静日零决策不发卡）`。

## 5. PEAD 事件工作流（MVP 主线）

```
START → load → ┬ prep:  fetch → narrative → expectations → signal_chain → persist → END
               └ score: fetch → actuals → scorecard → decision(建议) → persist → END
```
- **prep**（财报前）：建期望基准——叙事（注入：静态行业笔记+最新行业/宏观评审+monitor
  累积的活叙事 `prior_narrative`，**prep 是唯一叙事 consolidation 点**）、分维度预期
  （保守/中性/乐观）、信号链、市场 setup（抢跑/期权 Expected Move）。
- **monitor**（财报间每日）：新闻(Finnhub+RSS→**Gemini Flash 分诊降噪**→高分抓正文)+研报
  insight 折进 dossier 叙事；结构化维度变更持久化。
- **score**（财报后）：纪要/8-K/财报→抽实际值→对基准打 Surprise Scorecard（-2..+2 加权）
  →产出**建议**（scoped 风控预夹，risk-aware）→ 存 dossier。**不出单、不审批**——Chief 收口。
- 数据源清单与状态见 `docs/DATA_SOURCES.md`（11 个源 + 2 条 LLM 通道 + 行业知识注入）。

## 6. Chief 统一决策（v0.2 新增）

`agents/chief/`：`assemble.build()` 只读收集六块（实时持仓 / PEAD 档案**含新鲜度标注**
（score 期 ≤3 交易日=可行动，否则仅背景）/ 行业 company_calls / 宏观 sector_tilts /
风控 risk_state+breaches（derisk 时前置硬指令）/ 战绩反馈）→ 单次 Opus
（`chief` 角色，`skills/chief`）→ `TradeDecision[]`。

Skill 纪律：PEAD scorecard 是主 alpha；行业/宏观是倾斜修正器非独立信号；risk_state 是
约束；持仓复查（止损/落空/降级）；**零交易是正确默认**。

执行：chief 决策图（`graph/chief.py`）—— 风控硬闸+决策落库+Boss 审批+下单+落库全在
这一张图里，**全系统无第二个审批点**；`trader.execute()` 也只是该图的薄封装
（decide=False, seed_decisions）。CLI：`ats chief {run|show|probe}`（probe 免 LLM 查上下文）。

## 7. 六层风控（确定性硬约束）

`risk/`（无 LLM）；阈值在 `settings.yaml risk:`；产业链层限额在 `sectors/ai_hardware.yaml`
各层 `weight_cap`。

| 层 | 硬限额 | 动作 |
|---|---|---|
| 1 标的 | 单票 20% · **每产业链层 weight_cap**(L6设备10%…) · 止损-25% | 削/block/强制trim |
| 2 组合 | 杠杆 1.0 · 现金 ≥5% | 缩买单 |
| 3 因子 | 组合 beta ≤1.5 · 相关簇 ≤75%(实算相关矩阵+聚簇；有意集中故高) | block 加仓 |
| 4 回撤 | 回撤-15%→**derisk**(只许减仓) · 日亏-5%→停新仓 | block 所有新买 |
| 5 压测 | 情景损失≤25%NAV(beta×冲击+AI泡沫打簇) | block 加重敞口 |
| 6 事件 | 财报 gap≤3%NAV(仓位×Expected Move) | 削 notional |

强制点：`risk.checks.pre_trade()` 在**决策图 risk_gate 节点（审批之前，任何下单路径必过）**
与 PEAD score 建议生成时（scoped）。每日快照存 `risk_reviews`，derisk/破限推飞书。
`ats risk {report|check}`。

## 8. Context Memory（SQLite `var/ats.sqlite`，13 表）

| 表 | 写入者 | 读取者 |
|---|---|---|
| pead_dossier | PEAD prep/monitor/score | Chief、行业分析师、monitor 自身 |
| pead_events | monitor(+研报注入)，含 triage_score | monitor 上下文、行业分析师 |
| research_articles/insights | 研报通道(Gmail→QQ IMAP+RSS) | 行业分析师、monitor |
| sector_reviews | 行业分析师 | Chief、PEAD 注入、行业历史对比 |
| macro_reviews | 宏观策略师 | Chief、行业/PEAD 注入 |
| risk_reviews | 风控官(每日) | Chief、告警 |
| trades(含context JSON)/fills | Trader | Chief 战绩反馈、绩效分析 |
| performance(含account_id) | Trader 每日快照 | Chief、`ats trader perf`、风控回撤 |
| cycles/decisions | Chief 决策图（source ∈ chief/scheduled/pead-chief） | Chief 自反馈、审计 |
| reports | （无新写入者，历史数据） | Boss `report <SYM>` 上下文 |

原始行情/基本面等不落库（run 时现取）；`var/data_dumps/` 仅人工查验。
向量记忆层（Chroma）仍未建——见路线图。

## 9. Schemas（`src/ats/schemas/`）

`market` `portfolio`(Position 含 beta) `decision`(TradeDecision/BossApproval)
`memory`(TradeLogEntry/Fill/PerformanceRecord) `risk`(RiskGuardrails/**RiskReview** 6层)
`pead`(PeadConfig/ExpectationSet/Scorecard/PeadDossier) `news`(NewsItem/ContextUpdate)
`research`(Article/Insight) `sector`(SectorConfig 含 weight_cap/SectorReview)
`macro`(MacroData 含信用利差·大宗) `macro_strategy`(MacroReview/SectorTilt)
`events`(CalendarEvent) `channel` `reports`(历史 reports 表)

## 10. Skills 与 LLM 路由

15 个 skill（`src/ats/skills/<slug>/SKILL.md`）。路由（`settings.yaml llm.routing`，
经 OpenRouter）：

- **Opus 4.8**（判断，低频）：`chief` `manager`(路由键，pead-scorer 复用) `sector_analyst`
  `macro_strategist` `research_extract` + PEAD prep 叙事/预期、scorer
- **Gemini 2.5 Flash**（高频/抽取）：`news_triage`(新闻分诊) `context_monitor`(monitor 合成)
  `actuals_extract`(财报实际值抽取)

外部正文均含提示注入防护（skill 声明不可信数据 + 结构化输出 + 代码侧白名单/clamp）。

## 11. HITL 审批与通道

唯一审批点：决策图 `boss_review` interrupt。构 `ApprovalRequest`（含账户/端口/paper|live
警示 + 风控破限清单）→ CLI 同步问答，或飞书卡片 + checkpoint 退出、`ats serve` webhook
`resume_cycle(thread_id)` 恢复。审批结果与完整上下文（决策+审批人+来源）JSON 落
`trades.context`。`--yes`（auto_approve）永不默认，实盘禁用。

## 12. 仓库结构（v0.2 实况）

```
src/ats/
  agents/            # base(run_structured) risk_manager risk_validator
    pead/            #   prep score monitor triage research outputs
    sector/          #   assemble review report context outputs
    macro/           #   assemble review report context outputs
    chief/           #   assemble decide outputs        ← v0.2 决策收口
  broker/ibkr.py     # IBKR: portfolio/pnl/fills/orders/cancel
  channel/           # cli / feishu / feishu_bot + server 回调
  data/              # market fundamentals macro consensus options runup earnings_calendar
                     # news(分诊) research(newsletter) transcript documents industry(行业知识)
                     # factset websearch(Tavily) sector_snapshot web indicators base
  graph/             # chief+chief_state(决策图) pead+pead_state(事件图) checkpoint
  memory/            # store(13表) performance
  risk/              # correlation stress assess checks report   ← 6层风控
  trader/            # portfolio performance analytics execute   ← 执行+绩效
  runtime/           # cli scheduler server
  schemas/ skills/
config/              # settings watchlist pead(+pead/<SYM>) sectors/ macro.yaml events.yaml news_sources
docs/                # DESIGN DATA_SOURCES SECTOR_ANALYST GO_LIVE
```

## 13. 路线图

**已完成（v0.1→v0.3）**：✅日循环基线 ✅PEAD prep/monitor/score ✅新闻双通道(分诊+研报)
✅行业知识注入 ✅行业分析师 ✅宏观策略师(FactSet) ✅Trader(审批执行+绩效) ✅风控官(6层)
✅Chief 收口 ✅事件日历 ✅飞书异步审批 ✅每日快照/告警 ✅chief 决策图化（单漏斗+
异步审批全路径）✅遗留日循环退役

**下一步**：① events.yaml 日期校准与季度维护流程（经济日历 API 自动填充）② Chief 决策
复盘（决策 vs 实际收益归因，喂回 skill）③ PEAD 财报后 Day1-2 漂移跟踪校准 Scorecard
阈值 ④ 向量记忆层 ⑤ live 运行经验回填 GO_LIVE checklist

## 14. 验证

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q          # 全量
# 单角色
ats macro review / ats sector review ai_hardware / ats pead prep|score COHR
ats risk report / ats trader portfolio / ats chief probe
# 端到端（真实链路）
ats schedule --now        # 宏观→行业→事件→PEAD→快照→Chief，全流程 dry-run
ats chief run             # 收口：读存档→决策→风控→审批→(dry-run)执行
```

## 15. 已知风险与待定

- events.yaml 日期需人工季度维护（过期有 CLI 提示，但无自动抓取）
- Chief 每日一次 Opus（~$0.3-0.6/次）；安静日零决策不发审批卡
- 相关簇/压测基于历史相关性与 beta 代理，非完备风险模型（个人投资者权衡）
- 韩/日标的数据退化（earnings/options/news 无覆盖）
