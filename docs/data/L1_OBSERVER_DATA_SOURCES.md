# L1 Observer 数据源总览

> 读者：发布负责人、Evidence 审阅者、数据源接入开发者
> 范围：`ai_hardware` sector 的 `L1_app` 层全部已启用的只读 Evidence Observer，及其消费的全部**结构化**来源与数据集。不含 `unstructured.yaml` / `news_sources.yaml` / `sources.yaml` 三个注册表——它们服务 Chain 与 PEAD 路径，理由与核验见第一节「范围边界」。
> 核验基准：本文件的规模、期间与状态数字取自本机 `var/data.sqlite`（统一结构化仓库）与 `var/structured_data/releases.yaml`，**观测时点 2026-09-18**。它们是时点快照，会随采集推进而变化；方法、口径与治理规则则以 `config/data/structured.yaml`、`config/data/schedules.yaml`、`config/sectors/ai_hardware.yaml` 为真源。

---

## 一、L1 Observer 与数据源的分工

`config/sectors/ai_hardware.yaml` 的 `L1_app` 当前声明 **3 个独立 Observer**。三者分开运行、分开出报告、分开失败，互不改写对方命题，且都不进入 Chain、评分、组合、风控或交易路径。

| Observer（claim_id） | 版本 | runner | 回答的问题 | 消费来源 | 数据集 |
|---|---|---|---|---|---|
| `ai_core_production_workflow_penetration` | v2 | `ai_production_penetration` | 能力是否进入持续运行的生产工作流并扩散 | `us_census_btos`、`rps_genai_adoption`、`anthropic_economic_index`，补充 `ramp_ai_index` | `ai_enterprise_adoption_us`、`ai_worker_adoption_us`、`ai_work_adoption`、`ramp_ai_adoption`、`ramp_ai_spend` |
| `ai_frontier_labs_commercialization` | v1 | `ai_commercialization` | 使用是否转化为可持续收入 | `sacra_public_company_profiles`、`tickertrends_public_research`，补充 `openrouter_rankings` | `frontier_ai_labs_revenue`、`openrouter_rankings_daily` |
| `ai_frontier_raw_capability` | v1 | `ai_raw_capability` | 模型原始能力前沿是否外扩 | `frontier_ai_capability`、`frontier_ai_capability_official_lab` | `frontier_ai_capability_benchmarks` |

### 范围边界：本文件为什么只覆盖 structured 域

统一入口 `config/data/catalog.yaml` 把数据层切成五个注册表：`structured`、`unstructured`、`sources`（evidence legacy overlay）、`news_sources`、`schedules`。**本文件只覆盖 `structured.yaml`**，因为 L1 的三个 Observer 经 DataProducts 只读统一结构化仓库，不读非结构化语料。

这一点已逐项核验（2026-09-18）：

- `evidence_observers` 在整个 `config/` 树中**只出现一次**——`ai_hardware.yaml` 的 `L1_app`；L2–L8 均无 Evidence Observer。
- 上文九个来源在 `sources.yaml`、`news_sources.yaml`、`unstructured.yaml` 中的命中数**全部为 0**。
- `L1_app` 段落（43–460 行）**没有 `claims:` 块**——L1 的 Chain 命题仍是注释里的草案，故当前不存在消费非结构化数据的 L1 Chain 路径。`claims:` 从 L2_cloud 起才出现。

另外三个注册表服务的是**别的路径**，与 L1 Evidence 无关：

| 注册表 | 条目 | 消费方 |
|---|---|---|
| `sources.yaml`#`sources` | `kr_semiconductor_exports`、`tw_ic_exports`、`dram_contract_price` | `structured.yaml` 的 `chain.persistent_datasets`（`regional_kr_exports`、`regional_tw_exports`、`industry_dram_contract_price`） |
| `unstructured.yaml` | `factset_earnings_insight_doc`、`trendforce_news`、`semianalysis`、`ibkr_news`、`yfinance_live_news` | 文章/研报采矿；其中 `ibkr_news` + `yfinance_live_news` 属 PEAD 新闻路径（`news_sources.yaml` 首行明写「for the continuous PEAD monitor」），`trendforce_news` 另派生 `industry_dram_contract_price` 供 Chain |
| `news_sources.yaml` | RSS / IMAP 订阅 / X 账号 / 关键词表 | PEAD 连续监控，与 Evidence 层无关 |

`schedules.yaml` 的 `unstructured:` 段目前**只有 `trigger` / `command` / `default_timezone`，没有任何 `jobs`**——即非结构化来源已登记但尚无周期调度。

**划分依据是入库契约，不是上游形态**：`sacra_public_company_profiles` 与 `tickertrends_public_research` 的上游分别是公开网页与公开研究文章，但都按 structured 域适配器纳入结构化 observation 仓库，因此在本文件范围内。「网页/文章 = 非结构化」是错误直觉。

> 若日后 L1 激活 Chain claims，或某个 Observer 开始消费文章语料，本文件的范围必须重新评估。

### 生效发布模式（`source_mode` 解析结果）

模式解析顺序为：环境变量覆盖 → 可变发布覆盖层 `var/structured_data/releases.yaml` → 签入基线 `config/data/structured.yaml` 的 `feature_flags` → catalog 默认值。

