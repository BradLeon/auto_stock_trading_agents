# Design

## Context

- **现有抽取逻辑：** `collect_articles` 内部完成“发现 → 过滤 → 取正文 → 抽取”全流程，并自己访问网络。采集现在已由 `article-ingest` 负责（入库、去重、版本）。如果再让抽取自己取一遍网络，会和入库重复请求。
- **刷新控制器：** 派生任务的命令必须是 `_SAFE_DERIVED_ACTIONS` 中的动作；`_PARTIAL_OK_DERIVED` 决定哪些动作的 `partial` 记为成功。
- **ibkr_news 规模：** 每日约选中 350 篇，正文预算 10 篇。

## Goals / Non-Goals

**Goals：**
- `DOWJONES`、`SEMIANALYSIS`、`TRENDFORCE` 在其登记为证人的各层（L4–L8）恢复产生可追溯观测，每篇文章的同一版本只抽取一次。
- 抽取成本可控、可预估。

**Non-Goals：**
- 不新增文章来源。
- 不改任何层的命题内容与证人声明。
- 不提高 ibkr_news 正文预算（另行评估）。

## Decisions

### D1. 抽取只读已入库文章，不再自己访问网络

把 `collect_articles` 拆成两段，派生任务只调用抽取段，输入是 `article-ingest` 已入库的文章版本。

理由：
- 一份文章只取一次网络；
- 抽取可以重放；
- 失败时能区分“没采到”和“没抽到”。

### D2. 新增派生动作 `article-evidence-observe`

参数为 `--source <article source id>`，每个来源一个任务：
- `article_evidence_ibkr_news_daily`：排在 `ibkr_news_daily` 之后 1 小时。
- `article_evidence_semianalysis_weekly`：排在 `semianalysis_newsletter_weekly` 之后 2 小时。
- `article_evidence_trendforce_weekly`：排在 `trendforce_news_weekly`（周一 10:00 UTC）之后 2 小时。

（2026-11-13 补 TrendForce：它是 L4–L8 五层的证人，原稿漏了。）

### D3. 已抽取标记

以“文章 id + 版本 hash + 抽取器版本”作为已处理键，记在证据库中。重复运行跳过已处理文章；正文改版（hash 变化）会再抽一次。

### D4. 预抽取过滤

在调用模型前用确定性规则过滤。过滤范围按**来源**推导，不写死某一层：先从 `config/sectors/ai_hardware.yaml` 找出该来源（DOWJONES / SEMIANALYSIS / TRENDFORCE）被登记为证人的全部命题，再取这些命题所在层的成员、命题的 `entities` 与 `expect_from` 实体，以及 `config/data/sources.yaml` 中该来源已有的关键词表（`match`）。文章满足以下任一条即进入抽取：
- 标题命中关键词（允许英文复数）；
- 标题点名了上述范围内的公司，且正文开头 3000 字命中关键词。

关键词表为空表示全读（SemiAnalysis）。例如 TrendForce 的过滤范围覆盖 L4–L8，DOWJONES 与 SEMIANALYSIS 目前只覆盖 L5；以后在别的层登记这些来源为证人，过滤范围自动跟着扩大。

**为什么不是"提到实体或命中关键词"（2026-11-13 实测，IBKR 一周 160 篇）：** 入库时已按标的挑选，"文中提到范围内公司"160/160 全中，等于不过滤；"开头命中关键词"153/160，`order`、`supply` 这类词到处都是；只看标题关键词（8 月 `_wanted` 的口径）14/160，会漏掉"Jensen Huang Affair Revives Push for Chip Restrictions"这类证人最该读的报道。采用的规则为 115/160，约每天 16 篇，在每次运行 20 次请求的上限之内；TrendForce 8 月以来 118 篇中 117 篇入选，SemiAnalysis 全读。

未命中的文章记为“已检查、不相关”，同样只检查一次。

**预算：** 每次运行最多 20 个模型请求，超出的留到下一次，运行结果记为 `partial`（成功），并注明剩余数量。

### D5. ibkr_news 的两个缺陷

- `article-ingest` 的 `partial` 在正文达到预算上限时属于预期结果：加入 partial-ok 判定，记为成功，并在原因里写明“预算内 10/350”。
- `persistent_queue.run_one` 的超时分支先给 `payload` 赋值，记录 `timeout` 和已等待时长，不再抛出 `UnboundLocalError`。

## Risks / Trade-offs

- [新闻相关性过滤过宽，请求量上升] → 每次运行的请求上限兜底；先观察一周的命中率再调关键词。
- [过滤过严，漏掉相关文章] → “不相关”的判定可回看；关键词表放在配置里，可随时调整。
- [模型把新闻里的二手说法当成事实] → 沿用现有中性观测规则：说话人记为文章来源，stance 为 regulator，不提升为公司自述。

## Migration Plan

1. 先修 ibkr_news 两个缺陷并观察一个窗口。
2. 新增抽取动作，对历史已入库文章（三个来源，自 8 月停产起）做一次手动回填。回填会消耗模型请求，执行前会先按来源报过滤命中数与预计请求数，征得用户确认。
3. 开启三个派生任务。

回滚：关闭三个任务即可；已写入的观测保留并可追溯。
