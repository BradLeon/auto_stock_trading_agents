# 设计文档：基于 AI Agents 的自动股票交易系统（auto_stock_trading_agents）

> 状态：v0.1（设计基线） · 最后更新：2026-06-13

## 1. 背景与目标（Context）

构建一个由多 Agent 协作、**人类在环（HITL）审批**的自动股票交易系统。

**经确认的关键约束（决定架构）：**

| 维度 | 决策 | 架构含义 |
|---|---|---|
| Boss 审批 | **真人审批（HITL）** | LangGraph `interrupt()` + checkpoint，可中断/可恢复 |
| 交易周期 | **波段/持仓（日级–周级）** | 批处理 + 调度，不上 streaming/低延迟撮合 |
| 落地阶段 | **先 IBKR Paper Trading** | 模拟盘跑通全链路并验证策略 |
| 标的范围 | **少量精选（3–10 只）**，如 NVDA/GOOGL/AAPL | 基本面/技术面按标的实例化 |
| 券商 | **仅 IBKR** | `ib_async` + 独立 paper/live profile |
| LLM | **仅 Claude Opus（`claude-opus-4-8`）** | Agent 层与模型解耦，OpenAI 兼容格式便于切换 |

**开发规范：** Agent 编排用 LangGraph；Agent 执行流程用 Skills（SKILL.md）规范；数据结构用 Pydantic 实例化。

**本阶段目标产出：** 可运行的端到端骨架——在 IBKR 模拟盘上完成「数据采集 → 多分析师并行 → 风控约束 → Manager 决策 → 人类审批 → Trader 执行 → 记忆/绩效回写」一个完整周期。

