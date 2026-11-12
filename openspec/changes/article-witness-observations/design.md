# Design

## Context

- **现有抽取逻辑：** `collect_articles` 内部完成“发现 → 过滤 → 取正文 → 抽取”全流程，并自己访问网络。采集现在已由 `article-ingest` 负责（入库、去重、版本）。如果再让抽取自己取一遍网络，会和入库重复请求。
- **刷新控制器：** 派生任务的命令必须是 `_SAFE_DERIVED_ACTIONS` 中的动作；`_PARTIAL_OK_DERIVED` 决定哪些动作的 `partial` 记为成功。
- **ibkr_news 规模：** 每日约选中 350 篇，正文预算 10 篇。

## Goals / Non-Goals

**Goals：**
- `SEMIANALYSIS`、`DOWJONES` 在 L5 产生可追溯观测，每篇文章只抽取一次。
- 抽取成本可控、可预估。

**Non-Goals：**
- 不新增文章来源。
- 不改 L5 命题内容。
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
- `article_evidence_semianalysis_weekly`：排在 `semianalysis_newsletter_weekly` 之后 2 小时。
- `article_evidence_ibkr_news_daily`：排在 `ibkr_news_daily` 之后 1 小时。

### D3. 已抽取标记

以“文章 id + 版本 hash + 抽取器版本”作为已处理键，记在证据库中。重复运行跳过已处理文章；正文改版（hash 变化）会再抽一次。

### D4. 预抽取过滤

在调用模型前用确定性规则过滤：
- 文章提及 L5 见证方声明的实体或 `config/pead.yaml` 中 L5 相关标的；
- 或命中 L5 命题关键词表。

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
2. 新增抽取动作，对历史已入库文章做一次手动回填。回填会消耗模型请求，执行前会先报预计请求数并征得用户确认。
3. 开启两个派生任务。

回滚：关闭两个任务即可；已写入的观测保留并可追溯。