| 来源 | 覆盖层 | 生效模式 |
|---|---|---|
| `anthropic_economic_index` | `platform`（2026-09-06） | `platform` |
| `ramp_ai_index` | `platform`（2026-09-11） | `platform` |
| `ramp_ai_index_fred` | — | `platform`（签入基线，2026-11-12 新增） |
| `frontier_ai_capability` | `platform`（2026-09-18） | `platform` |
| `sacra_public_company_profiles` | — | `platform`（签入基线） |
| `tickertrends_public_research` | — | `platform`（签入基线） |
| `openrouter_rankings` | — | `platform`（签入基线） |
| `frontier_ai_capability_official_lab` | — | `platform`（签入基线） |
| `us_census_btos` | — | `platform`（签入基线，2026-09-19 由 `legacy` 提升） |
| `rps_genai_adoption` | — | `platform`（签入基线，2026-09-19 由 `legacy` 提升） |

2026-09-19 起，**L1 三个 Observer 消费的全部来源的生效模式都已是 `platform`**。此前 BTOS 与 RPS 停留在 `legacy`——它们没有覆盖层条目，因此生效值等于签入基线，改动基线即生效，不需要走发布覆盖层。这次调整同时消除了签入基线与本文件、`STRUCTURED_DATA_OPERATIONS.md` §10.2 之间的不一致。

模式门的作用范围是精确的，不应扩大理解：

- **约束手动采集**：`ingest_source()` 在模式不属于 `{shadow, platform, fallback}` 时抛 `PermissionError`，需显式 `--force` 才能隔离运行。
- **不约束定时探测**：`release_check(..., ingest_new=True)` 的探测→入库交接路径以 `force=True` 调用采集，因此 `legacy` 来源的周期任务照常写入受治理仓库（这也是 `us_census_btos` 在 2026-09-10 有成功入库记录的原因）。
- **不构成读取门**：Observer 经 DataProducts 读取仓库，不按来源模式过滤。

---

## 二、来源总表：获取方式、更新方式、规模、时效

### 2.1 获取方式与更新方式

| 来源 | 提供方与入口 | 取数机制 | 鉴权/成本 | 更新节奏 | 调度任务 |
|---|---|---|---|---|---|
| `us_census_btos` | 美国普查局 BTOS Core API 与历史下载<br>`census.gov/hfp/btos/api`、`/data_downloads` | HTTP 拉取官方 API 与历史文件；只接受 2025-11-17 起「any business function」新口径 | 免费、免鉴权 | 双周（调查波次） | `us_census_btos_weekly`（每周五） |
| `rps_genai_adoption` | Real-Time Population Survey via FRED<br>`fredgraph.csv?id=`、`graph/api/series/` | 5 条白名单 FRED 系列的公开 CSV + 系列元数据交叉校验 | 免费、免鉴权 | 季度 | `rps_genai_adoption_weekly`（每周五） |
| `anthropic_economic_index` | Anthropic Economic Index<br>`huggingface.co/datasets/Anthropic/EconomicIndex` | 先解析 HF 仓库解析到 commit SHA，再**按 commit 固定**下载；只持久化确定性 Global SOC/O*NET 查询切片 | 免费、免鉴权 | release_event（不定期发布） | `anthropic_economic_index_weekly`（每周五） |
| `ramp_ai_index_fred` | FRED 上的 Ramp AI Index 序列（release rid=23）<br>`fredgraph.csv?id=RAMPAI*` | 14 条月度序列的公开 CSV + 序列元数据校验；纯 HTTP | 免费、免鉴权 | 月度 | `ramp_ai_index_fred_weekly`（每周五） |
| `ramp_ai_index` | Ramp AI Index 公共图表<br>`ramp.com/data/ai-index` | 只采 FRED 没有的部分：无头浏览器 runner 点官方 `Get the data`，截获剪贴板原文并落盘到 `var/data/ramp_exports/<scope>.tsv`；**不用截图/OCR/猜隐藏 URL** | 免费、免账户 | 月度（token 份额为周度） | `ramp_ai_index_p10d_probe`（每 10 天） |
| `sacra_public_company_profiles` | Sacra 公开公司页<br>`sacra.com/c/openai/`、`sacra.com/c/anthropic/` | 每轮每页**只探测一次**，原始 HTML 存为 constrained snapshot；后续解析/入库/报告离线复用该 artifact。静态 HTML 缺 Revenue 段时允许单次受控 browser snapshot 回退 | 免费、免登录；**明令禁止**付费 API 与 MCP | 每 7 天 | `frontier_ai_labs_revenue_p7d_refresh` |
| `tickertrends_public_research` | TickerTrends 公开研究文<br>`blog.tickertrends.io/api/v1/posts/anthropic-vs-openai-arr-tracking` | 每周期一次公开 Substack post API；判变依据是准入数值的**语义指纹**而非页面字节（`body_html` 每次重新序列化）。离线路线仍保留冻结 seed `tests/fixtures/.../tickertrends_anthropic_vs_openai_arr_tracking.json` | 免费、免鉴权；**禁止**付费 API 与 MCP | 每 7 天（与 Sacra 同一研究对象，共用发现组） | `frontier_ai_labs_revenue_p7d_refresh`（每 7 天，组 `frontier_ai_labs_revenue`） |
| `openrouter_rankings` | OpenRouter Rankings Data API<br>`openrouter.ai/api/v1/datasets/rankings-daily` | 文档化的每日 token 数据集；14 天重叠窗口回补；不抓页面、不推 request share | 需免费 bearer key `OPENROUTER_API_KEY`；数据许可 CC BY 4.0 | 上游日更，**我方每 7 天**（2026-09-19 起） | `openrouter_rankings_p7d_ingest`（每 7 天） |
| `frontier_ai_capability` | LiveBench、Terminal-Bench 4.0、Terminal-Bench-Science 0.1、OSWorld 2.0、AutomationBench、SciCode、CritPt、MMMU-Pro、Toolathlon Verified、SpreadsheetBench 2、Humanity's Last Exam 的公开 Git/CSV/JSON/HTML | 一轮批量探测 11 条公开路线，再本地解析；Git 记 commit/blob SHA，JSON/HTML 记 ETag/Last-Modified、结构指纹与 payload hash；无条件请求（etag/last_modified/git_sha/payload_sha256） | 免费；AA 付费 API **非必需**；公开结构化页面需双门禁 fail-closed | 事件驱动，**我方每 7 天**（2026-09-19 起，取消热度分级） | `..._model_discovery_p7d`、`..._benchmark_probe_p7d`、`..._official_release_probe_p7d`、`..._full_audit_p7d` |
| `frontier_ai_capability_official_lab` | OpenAI/Anthropic/Google/xAI/DeepSeek/Moonshot/Tencent/Z.ai/Alibaba 官方发布与 model card | 只发现官方自报身份与分数，**永不覆盖**第三方观察 | 免费 | 事件驱动，**我方每 7 天**（2026-09-19 起） | `..._official_release_probe_p7d` |