> 备注：本运行环境已挂载 **IBKR MCP**、**Aiera（财报电话会纪要）MCP**、Morningstar/FactSet/S&P 等金融数据 MCP，可作为数据源/券商层的加速选项。
>
> 参考开源项目：[TradingAgents](https://github.com/TauricResearch/TradingAgents)、[anthropics/financial-services](https://github.com/anthropics/financial-services)。

---

## 2. 总体架构

```
                   ┌─────────────────────── 调度 (scheduler / CLI) ───────────────────────┐
                   │                每日盘前/盘后触发一个 trading cycle                      │
                   ▼
        ┌───────────────────┐
        │  数据采集 ingest    │  yfinance / SEC / 财报纪要 / 新闻 / 社媒 / 研报 / 宏观
        └─────────┬─────────┘
                  │  (写入 Context Memory，产出标准化 Pydantic 数据)
                  ▼
   ┌──────────────────────── 分析师团队（LangGraph 并行 fan-out, Send API）────────────────────┐
   │  宏观分析师×1(全局)   行业分析师×N(按板块)   基本面×M(按标的)   技术面×M(按标的)              │
   └──────────────────────────────────┬────────────────────────────────────────────────────┘
                                       ▼  (各自产出 AnalystReport)
                          ┌────────────────────────┐
                          │   风控负责人 RiskManager │  读 IBKR Portfolio → Guardrails
                          └───────────┬────────────┘
                                      ▼
                          ┌────────────────────────┐
                          │      Manager（决策）     │  报告 + 约束 → TradeDecision[]
                          └───────────┬────────────┘
                                      ▼   interrupt()  ← LangGraph 暂停
                          ╔════════════════════════╗
                          ║  Boss 审批（真人 HITL）  ║◀──▶ BossChannel (CLI / 飞书 / Discord)
                          ╚═══════════┬════════════╝
                                      ▼  (resume)
                          ┌────────────────────────┐
                          │   Trader（纯执行）       │  IBKR 下单 + 交易日志
                          └───────────┬────────────┘
                                      ▼
                          ┌────────────────────────┐
                          │  记忆/绩效回写 memory     │  trade log / reports / performance
                          └────────────────────────┘
```

**编排模型：** 单个 LangGraph `StateGraph`，节点 = Agent，边 = 数据/控制流。分析师层用 **Send API** 做 map 式并行扇出（按标的/板块动态生成节点调用）。Boss 审批用 **`interrupt()`** 暂停、checkpoint 持久化、人类响应后 `Command(resume=...)` 继续。整个 cycle 可断点恢复（进程重启不丢状态）。

---

## 3. Agent 角色与职责

| 角色 | 实例化 | 输入 | 输出（Pydantic） | 节点类型 |
|---|---|---|---|---|
| 宏观分析师 | 全局 ×1 | 利率/CPI/非农(FRED)、关税/政治/地缘、SPX/NDX 盈利、VIX、贪婪指数 | `MacroReport` | LLM 节点 |
| 行业分析师 | 按板块 ×N | 行业景气度、产业链上下游瓶颈/利润传导（如 AI 硬件链） | `IndustryReport` | LLM 节点 |
| 基本面分析师 | 按标的 ×M | 财报、Earnings Call、SEC Filings、yahooFinance | `FundamentalReport` | LLM 节点 |
| 技术面分析师 | 按标的 ×M | 价量、均线/MACD/RSI、形态、支撑阻力 | `TechnicalReport` | LLM 节点（含 TA 工具） |
| 风控负责人 | 全局 ×1 | IBKR Portfolio：敞口、杠杆、盈亏比、行业集中度、持仓占比 | `RiskGuardrails` | LLM + 工具节点 |
| Manager | 全局 ×1 | 全部分析报告 + Guardrails | `TradeDecision[]` | LLM 节点 |
| **Boss** | 真人 | Manager 决策 | Approve/Reject/指令 | **HITL interrupt（非 LLM）** |
| Trader | 全局 ×1 | 已审批的决策 | `OrderResult[]` + `TradeLog` | 纯工具节点（无 LLM 或仅做参数校验） |

**设计要点：**
- **分析师互相独立**（无串话），降低偏见传染；并行执行。
- **Trader 不做判断**：只把 `TradeDecision` 翻译成 IBKR 订单并执行，强约束防止越权。
- **风控产出的是 guardrail 约束**（单一持仓上限、板块集中度上限、禁止加仓清单等），Manager 必须在约束内决策；并在 Manager 输出后加一个**确定性校验器**做硬性裁剪，不完全依赖 LLM 自觉遵守。

---

## 4. LangGraph State 设计

`src/ats/graph/state.py` —— 用 Pydantic 模型作为 graph state：

```python
class TradingState(BaseModel):
    cycle_id: str
    as_of: datetime
    watchlist: list[Ticker]                      # 本轮标的
    market_data: dict[str, MarketSnapshot] = {}  # ingest 产出
    macro_report: MacroReport | None = None
    industry_reports: Annotated[list[IndustryReport], add]   # reducer 合并并行结果
    fundamental_reports: Annotated[list[FundamentalReport], add]
    technical_reports: Annotated[list[TechnicalReport], add]
    risk_guardrails: RiskGuardrails | None = None
    decisions: list[TradeDecision] = []
    approval: BossApproval | None = None         # interrupt 回填
    order_results: list[OrderResult] = []
```

- 并行分析师用 `Annotated[list[...], operator.add]` reducer 聚合。
- Checkpointer：开发期 `SqliteSaver`（`./var/checkpoints.sqlite`），生产 `PostgresSaver`。

---

## 5. 数据源模块

`src/ats/data/` —— 统一 `DataSource` 协议（`fetch() -> Pydantic 模型`），可缓存、可降级、带速率限制。

| 子模块 | 内容 | 首选实现 | 备选/MCP |
|---|---|---|---|
| `market_data.py` | 价量、基础行情 | `yfinance` | IBKR MCP / Finnhub |
| `fundamentals.py` | 财报、SEC Filings | `edgartools`(SEC EDGAR) + `yfinance` | Daloopa/FactSet MCP |
| `transcripts.py` | Earnings Call 纪要 | **Aiera MCP** | FMP / API Ninjas |
| `news.py` | 财经新闻(Bloomberg/Reuters 类) | Finnhub / Tavily / NewsAPI | — |
| `social.py` | Reddit / X，重点账号(Trump/Karpathy/Musk/Huang) | Reddit `PRAW`；X 用官方 API 或备用抓取 | — |
| `research.py` | 研报/newsletter（SemiAnalysis、MarketSenseAI、Substack） | RSS + 正文抓取 | — |
| `macro.py` | 利率/CPI/非农、VIX、贪婪指数、SPX/NDX | FRED API + yfinance(^VIX,^GSPC,^IXIC) + CNN F&G | — |

约定：所有适配器输出标准化 Pydantic 模型；原始抓取内容落 Context Memory；外部失败要降级（返回 `None`/缓存）而非中断整个 cycle。新闻类付费源短期用 Finnhub/Tavily 替代并标注覆盖差距，避免「沉默截断」。

---

## 6. Context Memory

`src/ats/memory/` —— 双层：
- **结构化层（SQLite→Postgres）**：`trade_log`、`decisions`（含审批结果）、`reports`、`performance`（每日 PnL、命中率、盈亏比、回撤）。
- **语义层（向量库 Chroma）**：报告/新闻/研报正文 embedding，供分析师 RAG 召回历史相似情景，也供 Boss 通过 `BossChannel.fetch_report_context()` 调取。

绩效追踪：每个 cycle 结束后计算并入库，作为下一轮 Manager 的反馈输入。注意区分 LangGraph **checkpoint（单 cycle 短期状态）** 与 **Context Memory（跨 cycle 长期记忆）**。

---

## 7. Pydantic 数据契约

`src/ats/schemas/` —— 所有 Agent I/O 强类型化，LLM 节点用 **structured output / tool-calling** 强制产出对应 schema：
- `market.py`：`Ticker`, `MarketSnapshot`, `OHLCV`
- `reports.py`：`MacroReport`, `IndustryReport`, `FundamentalReport`, `TechnicalReport`（共同基类含 `signal: Literal["bullish","neutral","bearish"]`, `conviction: 0-1`, `thesis`, `key_risks`, `sources`）
- `risk.py`：`RiskGuardrails`（`max_position_pct`, `max_sector_pct`, `max_gross_leverage`, `no_add_list`, `forced_trim`...）
- `decision.py`：`TradeDecision`、`BossApproval`（`status: approved/rejected`, `overrides`, `direct_instructions`）
- `portfolio.py`：`Position`, `PortfolioSnapshot`, `ExposureBreakdown`
- `memory.py`：`TradeLogEntry`, `PerformanceRecord`
- `channel.py`：`Notification`, `ApprovalRequest`, `ReportBundle`（Boss 交互用，见 §11）

---

## 8. Skills（Agent 执行流程规范）

`src/ats/skills/<role>/SKILL.md` —— 每个角色一个 Skill，固化其分析流程：目标、需调用的数据工具、分析步骤/检查清单、输出 schema 约束、few-shot 示例。Agent 节点运行时把对应 SKILL.md 注入 system prompt（配合 Claude **prompt caching** 缓存稳定部分降本）。「流程」与「代码」解耦，便于迭代调参而不改 Python。

---

## 9. LLM 抽象层（Claude Opus，可切换）

`src/ats/llm/gateway.py` —— `get_model(role: str) -> ChatModel`，按 `config/settings.yaml` 的 `model_routing` 选模型，默认 `claude-opus-4-8`。
- 主路径：`langchain_anthropic.ChatAnthropic`（原生 tool-calling + prompt caching，与 LangGraph 无缝）。
- 切换路径：经 **LiteLLM**（OpenAI 兼容网关）统一为 OpenAI 格式，改 config 即可换 provider，Agent 代码零改动。
- 温度/max_tokens/重试/超时集中在 gateway 配置。

---

## 10. IBKR 集成（Paper）

`src/ats/broker/ibkr.py` —— 封装连接 IB Gateway / TWS **paper 账户**：
- 库：`ib_async`（`ib_insync` 的活跃维护继任者）；或用 **IBKR MCP** 作为替代/补充接口。
- 能力：`get_portfolio()`（供风控）、`place_order()` / `cancel_order()`（供 Trader）、`get_positions()`、`get_account_summary()`。
- 安全：paper 与 live 用独立 config profile；Trader 下单前做最终校验（在 guardrails 内、不超审批范围）；所有下单写 `trade_log`。

---

## 11. HITL 审批流 与 Boss 交互客户端

### 11.1 审批流（图侧）

Manager 节点后接 `interrupt()`：把 `TradeDecision[]` 摘要交给 `BossChannel` → 人类回 `BossApproval`（approve / reject / 改写 / 直接指令）→ `Command(resume=approval)` 继续到 Trader。被 reject 的决策不下单并记录原因；Boss `direct_instructions` 可绕过 Manager 直接构造订单交给 Trader。状态全程 checkpoint，审批可跨进程/隔天进行。

### 11.2 Boss 交互客户端抽象（关键设计决策）

> **决策：设计层面一次性纳入抽象，实现层面分两期。** 一期实现 CLI 通道跑通 backend；二期加飞书（优先）/Discord 适配器。LangGraph 的 interrupt/resume 已把审批机制与图逻辑解耦，审批入口只是 I/O 边缘适配器，留好抽象点即可零改图、零改 Agent 地接入新渠道。

`src/ats/channel/` —— 定义端口接口 `BossChannel`（Protocol）：

```python
class BossChannel(Protocol):
    def push(self, msg: Notification) -> None: ...                                  # 主动推送（决策待审、成交回报）
    def request_approval(self, req: ApprovalRequest) -> BossApproval: ...            # 阻塞等审批
    def fetch_report_context(self, query: str) -> ReportBundle: ...                  # Boss 调取相关 report（查 Context Memory）
```

适配器：
- `cli_channel.py` —— **一期**：终端交互，展示决策摘要、读取 approve/reject/指令、支持 `report <ticker>` 调取上下文。
- `feishu_channel.py` —— **二期（优先）**：飞书自定义机器人 + 事件订阅回调；用消息卡片承载决策摘要与「批准/驳回」按钮；Boss 可在群里发指令查 report。可由 Hermes / OpenClaw 这类对话式 agent 托管前端。
- `discord_channel.py` —— **二期**：Discord bot，同上交互模型。

实现注意：审批是阻塞式（图在 `interrupt` 处等待），飞书/Discord 适配器需用「图暂停 + checkpoint 落库 → 异步收到机器人回调 → 用同一 thread_id resume 图」的模式，而非长连接死等。

---

## 12. 仓库结构

```
auto_stock_trading_agents/
├── pyproject.toml                # uv，依赖与脚本入口
├── .env.example                  # API keys (Anthropic/FRED/Finnhub/Reddit/IBKR...)
├── docs/DESIGN.md                # 本文档
├── config/
│   ├── settings.yaml             # 标的、板块映射、调度、风险阈值、model_routing、channel
│   └── watchlist.yaml
├── src/ats/
│   ├── config.py                 # pydantic-settings 加载
│   ├── schemas/                  # §7 数据契约
│   ├── llm/gateway.py            # §9
│   ├── data/                     # §5 数据源适配器
│   ├── memory/                   # §6 结构化+向量
│   ├── agents/                   # 各角色节点 (analysts/, risk_manager, manager, trader)
│   ├── skills/                   # §8 各角色 SKILL.md
│   ├── channel/                  # §11 BossChannel 抽象 + cli/feishu/discord 适配器
│   ├── graph/                    # state.py / build.py / nodes.py / checkpoint.py
│   ├── broker/ibkr.py            # §10
│   └── runtime/                  # cli.py（跑/恢复一个 cycle）, scheduler.py
└── tests/                        # 单测 + 用 mock 数据/paper 账户的集成测试
```

---

## 13. 技术栈

`langgraph` · `langchain-anthropic` · `litellm` · `pydantic` / `pydantic-settings` · `ib_async` · `yfinance` · `edgartools` · `praw` · `chromadb` · `pandas` / `pandas-ta`(技术指标) · `fredapi` · `tenacity`(重试) · `apscheduler` + `pandas_market_calendars`(调度/交易日历) · `uv` 包管理 · `pytest`。二期：`lark-oapi`(飞书) / `discord.py`。

---

## 14. 分阶段交付路线

1. **骨架与契约**：仓库结构、`pyproject`、`config`、全部 Pydantic schema、LLM gateway（先单一 Claude Opus）。
2. **图骨架**：用 stub 节点搭起完整 LangGraph 拓扑，含并行 fan-out、interrupt 审批（CLI 通道）、checkpoint —— 先让「空跑」端到端通。
3. **数据源**：market_data + macro + fundamentals 三个核心源接通（其余后补/降级）。
4. **分析师 + Skills**：实现 4 类分析师节点 + 对应 SKILL.md，接真实数据。
5. **风控 + IBKR 读**：连 paper 账户读 Portfolio，产出 guardrails。
6. **Manager + HITL CLI**：决策 + 真人审批交互。
7. **Trader + 下单**：paper 下单、交易日志。
8. **Memory + 绩效**：回写、绩效计算、反馈进 Manager。
9. **调度**：盘前/盘后自动触发。
10. **（二期）Boss 飞书/Discord 通道**：实现 `feishu_channel.py` / `discord_channel.py`。

---

## 15. 验证（端到端）

- **空跑**：阶段 2 后，`python -m ats.runtime.cli run --dry-run` 跑完整图（stub 数据），在 Manager 后正确 interrupt，`--resume` 后到达 Trader（mock）。验证并行 reducer 聚合、checkpoint 恢复。
- **数据源单测**：每个适配器对 mock 响应解析为正确 Pydantic 模型；失败时降级不崩。
- **IBKR 集成**：连本地 IB Gateway paper 账户，`get_portfolio()` 返回真实持仓；提交一笔限价单并在 TWS 中确认 + `trade_log` 落库。
- **HITL**：CLI 展示决策 → 输入 approve/reject → 验证 reject 不下单、approve 正确下单、direct_instruction 绕过 Manager。
- **全链路冒烟**：3 只标的（NVDA/GOOGL/AAPL）跑一个真实 cycle 到 paper 成交，检查 reports/decisions/trade_log/performance 四表入库。
- `pytest` 全绿。

---

## 16. 风险与待定项

- **新闻/社媒数据获取**：Bloomberg/Reuters 官方 API 昂贵；X API 受限。短期用 Finnhub/Tavily/Reddit 替代并标注覆盖差距，避免「看起来全覆盖实则缺失」。
- **LLM 成本**：3–10 标的 ×4 分析师 + 长上下文，单 cycle token 量可观 → 用 prompt caching、按角色路由（部分角色可降级到便宜模型）、控制召回上下文长度。
- **风控硬约束**：Manager 输出后用确定性校验器做硬裁剪，不完全依赖 LLM 自觉遵守。
- **时区/交易日历**：调度需处理美股交易日/盘前盘后，用 `pandas_market_calendars`。
- **审批渠道异步性**：飞书/Discord 审批必须走「checkpoint + 回调 resume」模式，不可长连接死等（见 §11.2）。
