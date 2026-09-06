# AI 模型产业 Evidence Observe：观察项与数据源接入清单

> 状态：候选信源研究清单，不代表已经接入或通过验收。
> 信源信息最后核验：2026-09-04。
> 目标：用公开、免费或低成本、可信且可持续更新的数据，追踪 AI 模型产业的技术能力、生产化与商业化。

配套阅读：[`docs/EVIDENCE_OBSERVER.md`](EVIDENCE_OBSERVER.md)、[`docs/CHAIN_EVIDENCE.md`](CHAIN_EVIDENCE.md)、[`docs/DATA_SOURCES.md`](DATA_SOURCES.md)。

---

## 一、三个核心命题

```text
技术能力前沿
模型能够经济地做什么
        ↓
生产化与应用扩散
这些能力被真实、持续地使用到什么程度
        ↓
商业化能力
使用是否转化为可持续收入与单位经济
```

1. **技术能力前沿**：前沿模型在哪些任务上达到或超过不同层级的人类，并在成本、速度、可靠性和监督要求上达到经济可用门槛？
2. **生产化与应用扩散**：已经达到技术可用门槛的能力，有多少进入持续运行的生产工作流，并以多快速度渗透行业、职能和任务？
3. **商业化能力**：模型公司能否把持续使用转化为高质量、可留存且具有合理单位经济的收入，并形成可持续商业模式？

本清单主动不回答：AI 最终增加多少 GDP、全产业利润如何分配、系统性 AI Capex 的最终 ROIC，以及谁是终局赢家。

---

## 二、可证伪信源的准入标准

核心时间序列至少应满足：

- [ ] 有明确分母和覆盖范围。
- [ ] 结果可能上升，也可能下降，不是只发布好消息。
- [ ] 有日期、历史值和指标口径。
- [ ] 最好提供 CSV、API、公开代码或可复现方法。
- [ ] 方法变化可以识别并形成版本断点。
- [ ] 原始数据、网页或文件按期快照，不能只保存最新值。
- [ ] 取数失败、没有覆盖和指标为零是三种不同状态。

公司新闻稿和媒体披露通常不满足上述全部条件，只能进入**事件账本**，不能直接充当连续时间序列。公司没有更新某项指标，也不能被解释为指标没有增长。

### 建议信源分层

| 等级 | 定义 | 使用方式 |
|---|---|---|
| A | 官方统计、监管披露，口径和下载机制稳定 | 核心时间序列 |
| B | 独立研究或基准，开放原始数据、方法和代码 | 核心或交叉验证 |
| C | 平台交易/使用遥测，真实行为但样本有偏 | 有边界的领先指标 |
| D | 公司自述、客户案例、可信媒体报道 | 事件证据，不单独确认命题 |

---

## 三、技术能力前沿

### 3.1 候选信源

