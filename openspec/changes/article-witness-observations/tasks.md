# Tasks

## 1. ibkr_news 缺陷

- [ ] 1.1 `article-ingest` 在正文达到预算上限时的 `partial` 记为成功，并注明“预算内 n/m”。验证：单测覆盖预算取满时控制器不重试；对真实 `ibkr_news_daily` 观察一个窗口，账本为成功的 partial。
- [ ] 1.2 修 `persistent_queue.run_one` 超时分支，如实记录 `timeout` 与已等待时长。验证：单测模拟超时，记录原因为 `timeout`，无 `UnboundLocalError`。

## 2. 抽取入口

- [ ] 2.1 把 `collect_articles` 拆成“采集”和“抽取”两段，抽取段只读已入库文章版本（D1）。验证：单测断言抽取段不发起网络请求；既有文章抽取测试通过。
- [ ] 2.2 已抽取标记（文章 id + 版本 hash + 抽取器版本），以及预抽取过滤（实体与关键词，D4）；关键词表放配置。验证：单测覆盖重复运行跳过、正文改版重抽、不相关文章不调模型。
- [ ] 2.3 新增派生动作 `article-evidence-observe --source <id>`，加入 `_SAFE_DERIVED_ACTIONS` 与 partial-ok 清单，单次请求上限 20。验证：单测覆盖超上限时为 partial 成功并注明剩余数；`load_plan` 校验通过。
- [ ] 2.4 文档：`docs/data/` 中文章来源说明补“抽取任务、频率、预算”。验证：文档一致性测试通过。

## 3. 调度与回填

- [ ] 3.1 在 `schedules.yaml` 新增 `article_evidence_semianalysis_weekly`、`article_evidence_ibkr_news_daily`（D2）。验证：refresh/schedule 测试通过。
- [ ] 3.2 统计历史已入库文章的过滤命中数与预计请求数，报给用户确认后执行一次回填。验证：`SEMIANALYSIS`、`DOWJONES` 在 L5 的观测非空，每篇只抽取一次。
- [ ] 3.3 观察一次真实定时触发。验证：两个任务账本为成功（或成功的 partial）；L5 报告出现两个见证方的观测。
