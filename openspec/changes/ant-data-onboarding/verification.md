# 验收记录

> 每项由用户抽查后签字。未签字的标"待确认"。

## 1. 身份统一

### 1.4 登记 ANT 前后的 L1 对比（2026-11-26，按分离方案重做）

**签字：用户 2026-11-26 确认**（1.1–1.4）。

方法：同一份生产数据副本，旧代码旧配置（HEAD）对新代码新配置各跑一次，比较观察器读数与命题证据簇（确定性部分，不调模型）。证据文件：`var/audits/ant-data-onboarding/1.4/cl_old.json`、`cl_new.json`、`clusters.py`。

**差异清单：无。** 两份输出逐字节相同：

| 项 | 结果 |
|---|---|
| 观察器读数（生产化 3、商业化 3、原始能力 2） | 相同 |
| ai_core_production_workflow_penetration：读入 6 行、3 个证据簇 | 相同 |
| ai_frontier_labs_commercialization：证人 ANTHROPIC / OPENAI / OPENROUTER，读入 182 行、4 个簇（含 SACRA→ANTHROPIC 收入簇） | 相同 |
| ai_frontier_raw_capability：读入 4 行、2 个簇 | 相同 |

证据簇相同则命题判读的输入相同。

生产账本清理（用户批准）：撤回方案前定时任务写入的两行——`revenue_ant` 读数标记失效（原 `revenue_anthropic` 行仍有效）；一条新闻观测实体改回 `ANTHROPIC`（观测表与事实表各一行）。清理后账本里没有有效的 ANT 行。清理前备份：`var/backups/data-before-ant-ledger-cleanup-20261126.sqlite`。

模型调用：2026-11-25 首次比对未限定 L1，发出 39 次 `evidence_adjudicator` 请求；2026-11-26 针对已撤回方案的重跑发出 5 次；本次对比 0 次。

附带改动的测试：`tests/test_chain_kb_review.py` 原用 ANTHROPIC 作"未登记的合作方"示例，ANTHROPIC 现在是 ANT 的名称别名，示例改为仍私有的 OPENAI。回归时另有两条原有失败（不带本次改动同样失败）：`test_anthropic_consumer_boundaries::test_anthropic_job_adoption_has_no_default_trading_weight_or_route`、`test_collection_boundary::test_technical_review_publishes_per_entity_projections`。

### 1.x 身份分工（2026-11-25 至 11-26，用户确认）

ANT 只是证券标识，适合交易直接相关的请求；证据、命题、新闻、评测用 Anthropic / Claude。ANT 同时是普通英文词、也与蚂蚁集团同形。落法见 design.md D1：
- `config/entities.yaml` 的 ANT 加 `code_ambiguous: true`。
- 文章里单独的 "ANT" 须带 `$ANT`、`NYSE/NASDAQ: ANT` 或邻近 shares/stock 等语境才算提及；`Ant Group`、大写 `ANT GROUP`、无语境的 `ANT` 都不关联。
- `canonical_entity` 不把 "Ant" 折叠成 ANT；Anthropic、Claude 等名字在证券侧照常折叠到 ANT。
- `config/entities.yaml` 的 ANT 加 `evidence_entity: ANTHROPIC`，证据账本入口换名。
- 测试：`tests/test_config.py` 与 `tests/test_chain_evidence.py` 新增用例。

## 2. 注册类文件入库

**签字：用户 2026-11-26 确认**（2.1–2.4 的代码、测试与隔离库试跑结果）。生产写入见下面的“生产写入”一节。

### 2.1 本地入库命令

`ats data registration-ingest --entity ANT --form S-1 --file <路径>`（封面没有申报日期时加 `--filing-date`，已知时可加 `--accession`）。查询版本链：`ats data registration-filings --entity ANT [--as-of 日期]`。

夹具测试（`tests/test_registration_filings.py`）覆盖：封面注册人不是 Anthropic, PBC 时隔离并记 `identity_mismatch`（正文提到 Anthropic 不算数）；同一文件重复提交返回已有文档、不产生新版本；同一申报的另一份副本不重复入库；表单不符、文件截断、正文过短、申报日期缺失或矛盾时隔离；未在 `registration_issuers` 声明的发行人拒绝；写生产库须持有托管队列租约。

### 2.3 补采表单与一次性命令

`ats data registration-backfill --entity ANT`。S-1、S-1/A、424B4 只出现在这条命令里；每日发现的 `FORMS` 未变，测试确认它对 GOOG 与 ANT 都只返回常规表单。补采命令不进入任何定时任务。