**统一约束**：L1 的全部来源都不使用付费 API、付费订阅或 MCP 连接器；唯一需要凭据的是 OpenRouter 的免费 bearer key。`sacra_public_company_profiles` 与 `tickertrends_public_research` 在 catalog 中显式声明 `paid_api_allowed: false`、`mcp_allowed: false`。

### 2.2 实际入库规模（2026-09-18 时点）

| 来源 / 数据集 | 观察数 | 序列数 | 实体数 | 指标数 | 期间数 | 覆盖窗口 | 最近入库 |
|---|---:|---:|---:|---:|---:|---|---|
| `anthropic_economic_index` / `ai_work_adoption` | 153,218 | 92,417 | 22,820 | 13 | 3 | 2026-04 … 2026-06-26 | 2026-09-10 |
| `us_census_btos` / `ai_enterprise_adoption_us` | 39,281 | 1,732 | 269 | 5 | 20 | 波次 88 … 107 | 2026-09-10 |
| `openrouter_rankings` / `openrouter_rankings_daily` | 32,334 | 31,620 | 409 | 1 | 620 | 2025-01-01 … 2026-09-14 | 2026-09-15 |
| `ramp_ai_index` / `ramp_ai_adoption` | 1,264 | 1,264 | 14 | 4 | 44 | 2023-01 … 2026-08 | 2026-09-11 |
| `ramp_ai_index` / `ramp_ai_spend` | 542 | 542 | 56 | 2 | 36 | 2023-09 … 2026-08 | 2026-09-11 |
| `frontier_ai_capability(+_official_lab)` / `frontier_ai_capability_benchmarks` | 245 | 245 | 154 | 1 | 44 | 2025-10-28 … 2026-09-18 | 2026-09-18 |
| `rps_genai_adoption` / `ai_worker_adoption_us` | 38 | 5 | 1 | 5 | 8 | 2024-Q3 … 2026-Q2 | 2026-09-10 |
| `sacra_public_company_profiles` / `frontier_ai_labs_revenue` | 8 | 5 | 2 | 3 | 7 | 2025-12 … 2030（含前瞻期） | 2026-09-18 |
| `tickertrends_public_research` / `frontier_ai_labs_revenue` | 5 | 2 | 2 | 1 | 4 | 2026-01 … 2026-06 | 2026-09-18 |

**整体量级**：L1 九个来源合计约 **226,935** 条观察，占全库 251,177 条的 **90.4%**；但规模高度集中在两个明细型来源——Anthropic Economic Index（15.3 万）与 OpenRouter（3.2 万）合计占 L1 的 80%+。其余七个来源合计不足 4 万条，其中收入类仅 13 条。

**原始 artifact**：L1 来源共 70 个 artifact、约 80 MB 原始字节，其中 Anthropic Economic Index 一项占 52 MB、BTOS 占 15.7 MB。全库 503 个 artifact、0.13 GB blob。

### 2.3 时效性

