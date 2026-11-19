# Tasks

## 1. ibkr_news 缺陷

- [x] 1.1 `article-ingest` 在正文达到预算上限时的 `partial` 记为成功，并注明“预算内 n/m”。验证：单测覆盖预算取满时控制器不重试；对真实 `ibkr_news_daily` 观察一个窗口，账本为成功的 partial。
  - 2026-11-13 核实：已由 `f55e0c3`（10-21）实现（`_article_partial_outcome` → `succeeded / partial_budget_deferred`，原因里带 deferred 数；重试用独立队列引用）；`tests/test_refresh_partial_articles.py` 7 项通过；真实窗口 11-12 记为 `succeeded / partial_budget_deferred`。11-10 的整窗失败是 IBKR 与 Yahoo 同时不可用的真实故障，不属本项。
- [x] 1.2 修 `persistent_queue.run_one` 超时分支，如实记录 `timeout` 与已等待时长。验证：单测模拟超时，记录原因为 `timeout`，无 `UnboundLocalError`。
  - 2026-11-13：`payload` 在启动前绑定；超时记 `worker_timeout:waited=<s>;limit=<s>`，结果含 `timeout`、`timeout_seconds`、`waited_seconds`。`tests/test_persistent_queue_failure_output.py` 新增超时用例，2 项通过。

## 2. 抽取入口

- [x] 2.1 把 `collect_articles` 拆成“采集”和“抽取”两段，抽取段只读已入库文章版本（D1）。验证：单测断言抽取段不发起网络请求；既有文章抽取测试通过。
- [x] 2.2 已抽取标记（文章 id + 版本 hash + 抽取器版本），以及预抽取过滤（D4）：过滤范围按来源从 `ai_hardware.yaml` 的证人声明推导（实体、`expect_from`、命题关键词），关键词表放配置。验证：单测覆盖重复运行跳过、正文改版重抽、不相关文章不调模型、TrendForce 的 L6 文章不因与 L5 无关被跳过。
- [x] 2.3 新增派生动作 `article-evidence-observe --source <id>`，加入 `_SAFE_DERIVED_ACTIONS` 与 partial-ok 清单，单次请求上限 20。验证：单测覆盖超上限时为 partial 成功并注明剩余数；`load_plan` 校验通过。
- [x] 2.4 文档：`docs/data/` 中文章来源说明补“抽取任务、频率、预算、过滤范围按证人声明推导”。验证：文档一致性测试通过。

  - 2026-11-13 实施记录（2.1–2.4）：
    - 2.1：采集段即 `ats data article-ingest`（已在跑）；新增抽取段 `chain.articles.observe_ingested_articles`，只读已入库文章版本，测试断言不调用 `discover`。旧的 `collect_articles`（自带发现与取正文）保留但无调用方，不再接入调度。
    - 2.2：已处理键沿用文档处理台账（文章版本 + `chain-observer-v1`），正文改版自动重读；不相关记在 `chain_prefilter` 台账，版本为过滤口径指纹，口径变了会重新检查。过滤规则与实测见 design D4。
    - 2.3：`ats data article-evidence-observe --source <id> [--since|--lookback-days] [--max-requests 20] [--dry-run]`；已加入 `_SAFE_DERIVED_ACTIONS` 与 `_PARTIAL_OK_DERIVED`，控制器把超上限的 partial 记为 `succeeded`，原因用命令给出的 `partial_budget_deferred: N ...`。
    - 测试：`tests/test_article_evidence_observe.py` 8 项；连同 refresh、队列、既有文章测试共 71 项通过。
    - 2.4：`docs/data/DATA_ARCHITECTURE.md` §12.3；`test_structured_docs_consistency` 通过。

## 3. 调度与回填

- [x] 3.1 在 `schedules.yaml` 新增 `article_evidence_ibkr_news_daily`、`article_evidence_semianalysis_weekly`、`article_evidence_trendforce_weekly`（D2）。验证：refresh/schedule 测试通过。
  - 2026-11-13：三个任务已加入 `derived.jobs`，`load_plan` 解析通过；**暂设 `enabled: false`**，回填（3.2）与上线（3.3）经用户批准后再打开，避免未经确认就在生产发模型请求。
- [x] 3.2 按来源统计 8 月停产以来已入库文章的过滤命中数与预计请求数，报给用户确认后执行一次回填。验证：`DOWJONES`、`SEMIANALYSIS` 在 L5、`TRENDFORCE` 在 L4–L8 出现 8 月之后的新观测，每篇只抽取一次。
  - 2026-11-13 执行（用户批准约 425 次请求）：起点为各证人最后一条观测之后（DOWJONES 08-31，TRENDFORCE / SEMIANALYSIS 08-19），每批 40 次、共约 426 次（含 1 次试跑；首批 40 次的输出文件在重跑时被覆盖，未计入下列合计）。
    - 结果：抽出 239 + 首批 24 篇，96 + 首批 16 篇判为"无可抽事实"（读一次即止），1 篇调用失败（留待定时任务重试）；新观测 DOWJONES 1631、TRENDFORCE 1347、SEMIANALYSIS 656 条。
    - 映射到命题维度的：DOWJONES → L5 138 条；SEMIANALYSIS → L5 145 条；TRENDFORCE → L4 54、L5 123、L6 266、L7 131、L8 49 条。
    - 同一文章版本被抽取超过一次的：0。
    - 过程修复：队列白名单补登 `article-evidence-observe`（首次启动被拒，未发请求）；"读过但无可抽事实"不再记为待重试（首批 16 篇行情快讯）。记录与输出在 `var/manual_fixes/article-backfill-20261113/`。
- [x] 3.3 观察一次真实定时触发。验证：三个任务账本为成功（或成功的 partial）；相关层的报告出现这三个见证方的新观测。
  - 2026-11-13（UTC 22:3x）`article_evidence_ibkr_news_daily` 首次真实运行：`succeeded / partial_budget_deferred: 2 relevant articles left for the next run (request cap 20)`，新增 DOWJONES 观测 115 条（7 条映射到命题维度）。TrendForce、SemiAnalysis 两个周任务待 11-16（周一）首次运行后再勾选。
  - 2026-11-19 核对，三个任务都已有真实定时运行，账本为成功或无新内容：
    - `article_evidence_trendforce_weekly` 11-16 `no_change / nothing_new_in_scope`；
    - `article_evidence_semianalysis_weekly` 11-16 `succeeded`，新观测 12 条；
    - `article_evidence_ibkr_news_daily` 11-13、11-14、11-16 `succeeded`（11-16 那次抽取 19 篇、143 条观测），11-15、11-17、11-18 `no_change`。
  - 层报告：11-14 的命题评估里，TRENDFORCE 是 L4–L8 的说话人，DOWJONES、SEMIANALYSIS 是 L5 的说话人。
  - 遗留（不在本 change 范围）：上游 `ibkr_news_daily` 在 11-14、11-15、11-17、11-18 以 `article_source_partial` 失败，11-16 之后没有新的道琼斯文章入库，所以 11-17、11-18 的证人任务无可抽内容。这是已登记的 ibkr_news 采集缺陷。