### 2.2 首版 S-1 试跑（2026-11-26，工作树隔离库，经托管队列）

| 项 | 结果 |
|---|---|
| 状态 | succeeded |
| 实体 / 表单 / 类型 | ANT / S-1 / `registration_statement` |
| 封面注册人 | Anthropic, PBC |
| 申报日期 | 2026-10-26 |
| 来源 | `local_registration_file`（本地提供） |
| 原始字节 | 14,169,626；sha256 `16b927eda717e80d3a805622ebd154bdf4dcead16634138cf060e1fde81a4b2e` |
| 正文 | 1,571,455 字符，状态 full |
| 按 ANT 检索 | 命中 1 份；按 ANTHROPIC 不命中（申报文件属证券侧） |
| 重复提交 | no_change，`same_file_already_held` |
| 截至日查询 | 2026-11-26 返回该份；2026-10-25 返回空 |
| 财报事件材料 | 0 份 |
| 证据账本 | 观测 0、事实 0（入库不触发抽取） |
| 模型调用 / 网络请求 | 0 / 0 |

抽取质量：去掉空白与不可见字符、解码 HTML 实体后，新抽取器与原有财报抽取器的正文逐字符相同（各 1,331,082 个非空白字符），即只改变了分词与实体解码。原抽取结果里的 `SE CURITIES` 一类断词已消除。RPO 段落可直接读到：2026-06-30 剩余履约义务 108 亿美元，约 75% 在 12 个月内确认。

### 2.4 SEC 补采试跑（2026-11-26，工作树隔离库，用户批准使用已配置的 SEC User-Agent）

夹具测试另覆盖：本地已有的首版不重复下载、只附展品的 S-1/A 记为不适用、下载失败下次只重试该份、封面或 CIK 名称不符时停止。

CIK 2130357。结果 succeeded：入库 4 份，首版 S-1 因本地已有而跳过（SEC accession 0001628280-26-067168）。

| 表单 | 申报日期 | accession | 原始字节 | 正文字符 | 来源 |
|---|---|---|---|---|---|
| S-1 | 2026-10-26 | （本地文件无） | 14,169,626 | 1,571,455 | 本地提供 |
| S-1/A（1 号） | 2026-11-02 | 0001628280-26-068676 | 14,311,206 | 1,590,155 | SEC EDGAR |
| S-1/A（2 号） | 2026-11-10 | 0001628280-26-072484 | 14,798,491 | 1,643,369 | SEC EDGAR |
| S-1/A（3 号） | 2026-11-17 | 0001628280-26-074338 | 14,896,162 | 1,649,486 | SEC EDGAR |
| 424B4 | 2026-11-19 | 0001628280-26-074944 | 14,400,541 | 1,608,853 | SEC EDGAR |

| 验证项 | 结果 |
|---|---|
| 截至 2026-11-26 最新 | 424B4（11-19） |
| 截至日查询 | 10-25 空；10-26 为 S-1；11-09 为 1 号修订；11-18 为 3 号修订 |
| 首版仍可读取 | 是，五版正文都可读 |
| 财报事件材料 / 财报事件 | 0 / 未写日历 |
| 证据账本 | 观测 0、事实 0 |
| 重跑 | no_change，5 份全部 `same_filing_already_held`，零下载 |
| 请求数 | 首跑 6 次（代码表、申报清单、4 份正文），重跑 1 次（申报清单） |
| 模型调用 | 0 |

四份下载文件都超过原 12 MB 固定上限，`max_body_bytes` 的改动是必需的。

首版留空、后版给值（抽查用，本组只保存各版原文，不抽取指标）：

| 项 | S-1（10-26） | 首次给值 | 424B4 |
|---|---|---|---|
| 合同义务表（截至 2026-09-30，百万美元） | 表头在，数值空白 | 1 号修订（11-02）：总额 319,993，其中采购承诺 313,819 | 同值 |
| 每股发行价 | 空白 | 2 号修订（11-10）：区间 70–78 美元 | 83.00 美元 |
| 剩余履约义务 | 108 亿美元（截至 06-30） | 1 号修订起：138 亿美元（截至 09-30），约 75% 在 12 个月内确认 | 同 1 号修订 |

### 生产写入（2026-11-26，用户批准“提交、合并并写入生产库”）