| 来源 | 天然频率 | 探测频率 | 配置的新鲜度约束 | 当前最新可用期 |
|---|---|---|---|---|
| `openrouter_rankings` | 日 | 每 7 天 08:00 UTC | `freshness_slo_days: 10`（2026-09-19 由 3 放宽） | 2026-09-14 |
| `frontier_ai_capability` | 事件驱动（新模型/新成绩） | 每 7 天；4 个 job 分别 07:20 / 07:25 / 07:27 / 07:35 UTC；**热度分级已取消** | — | 2026-09-18 |
| `sacra_public_company_profiles` | 不定期更新页面 | 每 7 天 07:50 UTC | `freshness_hours_max: 336`（14 天） | 2026-07（月）+ 2026-Q2 |
| `ramp_ai_index_fred` | 月 | 每周五 08:25 UTC（`ramp_ai_index_fred_weekly`） | `freshness_slo_days: 10` | 见 FRED |
| `ramp_ai_index` | 月（token 份额为周） | 每 10 天 07:30 UTC | `freshness_slo_days: 35`（过期报 `export_stale`） | v1 停在 2026-08；v2 首采后更新 |
| `us_census_btos` | 双周 | 每周五 08:10 UTC（`us_census_btos_weekly`） | `freshness_slo_days: 10` | 参考窗至 2026-08-23 |
| `anthropic_economic_index` | 不定期发布 | 每周五 08:20 UTC（`anthropic_economic_index_weekly`） | `freshness_slo_days: 10` | 2026-06-26 |
| `rps_genai_adoption` | 季度 | 每周五 08:15 UTC（`rps_genai_adoption_weekly`） | `freshness_slo_days: 10` | 2026-Q2 |
| `tickertrends_public_research` | 不定期更新文章 | 每 7 天 07:50 UTC（与 Sacra 同组） | — | 2026-06 |

> 2026-11-12：组任务 `ai_adoption_weekly_release_check` 在 2026-10-03 改用 launchd 控制器时未迁移（新格式要求逐源声明），BTOS/RPS/AEI 自 2026-09-10 起再无定时采集；现按来源拆为三个周任务补回。

时效跨度极大：最高频（OpenRouter 与 frontier 能力均为 7 天）与最低频（RPS 季度）相差约 13 倍。**证据层不做跨频率插值或前向填充**，期间不足即返回 `insufficient_history`，因此低频率来源天然只能支撑较弱的趋势结论。

> **上游频率 ≠ 我方调度频率**。OpenRouter 与多个 benchmark 路线是日更的，但我方统一按 7 天采集：这让所有 L1 来源的节奏彼此可比，也把上游压力降到原来的约 1/7；代价是新 token 周与新 benchmark 成绩最多延迟 7 天才进入仓库。OpenRouter 因此保留 14 天重叠窗口回补，7 天节奏下不会漏期。

---

## 三、逐来源细化

### 3.1 `us_census_btos` — 美国企业 AI 采用广度

- **观察对象**：美国雇主企业的 AI 使用比例，含行业（NAICS）与规模（EMP）拆分。
- **准确性保障**：只接受 2025-11-17 之后的新问答口径（`any_business_function_v2`），问题指纹与起始期双重校验；`answer_sum_tolerance_pp: 0.15` 校验选项占比合计；标准误非负校验；`exclude_geographies: [state, msa]` 排除州与大都市区口径。
- **必须注意**：`period` 是**调查波次编号**，历史排序必须按整数波次或真实参考窗口，**不能按字符串排序**（否则 100–107 会排到 88–99 之前）；图表横轴以参考期结束日为主、`Wxx` 为辅助标注。
- **口径断点**：2025-11 问题措辞变更，前后必须切断序列，不得把方法变化读成基本面变化。
- **时效性**：天然双周；最近一次探测 `succeeded`，参考窗至 2026-08-23。

### 3.2 `rps_genai_adoption` — 美国在职成人工作使用

- **观察对象**：工作场景的 GenAI 使用率与持续性代理。5 条白名单系列：`RPSGENAIUSAGESHAREWORK`、`...LWWORK`、`...EDLWWOR`、`RPSGENAIASSISTWRKHRSALL`、`RPSGENAITSALL`。
- **准确性保障**：系列 ID 白名单（范围外直接 `ValueError`）；CSV 表头与系列列存在性校验；百分比值域校验；频率漂移检测；期内重复检测；FRED 元数据与预置期望值逐字段比对（`frequency`/`season`/`units` 漂移即报错）；`work_only_series: true` 与 `daily_lte_last_week_lte_adoption` 单调性检查。
- **必须注意**：这是**自报使用**，不能直接证明企业正式部署，也不能当作企业采用率；季度频率 + 仅 38 个观察点，是三条主轴里最弱的一轴。
- **时效性**：最近一次探测 `succeeded`，最新期 2026-Q2（季度值在季度结束后才有）。

### 3.3 `anthropic_economic_index` — Claude 任务生产化

