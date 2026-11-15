# Proposal

## Why

三个文章类见证方 `DOWJONES`、`SEMIANALYSIS`、`TRENDFORCE` 自 8 月起不再产生观测：`DOWJONES` 最后一条在 2026-08-30，`SEMIANALYSIS` 与 `TRENDFORCE` 在 2026-08-18（历史观测分别为 296、130、280 条，均停在当时）。它们是第三方证人：

- `DOWJONES`、`SEMIANALYSIS` 只登记在 L5 的命题里；
- `TRENDFORCE` 登记在 **L4、L5、L6、L7、L8 五层**的命题里。

停产后，这些命题只能靠公司财报单边支撑，第三方立场缺席。

**原因：** 把文章变成观测的 `chain.articles.collect_articles` 自平台迁移后没有任何定时调用方。旧入口 `ats evidence articles` 退役后被指向 `ats data article-ingest`，而后者只把文章入库，不抽取观测。`ibkr_news_daily`、`semianalysis_newsletter_weekly`、`trendforce_news_weekly` 三个任务都在跑，但产出停在"文章已入库"。

**ibkr_news 自身的两个遗留缺陷也会影响这条链路：**
- 每个窗口都是 `partial`。正文请求预算只有 10 篇，而每次选中约 350 篇。控制器把 `partial` 当失败重试，队列却把 `partial` 当终态，第二次必然报 `queue_task_not_claimable`。（2026-11-13 核实：这一条已由 `f55e0c3`（10-21）修复——预算内的部分完成记为 `succeeded / partial_budget_deferred`，重试改用独立的队列引用；11-12 窗口即按此记账。2026-11-10 整窗失败**不是**此缺陷，而是 IBKR 主源 `ConnectionRefusedError` 与 Yahoo 备用源同时不可用的真实故障，记为失败是正确的。）
- 队列超时分支里变量未赋值，真实超时原因被掩盖成 `UnboundLocalError`。

（来源：switch-trade-path-and-retire-r5 tasks 11.3；2026-11-13 修正范围：补 TrendForce 与 L4–L8，更正"一条观测都没有"的描述。）

**下游依赖：** `event-impact-routing` 的整层风险命题主要靠这些第三方来源，依赖本 change 先完成。

## What Changes

- 新增派生任务：对已入库、尚未抽取的文章逐篇抽取中性观测，写入产业链证据。
  - 每篇文章的同一版本只抽取一次。
  - 每次运行有模型请求上限。
  - 只处理被声明为见证方的来源：`ibkr_news`（DOWJONES）、`semianalysis`（SEMIANALYSIS）、`trendforce_news`（TRENDFORCE）。
- 抽取频率跟随文章来源：IBKR 新闻每日，SemiAnalysis 与 TrendForce 每周；都在对应的文章入库任务之后运行。
- 修 ibkr_news 的两个缺陷：
  - 预算内正文取满、其余留待下一窗口的 `partial` 记为成功并注明原因，不再重试；
  - 队列超时分支如实记录超时原因。
- 抽取前过滤：只抽与**该来源被登记为证人的命题**相关的文章（按这些命题的见证实体、标的与关键词），避免对约 350 篇/天的新闻逐篇调用模型。过滤范围由 `ai_hardware.yaml` 的证人声明推导，覆盖 L4–L8，不写死某一层。
- 历史已入库文章做一次回填；回填前报预计请求数，经用户确认后执行。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `sector/chain-layers`：新增"文章类见证方必须由定时抽取产生观测"的要求，适用于所有登记了文章来源的层。
- `data/unstructured-ingestion`：新增"预算内完成的部分采集按成功记录、超时原因必须如实保留"的要求。

## Impact

- **代码：**
  - `src/ats/chain/articles.py`：拆分采集与抽取，抽取入口只读已入库文章；
  - `src/ats/data/refresh.py`：新增派生动作，加入 partial-ok 清单；
  - `src/ats/data/persistent_queue.py`：超时分支；
  - `src/ats/data/articles/ibkr_news.py`。
- **配置：** `config/data/schedules.yaml` 新增三个派生任务；`config/sectors/ai_hardware.yaml` 见证方声明不变；新增按来源的关键词表。
- **成本：** 新增模型请求，按每次运行上限控制（见 design）；回填一次性，执行前报数。