代码提交 `3d00b88`，快进合并到 `feat/provenance-observability`。写入前备份：`var/backups/data-before-ant-registration-20261126.sqlite`（quick_check ok）。均经托管队列。

1. `registration-ingest` 首版 S-1，带 `--accession 0001628280-26-067168`：succeeded。
2. `registration-backfill --entity ANT` 跑了三次：第一次入库 2 份，1 号和 2 号修订连接/读取超时（partial）；第二次补到 2 号修订（partial）；第三次补到 1 号修订（succeeded）。每次只下载缺的部分。超时不是 SEC 限流。

| 核对项（生产库只读） | 结果 |
|---|---|
| 版本链 | S-1 → 1 号 → 2 号 → 3 号修订 → 424B4，取代关系正确；五份都有 accession |
| 与签字的试跑比对 | 五份正文哈希与原始字节哈希全部相同 |
| 截至日查询 | 10-25 空；10-26 S-1；11-09 1 号；11-18 3 号；11-26 424B4 |
| 按 ANT 检索 / 财报事件材料 | 5 份 / 0 份 |
| 变化量 | 文档 +5（1719→1724），版本 +5 |
| 证据观测、事实、日历事件 | 无变化（13630、13818、228）；引用这些文档的事实 0 |
| 模型调用 | 写入前后 `llm_calls` 都是 858 条 |
| 隔离记录 | 0 |

常驻进程（dispatcher、serve）未重启：改动只增加新模块和新登记项，它们不使用。

### 留给第 4 组

信息简报读取目标实体的全部文档类型。ANT 进入 targets 后，回看窗口内的注册类文件会成为简报候选并产生模型调用，是否排除在第 4 组决定。

## 3. SEC 例行通路与日历（A3）

状态：3.2 代码与测试完成；3.3 完成（暂无可登记的日期）；3.1 只完成离线部分，不勾选。改动已于 2026-11-26 合并到主工作树（见第 4 组末尾“合并”）。

### 3.2 缺财季标签的财报日（2026-11-26）

- 来源只给日期时发布占位事件 `earnings:{代码}:unlabeled:release`（标签含“缺财季标签”）：不能确认发布，不匹配事件路由，仍驱动按日历到期的 SEC 申报发现。
- 补标签：`ats events label --event-id earnings:ANT:unlabeled:release --fiscal-label FY2026Q4 --actor … --reason … --evidence-json '{…}'`
- 来源完全没有时登记：`ats events register --entity ANT --date YYYY-MM-DD --fiscal-label FY2026Q4 --actor … --reason … --evidence-json '{…}'`
- 撤回：`ats events withdraw`。规格增量增加了“标注后改期”“来源随后给出财季”“零散日期不建事件”“没有来源时登记”四类场景。

| 验证 | 结果 |
|---|---|
| `tests/test_calendar_unlabeled_earnings.py`（新增 19 项） | 通过 |
| 原有日历、财报事件测试（39 项） | 通过 |
| 路由、触发、调度、归属等相关测试（102 项） | 通过 |
| 生产日历副本干跑：现有 18 个目标加 ANT，两个财报来源各刷新一次 | 新事件版本 0，占位事件 0 |

### 3.3 ANT 首份财报日期（2026-11-26 查询，窗口 10-27 至 2027-06-14）

| 来源 | ANT | 对照 NVDA |
|---|---|---|
| Finnhub 财报日历 | 0 行 | 3 行 |
| Yahoo 财报日期 | 无数据（ValueError） | 2 行 |

两边都没有日期和财季标签。没有可依据的日期，未做人工登记；公司公告后用 `register`，来源先给出无标签日期时用 `label`。用户 2026-11-26 确认“ANT 还没有财报是符合预期的”，3.3 勾选。

给第 4 组：ANT 进入 targets 后，在 Yahoo 有数据前，该来源每次刷新都会记为 partial，日历产品质量显示 degraded。

### 3.1 回放：离线部分（2026-11-26）

在生产库的隔离副本（工作树 `var/sec_replay/data.sqlite`）上读取，禁止网络连接，没有模型调用。