- **观察对象**：SOC 职业与 O\*NET 任务两个视角下的 Claude 1P API / Claude.ai 流量结构。
- **准确性保障**：先解析 HF 仓库到 commit SHA，**所有下载按 commit 固定**，确保一次发布内文件一致；Job Explorer UI 被明确排除，不作为输入；只持久化确定性 Global 切片；`percentage_sum_tolerance_pp: 0.15` 校验占比合计；隐私过滤缺失记为 `not_published_or_privacy_filtered`，**不转成零**。
- **当前实际覆盖缺口（需关注）**：最近三次运行均记录两条**警告级**原因码——`task_relation_coverage_below_threshold:1p_api:0.7845` 与 `:claude_ai:0.7885`，即 O\*NET 任务到 taxonomy 关系的覆盖率约 78.5%，低于代码内 0.99 的期望线；同时约 2.09 万个未映射 task id 被记录。该缺口是警告而非失败，观察照常入库、四条核心序列仍可用，影响面限于**职业任务覆盖与 Figure 4 式分布等补充指标**；未映射任务不会被擅自分配到职业，因此该指标只是"公开数据能确认的覆盖"下界。
- **必须注意**：`Usage Share` 是某职业/任务占 1P API 公开产品流量的份额，**不是该职业有多少人在使用 AI**；官网所称 Industry 是 SOC 职业大类，不是 NAICS/GICS 行业；职业与任务是同一流量的两种切法，不可相加或取平均。
- **时效性**：不定期发布，库内仅 3 期且最新为 `2026-06-26`；对趋势而言必然落在 `insufficient_history`。

### 3.4 `ramp_ai_index_fred` + `ramp_ai_index` — 真实付费采购（补充轴，v2）

- **观察对象（v2，2026-11-12 起）**：七个 scope——`adoption_overall`、`adoption_overall_models`、`adoption_sector`、`adoption_business_size`、`spend_per_employee_overall`、`token_spend_share_by_maker`、`token_spend_share_by_model`。
  - 总体、3 家供应商、7 个行业、3 档规模来自 FRED；其余来自网页 runner。
  - v1 的 `model_market_share_overall` 已停止更新，由周度 token 花费份额取代。
  - 详见 [RAMP_AI_INDEX.md](RAMP_AI_INDEX.md)。
- **准确性保障**：
  - 网页部分只用官方 `Get the data` 导出的原文，不使用截图/OCR/猜测隐藏 URL；FRED 已有的序列不从网页入库，只做交叉核对。
  - `geographies` 等图表仅做发现。
  - 质量门：百分比值域 `0–100`；`spend_nonnegative`；分位数顺序固定为 `median, top10, top1`；`vendor_shares_may_overlap: true` 说明 vendor 份额不要求合计 100%；token 份额拒收 Volumes 指数。
  - v1 与 v2 是不同序列，读取方默认只读 v2。
  - 网页部分失败（被拦、没装浏览器、35 天无新数据）时只标记网页部分，FRED 部分照常更新。
- **必须注意**：样本只覆盖 **Ramp 客户**，会漏掉免费、捆绑与个人账户使用，因此不能读成全市场采用率；`model share` 不是全 Ramp 企业采用率；公开导出**没有 Top 30% 字段**，不得把 Top 1% 改名或插值；浏览器导出失败会走 `export_unreadable`（历史上确曾出现）。
- **边界**：Ramp 是 `ai_paid_business_adoption_diffusion` 这一**独立补充命题**的证据，不改变三轴主命题状态；`reference_period`（图表所属月份）与 `fetched_at`（探测时间）**不得混用**。

### 3.5 `sacra_public_company_profiles` + `tickertrends_public_research` — 前沿 Labs 收入

两个来源共用一个数据集，分工是刻意的：

- **Sacra = 周期性主信源**：每轮每页只探测一次，原始 HTML 落 artifact 后离线解析；付费墙单元格由上游服务端脱敏为 `—`，**缺失记为覆盖缺口，绝不记为 0 收入**。
- **TickerTrends = 同一研究对象的第二信息源，与 Sacra 同节奏**：只接受参考期落在 `2026-01-01..2026-06-30` 的 OpenAI/Anthropic 数值，`chart_derived_values_allowed: false`（禁止图形目测值），统一标记 `third_party_estimate`。每 7 天复核**一次**公开 Substack post API，判变依据是准入数值的**语义指纹**而非页面字节——`body_html` 每次都会被重新序列化，字节哈希会把稳态误报成新 release 并重设主序列基线。语义不变即 `no_change`。离线路线（`tests/fixtures/.../tickertrends_anthropic_vs_openai_arr_tracking.json`）仍然保留，供测试与受治理重放固定 2026H1 历史。

**口径治理**（本数据集最复杂的一环）：四类指标 `reported_arr` / `annualized_revenue_run_rate` / `trailing_revenue` / `forward_revenue_projection` 与四类观察身份 `company_reported` / `media_reported` / `third_party_estimate` / `projection` 严格区分；`forbid_cross_entity_aggregation`、`forbid_projection_in_historical_actual`、`forbid_company_plus_product_summing` 三条禁令在质量门内强制。数值比较要求 metric identity、observation identity、methodology regime 三者一致，否则只能并列展示。

**数值保障**：正数、币种、参考期、metric/observation identity、引用链（publisher/origin）缺一即隔离；跨来源同期间差异超过 **10%** 写入 `source_conflict` 警报，headline 按 identity→source→known_at 优先级选取，**候选一律不删除、估算不升级为公司披露**；`reconciliation_relative_tolerance: 1e-9`；`freshness_hours_max: 336`。

