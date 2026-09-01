# 结构化数据迁移矩阵

> 盘点日期：2026-08-25
> 用途：固定平台改造前的消费者、返回契约、覆盖和迁移边界
> 机器可读配置：[`config/data/structured.yaml`](../config/data/structured.yaml)

## 1. 结论

当前持久结构化能力主要服务 Chain 的台湾、韩国和 TrendForce 序列。公司财务和 Consensus 虽然是低频研究事实，但仍由 PEAD、Sector 在 Workflow 运行时直接访问 yfinance、Finnhub 和 SEC；`stock_statement` 尚无生产消费者。股价和期权则是刻意保留的 runtime 能力，不属于迁移缺口。

## 2. 消费者迁移矩阵

| 消费者 | 当前调用 | 当前返回契约 | 实际消费字段 | 覆盖范围 | 目标产品 | 初始模式 |
|---|---|---|---|---|---|---|
| PEAD prep / score | `fundamentals.fetch(symbol)` | `FundamentalData`，失败以 `notes` 和缺失字段降级 | 估值摘要、收入/利润率/净利润/EPS/CapEx/FCF/债务及 QoQ/YoY、recent filings | `config/pead.yaml` targets + observe 中被调度实体 | `company_financials`，再组装旧 DTO | legacy → shadow |
| PEAD prep | `consensus.fetch(symbol)` | 固定键 dict；数字缺失为 `None`，集合缺失为 `[]`，不抛错 | EPS/收入均值与区间、目标价、评级分布、最近评级动作 | PEAD targets/observe 的实际运行实体 | `market_consensus` snapshot，再组装旧 dict | platform；独立开关可回滚 legacy |
| Sector snapshot | `fundamentals.fetch_light(symbol)` | None-filled dict | market cap、P/E、forward P/E、gross margin、operating margin、revenue growth、beta | `SectorConfig.all_symbols()` | 低频财务从 DataProducts；估值/价格相关仍按用途判定 | legacy → shadow |
| Sector cross-section | `fundamentals.fetch_light` + `consensus.fetch` | `FactorRow` 上游 dict | market cap、beta、revenue growth、gross/operating margin、forward P/E、rating trend | `config/sectors/ai_hardware.yaml` 全部截面实体 | Consensus 使用持久 snapshot；估值、beta、TTM 财务与 momentum 保持 runtime | Consensus platform；其余 legacy/runtime |
| Risk | `fundamentals.fetch_light` | None-filled dict | beta | 当前持仓与风险标的 | 本变更保持 runtime，避免把快速组合风险输入混入迁移 | unchanged |
| Chain sources | `sources.fetch/collect` | `SeriesPoint` → `Observation` | 原始水平、unit、预计算 yoy/mom、published_at | `TW_IC_EXPORT`、`KR_SEMI_EXPORT`、`DRAM_CONTRACT_PRICE` | 原始水平 observation + 查询时派生，再兼容组装 | legacy → shadow → platform |
| 图表/临时研究 | `DataProducts.indicator_series` | row dict 或 DataFrame | period/value/unit/source/fetched_at | 已落 measurement 的序列 | 统一 metric series、cross-section、SQL/Pandas | compatibility → platform |

## 3. 当前财务契约

`FundamentalData` 当前包含两类性质不同的数据：

- 运行时市场/估值快照：market cap、P/E、P/S、dividend yield 等。
- 可持久研究事实：财务报表行项目、报告期间和 filing 元数据。

季度 statement 当前能渲染 Revenue、Gross Margin、Operating Margin、Net Income、Diluted EPS、CapEx、Free Cash Flow、Total Debt，并提前计算 QoQ/YoY。迁移后原始行项目进入 observation ledger，利润率、QoQ 和 YoY 由 derivation registry 计算；兼容层仍能组装同样的 `StatementMetric`。

旧路径的缺失语义必须保留：

- yfinance info 失败：字段保持 `None`，notes 加 `yfinance fundamentals unavailable`。
- statement 失败：`statements=None`，notes 加 `quarterly statements unavailable`。
- SEC 失败：`recent_filings=[]`，notes 加 `SEC filings unavailable`。
- 某个 statement 行缺失：不输出该行，不补 0。

## 4. 当前 Consensus 契约

固定标量键为：

```text
eps, eps_low, eps_high,
revenue, revenue_low, revenue_high,
target_mean, target_median, target_low, target_high, target_current,
rating_strong_buy, rating_buy, rating_hold, rating_sell, rating_strong_sell
```