| 事件 | 发现 | 正文 | XBRL（本季） | 财报事件材料包 |
|---|---|---|---|---|
| NVDA Q2 FY2027（08-26） | 8-K、10-Q | 两份入库，哈希一致 | companyfacts 13 个指标，季度收入 962.21 亿美元 | 财报稿、10-Q、纪要，可打分 |
| NVDA Q3 FY2027（11-17） | 8-K | 入库，哈希一致 | 只有 defeatbeta 的 1 个指标；companyfacts 尚无本季 | 财报稿、纪要，可打分 |
| SPCX Q2 FY2026（08-04） | 8-K、10-Q | 8-K 入库；**10-Q 未入库** | 无 | 财报稿、纪要，可打分 |
| SPCX Q3 FY2026（10-20） | 8-K、10-Q（10-22） | 8-K 入库；**10-Q 未入库** | 无 | 财报稿、纪要，可打分 |

- SPCX 两份 10-Q 的隔离原因都是 `sec_missing:filing_index`，最后一次尝试 11-03，早于 11-19 的 iXBRL 主文档修复（`6aea8d5`）；任务在 3 个不同日期“缺失”后放弃，修复后没有重试。当前代码能否取到还要实际请求 SEC 才能确认。
- SPCX 没有财务数据，是因为它在 `observe` 名单而财务任务只覆盖 targets。ANT 进入 targets 后会被覆盖。
- 同一原因的存量不止 SPCX：副本里共 19 份定期报告已放弃且任何来源都没有正文（含 AMZN、TSLA 10-Q，MSFT、GOOG、MU 10-K 等）。不在本 change 范围，另行处理。
- NVDA 11-17 事件窗口内的发现记录没有 10-Q，是否已申报未核实。

用户决定（2026-11-26）：其他标的不补采。因此没有重新抓取 SPCX 的 10-Q 与财务数据，上述 19 份存量也不处理；实际值抽取的模型调用未执行。

未验证、留到 ANT 首份 10-Q 发布后的真实验收：当前代码对一家新上市发行人的 10-Q 正文与财务数据采集，以及实际值抽取。已有的旁证只有生产历史记录：NVDA Q3 FY2027 在 11-19 抽取出收入 1,158.44 亿美元、EPS 2.68 和 6 个打分维度。

## 4. 接入研究任务流（2026-11-26）

**签字：用户 2026-11-26 确认**（4.1–4.6 的代码、配置、文档与测试结果，含 4.1 的关联范围取舍）。

状态：4.1–4.6 完成；启用配置之外的改动已合并。

### 合并（2026-11-26，用户指示）

`feat/ant-sec-routine`（第 3、4 组）快进合并到主工作树分支 `feat/provenance-observability`，定时任务开始使用；启用分支 `feat/ant-activation` 未合并。改动不含数据库结构变化。合并前在不含启用配置的版本上跑 47 个相关测试文件：706 项通过，25 项失败，全部在上述改动前即失败的清单里。随后追加的日历、文章与调度器测试按用户指示中止，未取得结果。

### 4.1 关联范围

- ANT 声明 `association_scope: headline`。新增测试：正文提到 Anthropic / Claude 的 NVDA 文章只关联 NVDA；标题、副标题、导语提到时关联 ANT；只在正文提到 Anthropic 的 IBKR 新闻照常准入、不关联 ANT；元数据重算与简报选材同样只看标题、副标题、导语。
- 相关测试通过。顺带修正第 2 组遗留的一条失败测试（`test_managed_cache_miss_local` 的来源清单缺两个注册类来源）。
- 解释上的取舍，供确认：正文里的 `$ANT` 之类股票代码也不建立关联。
- 注册类文件在 ANT 进入 targets 后可能成为简报候选；424B4 日期为 11-19，合并时已超出 7 天回看，今后的注册类文件属于正常信息，未改代码。

### 4.2 基本面未覆盖数量

生产账本副本（只读复制，用后删除），2026-11-26 05:11 UTC，按生产配置用决策周期的同一函数计算：18 个 targets 未覆盖 **0** 个，最近 8 个决策周期冻结的 `fundamental_uncovered` 均为空。加入 ANT 后为 1 个，不超过上限 4。

### 4.3 首份财报前的未覆盖状态

改动前的行为：ANT 进入 targets 后，只要当天有信息简报，基本面例行任务就以“缺少上一版预期基线”失败。

新增测试 10 条：首份财报前发布缺口（有无简报都发布）；带人工登记的下次财报日期；财报前建出基线仍为未覆盖；财报已入库无基线时说明原因而不失败；财报与基线齐备后自动转正常；财务数据读不到只对新股记缺口；其他公司行为不变；缺口投影不被复用；决策覆盖计算把 ANT 列为未覆盖。`test_phase_f_snapshot_dependencies` 的 22 个 error 与 `test_collection_boundary::test_technical_review_publishes_per_entity_projections` 在改动前的提交上同样存在。