**来源分列**（2026-11-26 起）：趋势单元的键含来源，TickerTrends、Sacra 与公司文件各成一条序列，不再跨来源连线（此前 Anthropic 2026-06→07 出现过换估算机构造成的假回落）。另有一项参考期推断：OpenAI 的 `2025-12 = $20B` 由原句 "up from $20B at the end of 2025" 推断得出，来源并未显式给出 12 月数值。

**时效性**：Sacra 每 7 天探测（`freshness_hours_max: 336`）；TickerTrends 无周期。库内收入观察共 13 条（Sacra 8 + TickerTrends 5），其中 Sacra 的 `2028`、`2030` 属于前瞻期，**不得混入历史实际**。

### 3.6 `openrouter_rankings` — 公共路由 token 用量（补充轴）

- **观察对象**：OpenRouter 公共路由的每日 token 用量，按模型与作者拆分，`Other` 为一等来源桶。
- **准确性保障**：只消费文档化的每日数据集，**不抓排行榜页面**；`token_nonnegative_integer`；`daily_model_identity_unique`；`top_n_plus_other`（top 50 + Other）；作者别名表显式版本化，未知作者保持 unknown；派生指标（`model_tokens`、`author_tokens`、`token_share`、`concentration_top_n`、`concentration_hhi`）**仅在完整 UTC 周上进入趋势产品**。
- **必须注意**：只代表 OpenRouter 自身，**不是全市场**；token 不是请求数、客户数或收入——catalog 明确 `revenue_conversion: forbidden`、`request_share: unavailable`；`public_routed_scope_only`。唯一允许的"用量 × 价格"产品是 3.6.1 的估算花费份额。
- **时效性**：日频，含 14 天重叠窗口回补；`freshness_slo_days: 3`；最近入库 2026-09-15，最新数据期 2026-09-14。
- **版本与 `no_change`**：写入前，每行与同一模型、同一日期的最新版本比较；只有抓取时间或请求窗口（`raw.meta`）不同记 `unchanged`，整批如此记 `no_change`。token、来源行字段、模型名、厂商归属（含别名表变化）、排名、免费路由标记、方法口径或质量状态变化才追加版本，改回旧值也追加。当前视图的 OpenRouter 产品对同一模型、日期、方法口径只取最新一行；通用序列查询仍会按序列分别返回修订前后的行。没变化的日子保留首次写入时间和当时的 `as_of`；每次抓取仍归档原始载荷并写准入记录。

### 3.6.1 `openrouter_pricing` — 模型标价与估算花费份额

L1 观察器不读这个来源；它供 ANT 观测使用（`openrouter_model_price_series`、`openrouter_estimated_spend_share_series`）。

**价格怎么采**

- 来源是公开模型列表 `openrouter.ai/api/v1/models`，免鉴权，每天 07:45 UTC 一次。
- 每个模型存输入、输出两条单价，单位 USD / 百万 token，按日期保存；同一天价格变化追加修订版本。
- 只有当天的价格可取，**历史从首次采集开始**，漏掉的日子补不回来。
- 不记价格的条目：指向别名的条目；价格为 -1 的路由类条目（价格取决于它选中的模型）；价格条件无法折成单一单价的条目。
- 分时定价（按星期和钟点）：按各时段时长折成一周平均价。列表顶层价格是请求那一刻的时段价，不直接用。
- 按提示长度分档：用最短提示那一档。
- 免费路由（`:free`）记价格 0。
- 缓存读取价随输入价一起留存（`cache_read_price`），估算目前不使用它。

**列表价代表什么**

- 是模型默认端点（通常为原厂）当时的标价，含正在进行的折扣；折扣结束会表现为涨价。
- 开源权重模型有几十家服务商，价格相差可达十倍以上。2026-11-27 抽查 DeepSeek V4.1 Flash 1017：原厂输入价 0.30，网页首屏显示的最低价是 0.036。

**估算花费份额怎么算**

- 逐日：某模型当日 token × (输入占比 × 输入价 + 输出占比 × 输出价)。输入占比是声明的假设，写在 `config/data/structured.yaml` 的 `openrouter_model_pricing_daily.quality.estimated_spend.input_token_share`，每个结果原样披露。
- 当日价格 = 该日或之前最近一次快照，最多沿用 `max_snapshot_gap_days`（2）天；**从不使用之后的价格**。
- 只发布完整 UTC 周；周内任何一天没有可用快照，整周返回"无价格数据"并列出缺价日期。价格采集开始前的周同样如此。
- 份额 = 作者估算花费 ÷ 当周全部有价格用量的估算花费。`Other` 桶和列表里查不到的模型进"无价格"桶，不进分母，结果披露它占的 token 比例；超过 20% 带可靠性警告。
- 每周另披露免费路由、分时定价、分档定价模型各占多少 token。结果不给金额。

**为什么它不是收入**

- 排行数据不区分输入与输出 token，比例是假设。
- 标价不是实付价：缓存读写折扣、批量价、长提示加价、自带密钥和企业折扣都没有计入。差距很大：2026-11-27 模型网页上的实付输入均价，Claude Opus 5.5 为 0.63（标价 4），GPT-6.2 Astra 为 0.90（标价 8），主要因为缓存读取占大头。
- 同一模型经不同服务商路由价格可以不同，列表只给一个价。
- 只覆盖 OpenRouter 公共路由，不含直连各厂商接口、订阅和企业合同。
- 因此只看同一口径下的**趋势和拐点**；不当作绝对份额，不称为收入、实际支出或市场份额。