| 接入 | 观察项 | 数据源 | 成本与更新 | 定位与限制 |
|---|---|---|---|---|
| [ ] | 模型发布、上下文、工具、多模态、退役 | OpenAI、Anthropic、Gemini、xAI 官方模型页、价格页和 release notes | 免费；事件驱动 | 确认实际上线的产品与价格，不能独立证明能力 |
| [ ] | 综合能力与运行成本 | [LiveBench](https://livebench.ai/) | 免费；公开 CSV/GitHub；按版本刷新测试集，新模型持续加入 | 有客观答案和成本数据，适合构造质量—成本前沿 |
| [ ] | 模型、价格、延迟、吞吐聚合 | [Artificial Analysis API](https://artificialanalysis.ai/data-api/docs) | 免费层需要 API key；详细 benchmark 和历史数据需 Pro | 免费层已有综合指数、价格和中位性能，适合机器采集 |
| [ ] | 人类主观偏好 | [LM Arena](https://lmarena.ai/leaderboard/text/industry-software-and-it-services) | 免费；持续更新；原始数据阶段性发布 | 反映产品体验，但受用户结构、表达风格和提示词影响 |
| [ ] | 长任务自主执行能力 | [METR Task Horizons](https://metr.org/time-horizons/) | 免费；原始数据和代码开放；不定期更新 | 提供 50%/80% 成功率对应的人类任务时长；目前偏软件、ML和安全任务 |
| [ ] | 编码 Agent 能力 | [SWE-rebench](https://swe-rebench.com/about) | 免费；使用新近 GitHub 任务持续更新 | 比静态 SWE-bench 更适合跟踪进步；必须记录 Agent scaffold 和 Token 消耗 |
| [ ] | 工具调用和企业流程 | [tau-bench](https://taubench.com/) | 免费、开源；随模型和版本更新 | 覆盖零售、航空、电信、银行等工具调用任务 |
| [ ] | 电脑与跨应用操作 | [OSWorld 2.0](https://osworld-v2.xlang.ai/) | 免费、开源；不定期更新 | 适合观测长期、多步骤桌面工作流 |
| [ ] | 广泛经济任务覆盖 | [GDPval](https://openai.com/index/gdpval/) | 免费；部分任务开放；不定期更新 | 覆盖多种职业，经济相关性强；由模型厂商建设，不宜作为唯一排名源 |
| [ ] | 透明、可复现实验 | [Stanford HELM](https://crfm.stanford.edu/helm/index.html) | 免费、开源；不定期更新 | 适合复核供应商和聚合榜单结论 |

官方产品资料入口：

- [ ] [OpenAI Models](https://platform.openai.com/docs/models)
- [ ] [OpenAI API Pricing](https://openai.com/api/pricing/)
- [ ] [Anthropic Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)
- [ ] [Anthropic Model Deprecations](https://docs.anthropic.com/en/docs/about-claude/model-deprecations)
- [ ] [Gemini Release Notes](https://ai.google.dev/gemini-api/docs/changelog)
- [ ] [Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [ ] [xAI Release Notes](https://docs.x.ai/developers/release-notes)

### 3.2 建议派生指标

- [ ] 能力分项得分及其版本历史。
- [ ] 单个成功任务成本。
- [ ] 80% 可靠任务时长。
- [ ] Agent 任务成功率。
- [ ] 价格—能力 Pareto frontier。
- [ ] 相对普通人、熟练员工和专家的人类基准。
- [ ] 无人监督、最终审核、持续介入三档监督强度。
- [ ] 新模型相对上一代的能力增量。
- [ ] 能力领先优势的半衰期。

### 3.3 反证条件示例

命题：**前沿模型正持续扩大经济可用任务边界。**

- [ ] 连续数代模型只提高考试型 benchmark，真实 Agent 成功率没有提高。
- [ ] 50% 成功率提高，但 80% 可靠任务时长停滞。
- [ ] 得分提高完全依赖数倍 Token、延迟或成本。
- [ ] 厂商宣称的提升无法在至少两个独立 benchmark 上复现。

---

## 四、生产化与应用扩散

生产化需要同时采用三个分母：

```text
企业采用率    有多少企业使用
用户使用率    有多少员工持续使用
任务渗透率    有多少工作量实际交给 AI
```

### 4.1 候选信源

| 接入 | 观察项 | 数据源 | 成本与更新 | 定位与限制 |
|---|---|---|---|---|
| [ ] | 美国企业采用率、行业和规模 | [美国 Census BTOS](https://www.census.gov/hfp/btos/data)及[历史下载](https://www.census.gov/hfp/btos/data_downloads) | 免费；双周；历史文件和方法文档 | 2025-11 问题措辞变化，前后需要断点处理 |
| [ ] | 企业职能、任务和自动化方式 | Census BTOS AI Supplement | 免费；专题波次 | 部分问题并非永久核心题目 |
| [ ] | 欧洲企业 AI 采用 | Eurostat `isoc_eb_ai` 与[公开 SDMX API](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-getting-started/sdmx3.0) | 免费；年度 | 频率较低；需自行保存 vintage |
| [ ] | 英国企业采用和使用深度 | [ONS BICS AI 数据](https://www.ons.gov.uk/businessindustryandtrade/business/businessservices/articles/artificialintelligenceinukbusinesses/2023to2026) | 免费；BICS 双周，AI 专题不定期；CSV/XLS | 自愿调查且部分行业不在覆盖范围内 |
| [ ] | 员工每日/每周使用率 | [FRED/RPS GenAI Adoption Tracker](https://fred.stlouisfed.org/data/RPSGENAIUSAGESHAREEDLWALL) | 免费；季度；网页/CSV无需鉴权 | 自报使用，不能直接证明企业正式部署 |
| [ ] | 员工使用任务、频率和时间节省 | 美国 Census HTOPS | 免费；约双月专题 | 新数据源，历史较短 |
| [ ] | ChatGPT 工作/非工作、行业、职能、Agent 使用 | [OpenAI Signals](https://openai.com/signals/data-download/) | 免费；CSV；CC BY 4.0；持续更新 | 只覆盖 OpenAI 产品，分类方法由供应商掌握 |
| [ ] | Claude 职业、任务、自动化/增强、API 使用 | [Anthropic Economic Index](https://www.anthropic.com/economic-index) | 免费；开放数据；周期性发布 | 只覆盖 Claude，方法会随产品形态变化 |
| [ ] | 企业真实付费采用与供应商份额 | [Ramp AI Index](https://docs.ramp.com/developer-api/v1/ai-index) | 公共 Dashboard 免费、月度；API需 Data Partner 权限 | 基于 Ramp 客户付款，漏掉免费、捆绑和个人账户使用 |
| [ ] | 开发者/API 模型使用结构 | [OpenRouter Rankings](https://openrouter.ai/rankings?benchmark=intelligence) | 免费；高频公共榜单 | 只代表 OpenRouter；Token 不是请求数、客户数或收入 |
| [ ] | 开源模型关注与下载 | [Hugging Face Hub API](https://huggingface.co/docs/hub/en/api) | 免费；高频；开放接口 | 下载不等于生产部署，只作辅助信号 |

### 4.2 建议派生指标

- [ ] 企业采用率，按行业、规模和国家拆分。
- [ ] 员工周使用率和日使用率。
- [ ] AI 使用工时占总工时比例。
- [ ] 每家采用企业覆盖的职能数。
- [ ] 辅助、协作、代理执行的使用结构。
- [ ] 无人干预任务占比。
- [ ] API/Agent 使用相对传统聊天使用的比例。
- [ ] 付费采用率。
- [ ] 新采用、持续采用、停止付费 cohort。
- [ ] 行业渗透速度与扩散广度。

### 4.3 反证条件示例

命题：**AI 正在从试用进入持续生产。**

- [ ] 企业采用率上升，但员工日使用率长期不升。
- [ ] Token 大幅增长，却主要来自少量重度用户。
- [ ] 付费企业数量增加，但下一期大量停止付费。
- [ ] 使用长期集中在写作、搜索等辅助活动，自动执行占比不再提高。
- [ ] 厂商平台数据高速增长，而 Census、RPS、Eurostat 等独立调查持续停滞。

---

## 五、商业化能力

该层可观测性最弱，必须区分“可建立序列”和“只能记录事件”。

### 5.1 可以建立持续序列

| 接入 | 观察项 | 数据源 | 备注 |
|---|---|---|---|
| [ ] | API、订阅和工具价格 | 各厂商官方价格页 | 每日快照；分别记录 input/output/cache/batch/tool 等价格 |
| [ ] | 模型和产品组合 | 官方 release notes、model catalog、deprecation 页面 | 观察高低端产品结构、企业功能及旧模型替换 |
| [ ] | 付费企业采用率、供应商份额 | Ramp AI Index | 外部付费行为代理，不等于收入或市场份额 |
| [ ] | 第三方 API Token 份额 | OpenRouter Rankings | 开发者生态代理，不等于全市场收入份额 |
| [ ] | 开源模型使用热度 | Hugging Face、GitHub API | 用于判断闭源模型替代压力 |
| [ ] | 公共部门合同 | [USAspending API](https://api.usaspending.gov/) | 免费、无鉴权；金额和履约主体较硬，覆盖面窄 |
| [ ] | 融资申报 | SEC [Form D 数据集](https://www.sec.gov/data-research/statistics-data-visualizations/regulation-d-offerings) | 免费、季度；可确认部分融资金额，不能推导收入或完整估值 |
| [ ] | 战略投资方和云厂商披露 | [SEC EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | 免费、无需 API key、接近实时；适用于上市合作方 |

### 5.2 只进入事件账本

- [ ] 公司披露的收入、ARR、用户数和客户数。
- [ ] 创始人或管理层采访中的经营数据。
- [ ] 融资材料或内部材料的媒体披露。
- [ ] Reuters、Bloomberg、FT、The Information 等可信媒体报道的内部数字。
- [ ] 大客户合同和合作伙伴宣布。
- [ ] 客户案例中的效率、收入或成本节省。

每条事件至少记录：

- [ ] 指标原文和定义。
- [ ] 是年化 run-rate、合同额、bookings 还是确认收入。
- [ ] 是否包含免费额度、credits、转售或关联方交易。
- [ ] 指标覆盖期间和披露日期。
- [ ] 来源是否直接接触公司。
- [ ] 是单一来源还是多来源确认。
- [ ] 后续是否被公司确认、修订或否认。

### 5.3 当前缺乏可靠免费持续信源

以下字段应明确保存为 `unknown`，不能用估计值冒充事实：

- [ ] 私营模型公司的真实毛利率和贡献利润。
- [ ] 客户 NRR、GRR 和 churn。
- [ ] 客户集中度。
- [ ] 推理成本及训练成本分摊。
- [ ] 真实现金收入。
- [ ] 分产品收入。
- [ ] 企业折扣后的实际价格。
- [ ] 算力承诺和资产负债义务全貌。

---

## 六、第一版推荐接入范围

### P0：零成本、结构化程度高

- [ ] 官方模型目录、价格和 release notes。
- [ ] LiveBench。
- [ ] METR Task Horizons。
- [ ] SWE-rebench。
- [ ] Census BTOS。
- [ ] FRED/RPS。
- [ ] Eurostat `isoc_eb_ai`。
- [ ] OpenAI Signals CSV。
- [ ] Anthropic Economic Index。
- [ ] SEC EDGAR/Form D。
- [ ] USAspending。

### P1：免费但需要鉴权或额外准入

- [ ] Artificial Analysis 免费 API。
- [ ] Ramp Data Partner API。
- [ ] GitHub PAT，提高 API rate limit。
- [ ] FRED API key；非必需，CSV 路径可以无 key 运行。

### P2：网页型或方法偏差较大，作为交叉验证

- [ ] LM Arena。
- [ ] OpenRouter Rankings。
- [ ] OSWorld。
- [ ] tau-bench。
- [ ] GDPval。
- [ ] Stanford HELM。
- [ ] Hugging Face 下载与趋势数据。

### P3：低频非结构化事件

- [ ] 公司经营指标自述。
- [ ] 可信媒体披露。
- [ ] 大客户和合作伙伴案例。
- [ ] 上市战略合作方财报及电话会。

---

## 七、更新频率

| 频率 | 信源/任务 |
|---|---|
| 每日 | 官方发布、模型目录、价格、退役、公司经营事件；保存网页快照和内容哈希 |
| 每周 | LM Arena、Artificial Analysis、OpenRouter、主要 Agent 榜单 |
| 每月 | Ramp、SWE-rebench、OpenAI/Anthropic 使用数据；如来源本月未发布则标记 stale |
| 双周 | Census BTOS、ONS BICS |
| 季度 | RPS、上市合作方财报、经营指标汇总 |
| 年度 | Eurostat、跨国采用比较 |

每次采集至少保留：

- `period`
- `known_at`
- `source`
- `coverage`
- `unit`
- `methodology_version`
- `source_grade`
- `access_status`
- `raw_url`
- 原始文件或网页内容哈希

价格页、动态榜单以及只提供最新版本的数据源必须由本系统自行保留 vintage。

---

## 八、命题确认的交叉验证规则

优先使用三角验证：

```text
官方统计调查
      ×
真实平台/交易行为
      ×
模型公司自身遥测与披露
```

- [ ] 技术提升至少由两个相互独立的能力源支持，其中至少一个接近真实任务。
- [ ] 生产化提升至少由一个独立统计调查和一个平台/交易行为源支持。
- [ ] 商业化改善不能只依赖公司自述；至少需要付费采用、外部需求或监管/交易证据之一印证。
- [ ] 信源口径发生变化时切断时间序列，不把方法变化解释为基本面变化。
- [ ] 平台份额下降只能作为矛盾证据，不能直接外推为全市场收入下降。
- [ ] 取数失败或没有覆盖不能转化为负面证据。

---

## 九、权限与预算

第一阶段大部分来源无需鉴权。实施到对应项时再申请：

- [ ] **Artificial Analysis API key**：免费层即可启动模型、能力、价格和性能采集。
- [ ] **Ramp Data Partner API key**：增量价值高，但需要 Ramp provisioned access。
- [ ] **GitHub PAT**：提高 GitHub API 限流额度。
- [ ] **模型供应商 API keys**：用于运行固定的小规模自有 benchmark。
- [ ] **月度 benchmark 预算上限**：按模型数、任务数和重复运行次数设置硬上限。
- [ ] **FRED API key**：可选；仅在不用公开 CSV 下载时需要。

密钥不得写入本文件、Git 或聊天记录；应通过项目既有的 secret/env 机制配置。

---

## 十、当前仓库覆盖缺口

截至 2026-09-04：

- `private_company_events` 已注册但为 `registered_no_data`。
- 现有结构化目录主要覆盖上市公司财务、市场一致预期和区域/行业数据，不能直接回答本清单的三个命题。
- NashNova `fin websearch` 对本主题返回 `coverage=empty`，不应被当作 AI 产业数据主源。

因此后续应分别建设并治理：

- [ ] 技术能力数据产品。
- [ ] 生产采用数据产品。
- [ ] 私营模型公司事件数据产品。
- [ ] 商业化派生指标；允许并保留大量 `unknown`，不虚构完整财务序列。