待定（首份财报发布前需要决定）：财报前若因数据不足没有建出基线，首份财报后 ANT 只在事件评审有效期内算覆盖，之后要等下一次财报前窗口或人工执行基线刷新。

### 4.4 打分维度（用户 2026-11-26“按建议写入”）

`config/pead/ANT.yaml`：新增年化收入 0.25、推理毛利 0.20、算力费用率 0.15、未来承诺付款 0.15、非 coding 产品收入 0.10、NRR 0.10、年付 10 万美元以上客户数 0.05，合计 1.00。没有“下季指引”一项，首份财报后再定。不手填财季标签；产业链同侪由层配置派生为 GOOG。加载与权重测试通过。

### 4.5 可交易名单、L1 层、风控

- `config/pead.yaml` targets 加 ANT（19 个，未超过日历单次 20 个）；`ai_hardware.yaml` 的 L1_app 加 ANT 及定价机制备注，`private` 只留 OpenAI；`risk.yaml` 登记 `instruments.ANT` 并改写 L1 上限注释（上限 10% 不变）。
- 新增 `tests/test_ant_target_config.py`（5 项），`test_pead_roster` 名单加 ANT。
- 依赖这些配置的测试 34 个文件：419 项通过，25 项失败。同样的 25 项在改动前的提交（`8d1e194`）上也失败；逐条比对失败信息，仅两处文字差异（`ai_hardware.yaml` 的内容哈希随文件变化，该项改动前已与记录值不符；另一处是集合打印顺序）。失败分布：研究关口 14、研究 4、层级复核 3，配置哈希、事件开关、层风险命题、L1 观察器声明各 1。其中层级复核与 L1 观察器声明的用例改动前即红，对本次改动没有检验力。
- 第 3、4 组其余相关测试 11 个文件：210 项通过。

用户 2026-11-26 决定代码先合并、启用留到最后：以上三处配置、`docs/agents/SECTOR_ANALYST.md` 的 L1 描述、`tests/test_ant_target_config.py` 与 `tests/test_pead_roster.py` 的改动单独在分支 `feat/ant-activation`，试运行（16.1）通过后合并（16.2）。

启用后跟随 targets / 层名单自动生效的变化：

| 项 | 变化 |
|---|---|
| `consensus_daily`、`company_financials_weekly`、`company_financials_sec_daily` | 自动纳入 ANT。首份 10-Q 前的结果（失败、隔离或无变化）未验证，启用后在 14.4 核对 |
| `chain_evidence_daily` | 自动纳入 ANT。只处理近 7 天内已确认发布的财报，首份财报前为 `no_print_in_window`，不请求模型；注册类文件不触发它 |
| 风控 | L1 上限开始约束 ANT 实际持仓 |
| 层级与行业 | 层成员出现 ANT，命题证人仍为 ANTHROPIC；ANT 有规范财务数据前不进 L1 截面 |

### 4.6 文档

- `docs/ARCHITECTURE.md` 原本没有 L1 层的描述，只在 §9.2 补了新上市公司“未覆盖”；L1 的层描述在 `docs/agents/SECTOR_ANALYST.md`，已更新成员、读法与 L1 上限行（合计随之为 123%）。该表 L7 行仍是 11-06 规则取消前的 8%，不属本 change，未改。
- `docs/OPERATIONS.md` 新增 §5.1 注册类文件命令，§6 补财季标签命令。
- 命令按写法在工作树运行，数据库、队列、产物目录指向临时位置，用后删除：

| 命令 | 结果 |
|---|---|
| `registration-ingest`（用户提供的首版 S-1） | succeeded，申报日期 10-26 |
| `registration-filings`（含 `--as-of 2026-10-25`） | 返回 1 份；截至 10-25 为空 |
| `registration-backfill` | 工作树没有 `.env`，以 `sec_user_agent_missing` 停止，没有请求 SEC；真实补采见第 2 组“生产写入” |
| `events register` | 建出 `earnings:ANT:FY2026Q4:release` |
| `events label` | 参数通过，临时库没有占位事件故报 `unknown calendar event`；成功路径由 `tests/test_calendar_unlabeled_earnings.py` 覆盖 |

核对中发现文档初稿漏了财季标签命令必填的 `--evidence-json`，已改。经托管队列执行时工作进程自动读取仓库 `.env`；只有绕过队列的隔离运行需要手工导出 `SEC_EDGAR_USER_AGENT`。