### 3.7 `frontier_ai_capability` + `..._official_lab` — 原始能力前沿

- **观察对象**：11 个 benchmark × 9 家 Lab 的固定面板，只回答两件事——同一可比组内 global frontier 是否外扩（A）、是否跨过 50% 多数任务门槛（B）。
- **准确性保障**：优先 benchmark 维护方/独立第三方的免费公开结果；`public_maintainer_preferred: true`，来源优先级为 `benchmark_maintainer_or_independent_third_party` → `competitor_reported` → `lab_self_reported`；每条结果带 `comparability_group`（同 benchmark 方法与 harness）与 `measurement_scopes`（`model_capability_proxy` vs `model_agent_stack_capability`，两类不得合并）；A 判定要求 95% 置信下界 > `max(2pp, 0.2 × historical_sd)`；模型/方法元数据由确定性 methodology fingerprint 版本化；分数是不可变 vintage，修订只追加不覆盖。
- **必须注意**：`NA` 是显式状态（`not_evaluated` / `pending_publication` / `not_self_reported` / `not_applicable` / `non_comparable` / `source_unavailable` / `withdrawn`），**不得当零分**；不同 task set、harness、grader 或 metric semantic 的分数进事件账本（AutomationBench、OSWorld 2.0、Toolathlon Verified、SpreadsheetBench 2），**不得混入统一矩阵或横向排名**；不同 Lab 的近似版本不能填充精确旗舰列。
- **权限现实**：Artificial Analysis 付费 API 从未作为前置条件；2026-09-17 实测其 `/api/v2/language/models` 在免费计划下返回 HTTP 403（`requires a Pro subscription`）。公开结构化页面的使用需同时满足 `ATS_FRONTIER_AI_ALLOW_PUBLIC_STRUCTURED_PAGES` 与 `ATS_FRONTIER_AI_PUBLIC_TERMS_APPROVED` 两个门禁，任一关闭即 fail-closed；禁止浏览器视觉抓取，禁止绕过登录或付费墙。`fixture=true` 或 synthetic 数据只能用于解析测试，禁止进入 platform。
- **当前状态**：最近一次探测为 `unreachable`（`no_entitled_or_allowed_capability_slice`），即当轮无合规切片；库内 245 条成绩观察来自公开 Git/JSON 路线，覆盖至 2026-09-18。**这不等于来源没有数据**，也不得把 NA 解释成没有数据。

---

## 四、跨来源的准确性保障机制

### 4.1 七层防线

| 层 | 机制 | 作用 |
|---|---|---|
| 1. 来源准入 | catalog `constraints` 显式声明禁用项（付费 API、MCP、鉴权导出、截图/OCR、视觉抓取、跨实体求和） | 在协议层杜绝不可审计的取数手段 |
| 2. 采集预算 | 每来源 `internal_request_budget`（并发=1、`requests_per_run`、超时、`max_file_bytes`、`retry_attempts`） | 限制上游压力与单次失败半径 |
| 3. 内容指纹 | 原始响应全量落 artifact + `content_hash`；语义 fingerprint 识别结构变化；ETag/Last-Modified/git SHA 条件请求 | 变更可检测，`methodology_drift` 可识别 |
| 4. 身份与幂等 | observation id 由 `(dataset, source, entity, metric, period, …)` 哈希决定；同日同 payload 幂等 | 重复采集不产生重复事实 |
| 5. 质量门 | 数据集级 `quality` 规则（值域、单调性、占比合计、引用链、身份完整性）；不合格候选进 `rejected_candidates`/`failures`/`quarantined` | 不合格数据**不覆盖最近有效观察** |
| 6. Vintage 与 as-of | 同期间值变化追加 revision vintage，旧版本保留；`latest_only=False` 可见、`as_of` 可重放 | 修订可追溯，历史判断不被改写 |
| 7. 冻结与重放 | snapshot manifest 固定当次输入；`replay_*_bundle(snapshot_id)` 离线重放且不重新打开网络 | 任何历史结论可复算 |

### 4.2 状态语义（不可混淆）

- `no_change`：内容 hash 与已入库一致，**不产生新观察**（不是失败）。
- `new_release`：新期间，追加月份。
- Revision：同期间值变化，追加 vintage，旧值仍可按 `as_of` 查询。
- `methodology_drift`：header/方法文本/scope/fingerprint 变化，**停止自动 platform 发布**并保留诊断。
- `source_conflict`：同期间跨来源差异 > 阈值（收入为 10%），只告警不改数。
- `unreachable` / `export_unreadable` / `question_not_fielded`：**都不是负面证据**，只降低本次覆盖；保持旧值与 stale 标记。

### 4.3 三条不得跨越的红线

1. **不插值、不前向填充、不伪造共同期间**——期间不足一律 `insufficient_history`。
2. **不把方法变化读成基本面变化**——口径断点切断序列（BTOS 2025-11 换题、收入跨估算机构切换、benchmark 换版无 bridge）。
3. **不把弱证据升级为强证据**——估算不升级为公司披露，token 不换算成收入，self-report 不升级为第三方成绩，平台份额下降不外推为全市场下降。