集合键为 `rating_trend` 和 `upgrades_downgrades`。`fetch()` 永不向 Workflow 抛出 Provider 异常；失败标量保持 `None`，集合保持 `[]`。当前 `0q` 是 Provider 相对标签，未绑定具体公司财期，这是迁移必须修复的前视风险。

## 5. 当前区域序列契约

`SeriesPoint` 当前字段为 `period / series / value / unit / yoy / mom / published_at`。`collect()` 同时保存 measurement point，并把 yoy/mom 转成 Chain evidence Observation。

已知语义缺口：

- 台湾和韩国 Adapter 内提前计算 yoy/mom，平台无法统一版本公式。
- 台湾 Adapter 当前不填实际发布时间。
- `fetch()` 将各种异常吞为 `[]`，`collect()` 会把“未发布、零匹配、解析失败”都归成 unreachable。
- 当前没有完整 raw artifact/query-slice 血缘。

## 6. stock_statement 当前状态

仓库当前没有 `stock_statement` 生产 Adapter、存储表或消费者。defeatbeta 模块已使用 DuckDB 对远程 Parquet 做 transcript 切片，证明 predicate pushdown 技术路径可行，但不能把 transcript 的 schema 或来源语义直接套到 statement。

目标是把可映射的 statement 行项目写入共享 `company_financials` 指标体系。HuggingFace 只记录为托管渠道，业务血缘同时保留 defeatbeta 数据集及其 Yahoo Finance 上游声明。未知行项目进入 pending mapping，不建立平行 statement 数据库。

## 7. 首批核心指标的来源

首批清单只取自现有真实消费和基本对账需要：

- PEAD statement：revenue、gross/operating margin、net income、diluted EPS、CapEx、FCF、total debt。
- Sector：revenue growth、gross/operating margin；market cap、P/E、forward P/E 和 beta 需按 persistent/runtime 用途分开处理。
- 财务完整性：gross profit、operating income、cash from operations、cash/equivalents、inventory。
- Consensus：EPS/收入均值与区间、目标价、评级分布、评级变更。
- Chain：台湾出口水平、韩国出口指数水平、TrendForce 合约价水平。
- 证据型：funding amount、pre/post-money valuation、ARR。

未进入首批清单的 Provider 字段一律进入 pending mapping，并保留原字段和 artifact；不得静默丢弃。

## 8. Runtime / excluded 审计

| 路径 | 消费者 | 决策 |
|---|---|---|
| `ats.data.market_data` | PEAD、run-up、trader | runtime/excluded，不建 OHLCV observation |
| `ats.data.sector_snapshot` | Sector、Technical、Risk | runtime/excluded，不建 ticker 日线 |
| `ats.data.options` | PEAD、CLI | runtime/excluded，不建期权链/Greeks/IV vintage |
| `ats.broker.IBKRBroker` | Trader、Risk、Journal | runtime/excluded；交易与成交仍按既有 Journal 职责保存 |
| `ats.research.prices` | 研究回放 | 既有独立、可丢弃研究缓存，不纳入本平台权威数据集 |

本变更不得为以上路径增加采集调度、raw artifact、measurement series、历史回填或 structured snapshot 输入。

## 9. 验收与回滚矩阵

| 数据集 | 真实样本 | 关键门槛 | 切换失败时 |
|---|---|---|---|
| 台湾/韩国出口 | 两个固定地区实体 | 声明窗口 100% 连续；水平值与官方一致；派生与旧结果容差 `1e-6` | Chain 恢复 legacy，保留新 ledger 审计 |
| 公司财务 | AMZN、MSFT、KLAC、TSM、一个镜像缺失实体 | 核心覆盖 ≥80%；官方抽样 100% 正确；跨源差异逐项展示；无前视 | PEAD/Sector 保持 legacy，只保留 shadow 报告 |
| Consensus | AMZN、MSFT、KLAC、TSM | 目标财期已绑定；NaN 不入库；两个 known_at 无前视；旧 dict 容差 `1e-6` | PEAD 恢复 legacy Consensus |
| 证据型事件 | OpenAI、Anthropic | 发布样本人工抽查 100% 正确且有 document/version/span | 默认查询保持关闭，候选留在核验队列 |

切换粒度是“来源 × 消费者”，不是一个全局开关。任一数据集失败不得阻断其他已通过数据集运行。
