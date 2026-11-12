# Proposal

## Why

L5 的两个文章类见证方 `SEMIANALYSIS`、`DOWJONES` 至今一条观测都没有，相关命题只能靠公司财报单边支撑。

**原因：** 把文章变成观测的 `chain.articles.collect_articles` 没有任何定时调用方。旧入口 `ats evidence articles` 退役后被指向 `ats data article-ingest`，而后者只把文章入库，不抽取观测。`semianalysis_newsletter_weekly` 与 `ibkr_news_daily` 两个任务都在跑，但产出停在“文章已入库”。

**ibkr_news 自身的两个遗留缺陷也会影响这条链路：**
- 每个窗口都是 `partial`。正文请求预算只有 10 篇，而每次选中约 350 篇。控制器把 `partial` 当失败重试，队列却把 `partial` 当终态，第二次必然报 `queue_task_not_claimable`。
- 队列超时分支里变量未赋值，真实超时原因被掩盖成 `UnboundLocalError`。

（来源：switch-trade-path-and-retire-r5 tasks 11.3）

## What Changes

- 新增派生任务：对已入库、尚未抽取的文章逐篇抽取中性观测，写入产业链证据。
  - 每篇文章只抽取一次。
  - 每次运行有模型请求上限。
  - 只处理被声明为见证方的来源。
- 抽取频率跟随文章来源：SemiAnalysis 每周，IBKR 新闻每日；都在文章入库任务之后运行。
- 修 ibkr_news 的两个缺陷：
  - 预算内正文取满、其余留待下一窗口的 `partial` 记为成功并注明原因，不再重试；
  - 队列超时分支如实记录超时原因。
- 抽取前过滤：只抽与 L5 命题相关的文章（按标的、关键词与见证方声明），避免对约 350 篇/天的新闻逐篇调用模型。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `sector/chain-layers`：新增“文章类见证方必须由定时抽取产生观测”的要求。
- `data/unstructured-ingestion`：新增“预算内完成的部分采集按成功记录、超时原因必须如实保留”的要求。

## Impact

- **代码：**
  - `src/ats/chain/articles.py`：抽取入口改为只读已入库文章；
  - `src/ats/data/refresh.py`：新增派生动作，加入 partial-ok 清单；
  - `src/ats/data/persistent_queue.py`：超时分支；
  - `src/ats/data/articles/ibkr_news.py`。
- **配置：** `config/data/schedules.yaml` 新增派生任务；`config/sectors/ai_hardware.yaml` 见证方声明不变。
- **成本：** 新增模型请求，按每次运行上限控制（见 design）。