---

## 五、主要限制与风险清单

**数据可比性**

1. 收入趋势按来源分列（见 3.5），但同一来源内部改换原始标签仍在同一序列里。
2. BTOS 波次必须按整数排序，且 2025-11 前后不可合并。
3. Anthropic Economic Index 职业/任务两种切法不可相加或平均。
4. Ramp vendor 份额可重叠、model share 非采用率；OpenRouter `Other` 桶不可忽略。

**样本代表性**

5. BTOS 为企业自报采用；RPS 为个人自报使用；两者分母、主体、频率均不同，**不合成统一渗透率**。
6. Ramp 只覆盖 Ramp 客户；OpenRouter 只覆盖 OpenRouter 公共路由；两者都是有偏的领先指标，不是全市场统计。
7. Anthropic Economic Index 只覆盖 Claude；Sacra 为第三方估算。三角验证应跨"官方统计 × 平台行为 × 公司披露"三类，而非同类来源互证。

**当前运营状态（2026-09-18 观测）**

8. `frontier_ai_capability` 最近探测 `unreachable`（无合规切片）；需区分"无授权切片"与"来源无数据"。
9. `openrouter_rankings` 最近入库 2026-09-15。改为 7 天节奏后，SLO 已从 3 天放宽到 10 天，因此原先"贴着 SLO 边界"的告警已消除。
10. Anthropic Economic Index 的 O\*NET 关系覆盖率约 78.5%，低于代码内 99% 期望线，仅记警告；影响补充覆盖指标，不影响四条核心序列。
11. ~~`us_census_btos` 与 `rps_genai_adoption` 仍未提升到 `platform`~~ —— **已于 2026-09-19 解决**：两者签入基线改为 `platform`，生效模式随之切换；签入基线、本文件与 `STRUCTURED_DATA_OPERATIONS.md` §10.2 的不一致同时消除。
12. **7 天节奏带来的新鲜度代价（2026-09-19 起）**：OpenRouter 与 frontier 能力路线上游都是日更或事件驱动，统一降到 7 天后，新 token 周与新 benchmark 成绩最多延迟 7 天入库。若某个模型在窗口内发布了关键成绩，Observe 报告在那一周内不会反映它。这是刻意用新鲜度换成本与节奏一致性，不是缺陷。
13. **双来源同节奏**：TickerTrends 与 Sacra 同在 7 天节拍上，两家各成一条序列；语义指纹只能挡住同一来源的标记漂移，两家数值之间仍不可直接比较。

**容量**

14. L1 观察量占全库 90.4%，但 80%+ 集中在两个明细来源；L1 原始 artifact 约 80 MB，其中 Anthropic Economic Index 单项 52 MB。Anthropic Economic Index 的 HF 下载上限配置为 367 MB，是全部来源中最大的单次预算，扩容需优先评估它。

---

## 六、变更本文件时

本文件的规模数字是时点快照。当出现以下情况时应重新核验：来源新增或退役、数据集 quality 规则调整、调度频率变更、来源发布模式变更、Observer 声明的来源集合变更。

核验入口：

```bash
# 各来源生效发布模式（解析覆盖层与签入基线）
python -c "from ats.data.rollout_modes import source_mode; print({s: source_mode(s) for s in [...]})"

# 各来源最近一次探测状态与诊断
#   structured_source_checks（var/data.sqlite）
# 各来源入库规模、期间窗口与最近入库时间
#   structured_series JOIN structured_observations
# 最近一次采集质量计数（discovered/accepted/quarantined/unchanged）与原因码
#   structured_ingestion_runs

# 来源与数据集契约真源
#   config/data/structured.yaml、config/data/schedules.yaml
# L1 层面的 Observer 声明真源
#   config/sectors/ai_hardware.yaml 的 layers[L1_app].evidence_observers

# 范围复核（确认非结构化/新闻注册表仍与 L1 无关）
grep -rn "evidence_observers" config/                     # 应只有 ai_hardware.yaml 的 L1_app 一处
grep -c "<source_id>" config/data/unstructured.yaml        # 每个 L1 来源应为 0
```

> 提交本文件的任何改动前，请先重跑上面两条范围复核——本节第一段的三条结论（唯一 `evidence_observers`、十个来源在另一域命中为 0、`L1_app` 无 `claims:` 块）是本文件收窄范围的**唯一依据**。

配套阅读：`docs/archive/EVIDENCE_OBSERVER.md`、`docs/data/AI_PRODUCTION_PENETRATION_OBSERVER.md`、`docs/data/FRONTIER_AI_RAW_CAPABILITY_OPERATIONS.md`、`docs/data/FRONTIER_AI_LABS_REVENUE_OPERATIONS.md`、`docs/data/RAMP_AI_INDEX.md`、`docs/research/AI_MODEL_EVIDENCE_SOURCES.md`、`docs/data/STRUCTURED_DATA_OPERATIONS.md`。

范围外的注册表见 `docs/data/STRUCTURED_DATA_OPERATIONS.md`（Chain 侧）与 PEAD 新闻链路的运维文档。
