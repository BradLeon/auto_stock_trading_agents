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

## 第 7 组：公司披露指标通路（2026-11-26，7.1–7.4；7.5 未执行）

分支 `feat/ant-sec-routine`，尚未提交与合并；没有发出模型请求，没有写生产库。

### 实现

- 清单 `config/data/disclosure_metrics.yaml`：ANT 23 项，每项带单位、期间类型、定义与定位词。
- 定位、抽取、核验、候选：`pipelines/structured/disclosure_metrics.py`、`agents/disclosure/`、`skills/disclosure-extract/`。
- 确认、驳回、更正、序列：`stores/structured/disclosure_review.py`、`products/company_disclosure_metrics.py`。
- 命令 `ats data disclosure-{extract,candidates,confirm,reject,correct,series}`，说明见 `docs/OPERATIONS.md` §5.2。
- 模型路由新增角色 `disclosure_extract`（`config/settings.yaml`）；`agents/base.py` 未改。

### 测试

- `tests/test_disclosure_metrics.py` 19 项通过：清单加载，片段不含数值记为证据不完整，未披露不生成零值，单位、期间、表格列核验，缓存，确认人留痕，更正保留历史，多版文件取较新值并标记冲突。
- 运维文档一致性两项起初失败（新来源与数据集未登记），补登记后与登记文件、队列失败输出、官方披露、数据层文档测试一起 61 项通过。
- 新增文件的 ruff `F`、`B` 类检查通过。

### 命令行核对

| 命令 | 结果 |
|---|---|
| `disclosure-extract --form S-1 --dry-run`（生产库只读） | succeeded，需 18 次模型请求 |
| `disclosure-extract --form 424B4 --dry-run`（生产库只读） | succeeded，需 19 次模型请求 |
| `disclosure-candidates`、`disclosure-series`（生产库只读） | 空候选、空序列，各指标 `not_extracted` |
| `disclosure-confirm` 不存在的候选（临时库、临时队列） | 经队列执行并返回 `disclosure_candidate_not_found` |

预演后生产库中没有新表。队列核对发现失败命令默认进入 `retry_wait`、最多尝试 3 次，人工确认可能在日后被自动重放，抽取失败也会再次请求模型；已改为 `disclosure-*` 只尝试一次，临时队列复核为 `failed`、1/1 次。

### 离线预演（定位结果，不调模型）

| 文件 | 请求数 | 送入字符 | 全文未定位 |
|---|---|---|---|
| S-1（10-26） | 18 | 119,430 | 推理毛利、未来 12 个月到期承诺、GRR、产品收入、前十大客户占比 |
| 424B4（11-19） | 19 | 132,526 | 推理毛利、GRR、产品收入、前十大客户占比 |

合计 37 次请求；模型返回空内容时底层会重试，单项最多 4 次。

人工查读定位段落得到的待核对事实（以回放后的候选与原文比对为准）：424B4 写明算力是单一共享资源、按总量评估，不按推理与训练拆分，只有按性质列示的算力与基础设施费用，以及按职能列示的营业成本与研发费用。能否用营业成本代理“推理算力”（文稿指标 1a.a）需要用户决定，抽取不做替代。

### 7.5 首轮回放（2026-11-26，用户批准“合并并回放”）

- 提交 `2144a7e` 快进合并到 `feat/provenance-observability`（未推送），重启 dispatcher、serve；`/health` 与 `/dashboard/sources` 返回 200。新来源不在定时任务里。
- 回放前备份生产库：`var/backups/data-before-disclosure-replay-20261126.sqlite`，`quick_check` 通过。
- 先单跑 S-1 的 RPO 一项（1 次请求）核对结果，再跑 S-1 其余（17 次）与 424B4（19 次）。共 37 次抽取请求，与预估一致；无失败指标，日志中未见重试记录。
- 写入：抽取记录 46 条（有读数 29、未披露 8、未定位 9），候选 118 条（S-1 54、424B4 64），审阅记录 0，链接表无变化，没有发布观测。

比对（脚本与清单在主目录 `var/review/ant-disclosure-20261126/`）：

- 118 条候选的数值、期间、期间类型、维度与另行从全文扫描读出的表格行和句子逐项一致，无多无少。
- 收入与算力费用的单季之和等于全年、前九个月、上半年累计；到期分布之和等于合计 319,993 百万美元；两份文件无取值冲突。
- 108 条通过自动核验；10 条证据不完整（数值正确）：到期分布 6 条缺日期依据（`period_unverified`），算力费用累计 4 条取自带增减列的表（`table_column_unverified`）。拟定的更正经同一核验函数试算全部通过，尚未写入。
- 漏抽：10 万美元客户数 2025-03-31 约 400 家，两份文件的该句都不在送给模型的段落里。
- 清单未声明：424B4 写明全口径 900.9 十亿美元承诺中约 451.6 十亿美元在 2030-12-31 前发生。
- 口径：到期表（319,993 百万美元）的采购承诺只含剩余期限超过一年的不可撤销部分；“未来 12 个月 70.7 十亿美元”属于全口径 900.9 十亿美元，不能与到期表混算，第 8 组派生比率须按此取数。

#### 确认与发布（2026-11-26）

- 用户查看按指标与期间整理的清单（`ANT_候选确认.xlsx`，80 行、17 个指标）后在对话中确认全部指标；文件未由用户编辑。确认人记为 `lutong`。
- 发布前备份生产库：`var/backups/data-before-disclosure-publish-20261126.sqlite`，`quick_check` 通过。同一脚本先在该备份的副本上预演并核对通过，生产库未受预演影响。
- 经托管队列执行：更正 10 条（原候选记为驳回并指向新候选，数值与期间不变），确认 118 条；清单中的口径提示作为 `basis_note` 随观测写入。
- 发布后只读核对无问题：观测 118 条、质量状态均为 `accepted`、证据链接由 1,846 增至 1,964；默认序列 80 行，两份文件都有的取 424B4，无冲突；数值与回放时的候选快照逐项一致。
- 覆盖状态：17 个指标已发布，6 个未披露或未定位（推理与训练算力费用、推理毛利率、GRR、产品收入、前十大客户占比）。上述漏抽时点与未声明指标仍未补。
- 脚本与输出在主目录 `var/review/ant-disclosure-20261126/`（`publish.py`、`publish.out`、`verify.py`、`verify.out`）。

## 第 8 组：派生指标与收入序列（2026-11-26）

只读生产库运行，不调模型、不写库（观察器的快照写入在核对脚本里屏蔽）。输出在主目录 `var/review/ant-derived-20261126/`。

### 8.1 派生指标

- 代码 `data/products/company_disclosure_ratios.py`，命令 `ats data disclosure-derived --entity ANT`；口径见设计 D13。
- 测试 `tests/test_disclosure_ratios.py` 11 项：三项比率的正常计算与缺输入、缺项一次列全、不借相邻时点与到期表合计、收入增量非正、第四财季推算、撤回输入后不可计算、命令只读。
- 生产数据读数：只有 1b.b 在 2026-09-30 可计算，0.1464（13.8 × 75% ÷ 70.7），与静态基线一致。
- 其余不可计算：
  - 1a.c：全部承诺合计只有 2026-09-30 一期，缺 2026-06-30。
  - 3a 差距：运行率只发布了 2026-10-31 的 81 十亿美元。原文同句写“较 9 月 30 日增加 10 十亿美元”，9 月 30 日的 71 是两数相减，没有作为观测发布，没有借用。
  - 1b.b 2026-06-30：首版 S-1 的未来 12 个月承诺留空。

### 8.2 前沿实验室收入按来源分开

- 趋势单元加入来源；公司招股书与定期报告的收入与运行率成为独立序列（设计 D14）。
- 回归：6 月 TickerTrends 69.6、7 月 Sacra 65.0 不再连成下降（`tests/test_frontier_labs_revenue_series_split.py`）。
- 修复前后对比（同一生产数据，截止 2026-11-26 12:00 UTC）：

| 项目 | 修复前 | 修复后 |
| --- | --- | --- |
| 收入部分与总判断 | 扩大 | 扩大（未变） |
| OpenAI 最新值 / 来源 | 2026-09 50.0 / TickerTrends | 未变 |
| Anthropic 最新值 / 来源 | 2026-10 80.0 / Sacra | 未变 |
| OpenAI 趋势序列 | 5 点，两个来源混用 | 3 点，Sacra；净变化 +150% 未变 |
| Anthropic 趋势序列 | 7 点，两个来源混用，含 69.6 → 65.0 | 4 点，Sacra；净变化 +789% 未变 |
| 历史观察表 | 12 行 | 7 行；其余在“并列序列”表 |
| 月份缺口提示 | 4–5 个月 | 更多（单一来源披露月份少） |
| 观察器写入证据账本的两条收入读数 | “5 / 7 个披露点” | “3 / 4 个披露点”，数值与方向未变 |

- 冻结测试数据上总状态由“扩大”变为“部分”：OpenAI 两个来源各只有 2 点，按单一来源不能判方向。相应测试断言已改。
- 发现但未处理（改动前就存在）：Sacra 的“2026 年度”收入 16.30 与招股书 2026 年上半年收入 16.286 吻合，疑似把半年数记成全年。
- 相关测试 176 项：175 通过；`test_ai_hardware_l1_declares_data_observer_outside_chain_claims` 失败，它只检查本次未改的层配置。
- 2026-11-26 用户确认上述差异，勾选 8.2。
- 合并进运行分支后，定时运行的观察器会改写两条收入读数的说明文字。2026-11-26 只读核对：合并不增加模型请求。
  - 读取收入数据产品的只有商业化观察器，观察器与报告渲染不调模型。
  - 读数进入证据账本后，由 L1 收入命题的判读引用（取每行前 220 字，缓存键含全文）。这两行约 130 字，末尾带快照编号；11-18 至 11-26 每次采集编号都不同，缓存本来就失效。点数文字随同一次改写生效。
- 另见（改动前就存在，本组未改）：上述快照编号使读数未变时也生成新的事实版本，并让该命题重新判读，与 `config/data/schedules.yaml` 里“读数未变不重复入库”的说明不符。


## 第 9 组：OpenRouter 价格与估算花费份额（2026-11-27，工作树 `feat/ant-sec-routine`）

### 9.1 价格采集

- 隔离库跑了一次真实采集（公开接口 `openrouter.ai/api/v1/models`，不带凭据；未写生产）：列表 477 个条目，记价 450 个（900 条观测，全部 accepted），跳过 19 个别名、8 个价格为 -1 的路由类。
- 与用量的对应：生产库只读核对，2026-11-19 起 7 天用量按"带日期标识 + 路由变体"匹配到价格的 token 占 92.7%，`Other` 桶 5.0%，列表里查不到 2.2%；没有一对多。
- 存储：原件每天约 140 KB（原始响应约 930 KB）；库每天约增 1.0 MB（一年约 0.4 GB）。
- 网页抽查（2026-11-27 约 09:40 北京时间）：

| 模型 | 接口价（输入/输出，USD 每百万 token） | 网页 | 结论 |
| --- | --- | --- | --- |
| Claude Opus 5.5 | 4 / 20 | 首屏与各标准服务商 4 / 20 | 一致 |
| GPT-6.2 Astra | 8 / 40 | 8 / 40 | 一致 |
| Gemini 4 Carbon | 2 / 10 | 2 / 10（标注五折） | 一致 |
| DeepSeek V4.1 Flash 1017 | 高峰 0.30 / 1.20，折周均 0.181 / 0.725 | 原厂行 0.30 / 1.20（抽查时在高峰时段）；首屏 0.036 / 0.50 是最低价服务商 | 与原厂行一致，与首屏不一致 |

- 定向测试 `tests/test_openrouter_pricing.py`；`ats.data.refresh validate` 通过，隔离试跑显示新任务到期可执行。

### 9.2 估算花费份额

- 当前生产用量周在价格采集开始之前，产品按规格返回"无价格数据"；首个可估算周取决于合并时间：11-30 15:45（北京时间）前开始采集，则 11-30 至 12-06 周可估算，预计 12-10 排行入库后出数；晚于此顺延一周。
- 输入占比依据（模型网页 Activity 图上读到的 prompt 与 completion、reasoning token）：Claude Opus 5.5 约 99.2%，GPT-6.2 Astra 约 99.3%，DeepSeek V4.1 Flash 约 98.7%，Gemini 4 Carbon 约 97.4%。暂定 0.99。
- 一次性试算（今天的标价套在 11-16 至 11-22 的用量上，只用于选假设，不入库、不进任何报告）：

| 假设 | Anthropic | OpenAI | Google | DeepSeek |
| --- | --- | --- | --- | --- |
| token 份额（对照） | 4.2% | 4.7% | 2.3% | 27.4% |
| 标价，输入 0.50 | 42.5% | 10.2% | 10.6% | 13.5% |
| 标价，输入 0.95 | 42.9% | 10.3% | 10.6% | 15.7% |
| 标价，输入 0.99 | 42.9% | 10.3% | 10.6% | 16.2% |
| 缓存命中 75%，输入 0.99 | 40.7% | 10.1% | 10.6% | 14.9% |
| 缓存命中 90%，输入 0.99 | 38.6% | 9.8% | 10.5% | 13.6% |

- 输入占比对结果影响很小（Anthropic 变动不到 0.5 个百分点）；是否计缓存影响约 4 个百分点。实现未计缓存。
- 2026-11-27 用户确认：输入占 0.99，不计缓存。勾选 9.2。

### 局限

- 标价不是实付价（缓存、批量、长提示、自带密钥、企业折扣未计）；开源权重模型取原厂端点价，实际路由常走更便宜的服务商。
- 同一天价格改动后又改回原值，库里保留中间值（存储层对相同内容不新增版本）。

### 9.3 文档

`docs/data/L1_OBSERVER_DATA_SOURCES.md` 3.6.1 写明采集口径、估算算法和不能当收入的原因；`STRUCTURED_DATA_OPERATIONS.md` 两张目录表与预算表补行。顺带改正同文件里第 8 组之后已过时的"收入跨来源并成一条序列"三处。

### 测试

相关 11 个测试文件 143 项通过。更早一轮扩大到读取这些配置的 20 个文件时只有两项文档目录一致性失败，补行后通过。

## 第 10 组：前沿评测（2026-11-27，工作树 `feat/ant-sec-routine`，含提前做的 11.1）

### 先看这一条：Artificial Analysis 的网站条款

- 2026-11-27 读到 `artificialanalysis.ai/terms-of-use`（页面标注最后修订 2026-09-15）：网站只许可个人、非商业使用；禁止用软件、自动化代理或脚本向网站发起查询或抓取数据（只对公开搜索引擎有条件例外）。
- 现状：生产采集的 AutomationBench-AA、SciCode、CritPt、MMMU-Pro、Humanity's Last Exam 五项来自该站公开页面（门禁 `ATS_FRONTIER_AI_PUBLIC_TERMS_APPROVED=1`），占来源 `frontier_ai_capability` 4254 条成绩观测中的 2264 条。首版规格要求自动访问碰到已知站点条款限制时关闭。
- 本组检查时，我在读到条款之前用脚本下载过该站若干页面；读到条款后没有再请求它的数据页面。
- 本组未改动现有采集和门禁，去留由用户决定。官方免费接口是否覆盖这些评测与成本未核实（检索到的公开文档摘要只列综合指数、性能与价格；需要用户自行注册密钥后实测；免费档另受数据平台条款约束，有效条文未读到）。

### 10.1 发布日期与旗舰（11.1 提前完成）

- 新增 `config/data/model_releases.yaml`（11-27 当天登记现任旗舰 9 条，见下文“现任旗舰登记”）与 `src/ats/data/model_releases.py`：只有带实验室官方域名 https 公告链接的记录能确认；按 `as_of` 回放，之后登记的不可见、之后的更正回退。
- 采集端不再写发布日期，删掉写死的旗舰模型 ID、名称、别名表与 `ATS_FRONTIER_AI_FLAGSHIP_OVERRIDES`。读取端的发布日期和旗舰只取自登记；未登记的标"发布日期未登记"，旗舰列显示"旗舰未登记"，名单在矩阵的 `unregistered_release_models`、`unregistered_flagship_labs`。
- 库里旧版本不改写（4254 条中 4008 条存着"发布日期 = 成绩日期"），读取端忽略该字段。
- L1 观察器两条能力读数的周期原来取发布日期，改取成绩日期。
- 生产库只读对比（运行中代码 对 工作树代码，同一批数据）：

| 口径 | 当前旗舰矩阵覆盖 | A | B | L1 读数（2026-11） |
| --- | --- | --- | --- | --- |
| 运行中代码 | 80 / 99 | 扩大 | 已跨越 | 外扩 2、跨越 6 |
| 新代码 + 空登记 | 20 / 99 | 同上 | 同上 | 同上 |
| 新代码 + 测试用九条旧旗舰 | 80 / 99 | 同上 | 同上 | 同上 |

- **合并前提**：先登记现任旗舰（11.3 的一部分，需官方链接并经用户确认）。否则当前旗舰矩阵有数的格子从 80 降到 20；空登记下 753 个模型标"未登记"。11-27 已登记现任旗舰，合并后的覆盖见下文（合并线上分支后为 49 / 99）。
- 合并前发现并修复的读取问题（取代原“一次性影响”说明）：线上分支同日合入了版本去重（`RevisionRule`），未变化的成绩记 `unchanged`；序列维度去掉发布日期后，只有成绩变化或补上成本时才写新版本，且落在新序列，旧序列的值仍会出现在最新值读取里（成绩下调时旧高分可能继续参与 A/B）。现在最新值读取会丢弃已有更晚读数的旧序列行，带历史版本的查询不变。用保存的两份真实文件先后跑线上代码与合并后代码（临时库）：线上代码写 27 条；合并后代码 18 条补成本写新版本、9 条 `unchanged`；修复前读出 45 行（18 个评测重复），修复后 27 行、0 重复，覆盖 11、A 扩张、B 跨越不变。

### 10.2 成本字段（未完成，不勾选）

- **AA 页面做不到**：评测页的服务端数据里只有 token 数和单价，"每任务成本"数据集是空的，页面上的成本在浏览器端另行生成（页面还加载一份混淆过的数据文件，未解码）。用 token 数乘单价补算违反规格。模型页公布的只有"智能指数"整套成本（约 28 个模型），不是单项评测成本。按上一节条款，也不建议再扩大该站抓取。
- 已实现：非 AA 来源公开的成本随同一条成绩保存（数值、USD、`per_task` / `full_evaluation`、来源链接），读取端给 `cost_value` 等字段。

| 来源 | 核对对象（2026-11-27 匿名下载的公开文件） | 结果 |
| --- | --- | --- |
| Terminal-Bench-Science | 17 行 `total_cost_usd` | 17 行解析并保留，数值与文件一致 |
| OSWorld 2.0 | 56 行中 1 行有 `costPerTaskUsd` | 该行解析并保留，数值一致 |
| Terminal-Bench 4（Harbor） | 未做 | 只有固定样本测试。带公开 apikey 请求榜单接口的命令被会话权限分类器拒绝，没有换办法绕过 |

- OSWorld 另有 27 行 `estimatedCostUsd`（估算值），按"不用估算填充"未采用；这是我的判断，可改。
- 以上是与来源数据文件核对，没有逐项做网页目视抽查。成本只随新采集写入，现有库内记录没有成本。

### 10.3 新评测可得性检查（2026-11-27，用户当天决定）

| 评测 | 免费公开出口 | 覆盖与历史 | 版本、单位 | 使用条款 | 建议 |
| --- | --- | --- | --- | --- | --- |
| Epoch 能力指数 | `epoch.ai/data/eci_scores.csv` | 258 行，含置信区间；2023-02-24 至 2026-11-17；Anthropic 27 条（Fable 5.2 173.4） | 指数分；据二手来源称 11-04 改为贝叶斯方法（未核实） | CC BY 4.0（下载包内说明） | **接入** |
| AA 智能指数 v5.0.2 | AA 页面 | 默认只给 156 个中的 28 个，当前快照；Anthropic 4 个 | 指数分，九个子项 | 网站条款禁止脚本抓取 | 页面不接；**经官方免费接口接入**（见 10.4 续） |
| GDPval-AA v2.1 | AA 页面 | 320 个中的 27 个，快照；Anthropic 6 个 | Elo；页面标 Legacy | 同上 | 未覆盖（接口仅付费档含单项分数） |
| Harvey LAB-AA v1.1 | AA 页面（题库私有） | 48 个中的 27 个；Anthropic 5 个 | 0–1 比例 | 同上 | 未覆盖（接口仅付费档含单项分数） |
| APEX-Agents-AA | AA 页面 | 34 个中的 27 个，20 个已弃用；Anthropic 只到 4.6 | 无版本号 | 同上 | 未覆盖（接口仅付费档含单项分数；页面数据过期） |
| SWE-bench Pro | Scale Labs 页面内嵌数据 | 公开集 25、私有集 14 条；最新 2026-07-09；Anthropic 最新 Opus 4.6（04-08） | 百分比；公开、私有两组 | 未查到 | 未覆盖（停更，无 Claude 5.x） |
| SemiAnalysis GDPx v1.0 | 只有网页榜单，未见数据出口 | 默认 11 / 26 个模型，带每任务成本 | 百分比 | 条款（2026-11-11 生效）把交易或投资活动列为须书面授权的商业用途 | **未覆盖（待书面授权）** |

- 非百分比单位的评测接入时要放开现有 `[0, 100]` / `percent` 的取值约束。
- 现有已采集的 SciCode（页面另标 Under review）、CritPt、Humanity's Last Exam 在 AA 页面上也标为 Legacy。

用户决定（2026-11-27）："Epoch 和 SemiAnalysis GDPx 只要技术上可行，就都接入。"其余评测按上表不接。

- GDPx 没有接入，也没有写适配器。网页榜单技术上读得到，但 [条款](https://gdpx.semianalysis.com/terms) 第 3 节规定：未经 SemiAnalysis 另签书面许可，不得用于任何商业用途，交易或投资活动在列举之内；咨询地址 sales@semianalysis.com。规格要求未通过检查的评测登记"未覆盖"并写明原因，此条即登记。取得书面许可后再做适配器。

### 10.4 Epoch 能力指数（用户 2026-11-27 验收通过）

- 新来源 `epoch_capabilities_index` / 数据集 `epoch_capabilities_index_models` / 指标 `ai.epoch.capabilities_index`，周任务 `epoch_capabilities_index_p7d_ingest`（07:40 UTC）；读取入口 `epoch_capability_index`。口径见 `docs/data/L1_OBSERVER_DATA_SOURCES.md` 3.7.1。
- 许可：ECI 页面写明 CC BY 4.0，须署名。
- 真实文件采进隔离库（未写生产）：258 个模型全部接受、0 隔离；同日再采为 `no_change`。180 个模型归到九家中的八家（文件里没有腾讯）。发布登记为空，所以发布日期全部显示未登记。
- 网页抽查（2026-11-27，`epoch.ai/eci` 排行榜）：前 12 名的顺序、取整后的指数与可信区间全部一致。
- 读数时要注意：第一名 Claude Fable 5.2 为 173.40 [170.04, 177.22]，第二名 GPT-6.2 Astra 为 172.82 [169.37, 176.94]，区间大幅重叠，不能据此断言领先。
- 开发中发现并修掉一处读取错误：可信区间取错存储字段名而显示为空，隔离库实采时发现，已补断言。
- 历史：快照从首次采集开始；文件里带日期的旧模型一并在每个快照里。

### 10.4 续：Artificial Analysis 免费接口与 GDPx 复核（2026-11-27）

用户要求："1.AA 智能指数、GDPval-AA、Harvey LAB-AA、APEX-Agents-AA， 接入。 2. SemiAnalysis GDPx， 既然能读网页，能否采集时调起浏览器（playwright）的方式， 采集数据。 我们只做科研，不用于商业同途。"

**接口分档**（接口文档与定价页）：免费档含模型身份、综合指数、能力指数、输入输出单价、中位性能、智能指数成本，每天 100 次；单项评测分数只在付费的 Pro 档（500 次/天），价格需登录查看。

**数据条款 v1.1（2026-08-19，覆盖免费档）**：§1.7 内部使用包含本人研究与决策；§2.3 免费档可内部使用、分享图表、简短引用并署名；§2.6(d) 未经书面同意不得用数据开发、训练或改进任何 AI 系统；§11.7 终止后 30 天内删除原始数据。§2.6(d) 措辞很宽，是否限制把数值放进模型提示词没有写明，因此本来源不接任何模型任务。以上是我对条款文字的理解。

**已接入**：来源 `artificial_analysis_indices` / 数据集 `artificial_analysis_model_indices` / 指标 `ai.aa.*` 九个，周任务 `artificial_analysis_indices_p7d_ingest`（07:50 UTC），读取入口 `artificial_analysis_model_indices`。使用项目 `.env` 里已有的密钥。

- 隔离库实采（未写生产）：列表 767 个模型，其中 173 个有至少一项数值、157 个有智能指数、141 个有每任务成本；1395 条全部接受、0 隔离；同日再采 `no_change`；九家实验室都有记录；落盘文件中不含密钥。
- 核对：接口值与官网数据接口介绍页上展示的三个值一致（Claude Opus 5.5 59.1、GPT-6 Astra 56.9、Gemini 3.8 Flash high 30.7）。**没有做榜单逐项网页抽查**：网站条款禁止自动化读取，需用户人工抽查。
- 与 10.2 的关系：智能指数的每任务成本现在有合规来源；单项评测成本仍没有，10.2 不勾选。
- 密钥读取改为 `Secrets.artificial_analysis_api_key`（环境变量或 `.env`），定时任务不需要改 plist。

**未接入 GDPval-AA、Harvey LAB-AA、APEX-Agents-AA**：免费档不含，付费订阅须用户决定。接口示例里 `apex_agents_aa` 为空，订阅后覆盖多少没有核实。

用户决定（2026-11-27）："先只用免费部分"。不订阅 Pro，这三项登记为"未覆盖（付费档未订阅）"。现有靠页面采集的五项 AA 评测因此没有免费的合规替代，去留另行决定。

接入后复跑：读取相关配置与文档的 50 个测试文件 771 项通过、3 项跳过、5 项失败（与上文改动前就失败的是同一批）。

**GDPx 不做浏览器采集**：[条款](https://gdpx.semianalysis.com/terms)（2026-11-11 生效）§1 把抓取取得的数据同样算作 GDPx Results；§2 只许可个人、学术等非商业目的的访问查看与引用；§3 要求任何商业目的另签书面许可，交易或投资活动列在其中，商业目的的定义包含为自己取得直接或间接利益。条款没有单独禁止自动访问，限制在用途上，换成浏览器采集不改变用途；本系统的数据服务于交易决策。维持"未覆盖（待书面授权）"。

### 10.4 抽查（2026-11-27）

用户要求："抽查验收 AA 和 Epoch，登记现任旗舰模型。"两个来源都用隔离库（未写生产）。

| 核对项 | Epoch | Artificial Analysis |
| --- | --- | --- |
| 读取结果对采集时保存的来源响应，独立重读逐项比对 | 当天重新实采 258 条；258 个模型、1290 个字段，0 处不符 | 767 个来源模型、173 个结果行、1557 个字段（1395 个有值），0 处不符 |
| 对公开网页 | `epoch.ai/eci` 展开后的完整排行榜：258 行名称与取整指数全部一致，256 个可信区间全部一致 | 没有读榜单页面（网站条款禁止自动化读取） |
| 对公开引用 | — | 官网数据接口介绍页的三个值一致；Google 11-18 公告写 Gemini 4 Neon（xhigh）61 分、总排名第 3，我方 61.5、第 3。分值差 0.5，是取整方式还是数值后续更新无法确认 |

- 网页榜单表格中间多出一行重复的表头；起初按行号比对时因此差一位，数据行本身一一对应。
- Artificial Analysis 的榜单逐项目视核对只能由人做。可抽看三个值：Claude Fable 5.2 (Max) 64.8、GPT-6.2 Astra (Max) 62.2、Gemini 4 Neon (Xhigh) 61.5。
- 结论：两个适配器读出的值与来源一致。
- 验收：用户 2026-11-27 看过上述结果（含 Artificial Analysis 榜单页未逐项网页核对）后回复"通过，勾选"，10.4 据此勾选。范围是 Epoch 与 AA 免费接口两个适配器；GDPx（待书面授权）和 GDPval-AA、Harvey LAB-AA、APEX-Agents-AA（付费档未订阅）仍是"未覆盖"。

### 现任旗舰登记（11.3 的一部分，2026-11-27）

登记 9 条已确认记录（`config/data/model_releases.yaml`，登记时间 2026-11-27T07:31:33Z）。公告日期在各自官方域名页面核对（OpenAI 取官网新闻订阅源时间，Moonshot 取其官方论坛公告帖时间）。用户在对话中逐项选定。

| 实验室 | 旗舰 | 发布日期 | 备注 |
| --- | --- | --- | --- |
| OpenAI | GPT-6.2 Astra | 11-17 | |
| Anthropic | Claude Fable 5.2 | 11-09 | |
| Google | Gemini 4 Carbon | 11-10 | 取全面开放日（10-28 限量开放）；11-18 的 Neon 定位低于 Carbon |
| xAI | Grok 4.7 | 09-21 | |
| DeepSeek | V4.1 Flash 1017 | 10-17 | 与 09-10 初版分开；V4.1-Pro 未发布 |
| Moonshot | Kimi K3.1 Pro | 10-23 | UTC；北京时间 10-24 |
| 腾讯 | Hy4 | 11-04 | |
| 智谱 | GLM-5.4 | 11-23 | 只向编程套餐开放，API 待安全评估 |
| 阿里 | Qwen4-Max | 11-16 | |

- 按 11-26 时点回放，九家都显示"未登记"（登记时间生效规则）。
- 隔离库读取：AA 匹配到八家旗舰（缺 GLM-5.4），Epoch 匹配到六家（缺腾讯、智谱、阿里）。
- 生产库只读试算，同一批数据：

| 口径 | 矩阵覆盖 | OpenAI | Anthropic | Google | xAI | DeepSeek | Moonshot | 腾讯 | 智谱 | 阿里 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 运行中代码 | 79 / 99 | 11 | 10 | 10 | 7 | 9 | 11 | 2 | 9 | 10 |
| 新代码 + 现任九家 | 50 / 99 | 5 | 7 | 4 | 8 | 8 | 9 | 3 | 2 | 4 |

- A、B 各评测状态与 L1 两条读数（2026-11：外扩 2、跨越 6）两种口径完全相同。覆盖下降是因为多数评测还没测到新模型。
- 合并线上分支后复算（同一批生产数据，只读）：49 / 99，xAI 由 8 降到 7，其余八家与上表相同。原因是线上分支的 Toolathlon 序列起点过滤排除了一条 Verified 发布前的 xAI 存档成绩，原先被“最近评测版本”回退填进了矩阵；关掉该过滤即回到 50。运行中代码仍为 79 / 99，A、B 与 L1 读数相同。
- 合并后复跑：读取相关配置与文档的 50 个测试文件加线上分支带来的 3 个测试文件，819 项通过、3 项跳过、5 项失败（同下文改动前就失败的一批）。
- 向用户提问时第一问写的"约 57/99"有误：那是智谱仍用 GLM-5.3 时的数；按选定组合是 50/99（第三问写明选 5.4 再少 7 格）。
- 登记后复跑读取发布登记的 9 个测试文件：137 项通过。
- 11.3 要求的近 12 个月历史发布尚未登记，不勾选。

### 另见（改动前就存在，本组未改）

- 前沿评测任务报"成功"，但 `route_errors` 里一直有 LiveBench 404 和 Toolathlon 解析漂移；两项最后一条数据停在 2026-10-16。
- Terminal-Bench-Science 解析把模型发布日期当作成绩日期。
- OSWorld 同一模型同一日期的多种配置只保留第一条（保存的文件 50 行成绩落成 10 条记录）。

### 测试

`tests/test_model_releases.py` 覆盖"发布日期不等于成绩日期"、"候选不能用于计时"、"更正后按旧时点回放"。相关 17 个测试文件（含文档一致性）238 项通过；新文件 ruff 无报错。

接入 Epoch 后复跑：与两部分改动直接相关的 20 个测试文件 292 项通过。其余读取这些配置的 29 个文件 465 项通过、3 项跳过、5 项失败（`test_factset_earnings_insight.py` 4 项、`test_anthropic_consumer_boundaries.py` 1 项）；导出改动前的提交单独运行，这 5 项同样失败。

## 第 11 组：模型发布登记（11.2、11.3，2026-11-27 调研，未改代码、未写登记、未勾选）

### 11.2 只靠信息简报能否发现模型发布（生产库只读）

- 事件成分从 11-24 起才有，11-24 至 11-26 共 398 条，其中 24 条点名某家实验室，没有一条是模型发布。
- 11-07 以来的简报没有提到任何一家的现任旗舰。11-01 以来入库文档（761 份）和候选标题（4417 条）里也没有出现这些型号名。
- 同时含实验室名和发布类词的 21 条标题里，只有 1 条是模型发布（11-09，Anthropic 升级 Fable、Mythos，来源 `ibkr_news`）。
- 推测原因：简报按持仓标的生成，实验室发布只有影响到某只股票才进简报；成分技能要求例行产品消息不出成分。
- 结论：按现规格只从简报提候选，几乎不会产生候选。

另一个现成来源：每天已采集的 OpenRouter 模型清单（带上架时间）。读 11-27 保存的清单，近 60 天九家实验室新上架 41 条，合并批量与 pro 变体后约 20 个型号。登记的现任旗舰里有 8 个发布日落在该窗口，其中 7 个已上架且上架日期与登记日期一致，缺 GLM-5.4（只向编程套餐开放）。xAI 现任旗舰发布早于窗口，本次没有核对。

| 方案 | 做法 | 模型费用与提示词 | 漏报 | 规格 |
| --- | --- | --- | --- | --- |
| 甲（建议） | 用 OpenRouter 新上架型号生成候选，你确认时补官方链接与日期 | 无费用，不动提示词 | 不上 OpenRouter 或晚上架的（本次 GLM-5.4） | 需改"简报提出候选"一条 |
| 乙 | 甲 + 简报评级请求里加"是否模型发布"字段 | 不增加请求数，改动已验收的提示词 | 同甲，简报补充很少 | 保留原条并加来源 |
| 丙 | 只按原规格从简报提候选 | 同乙 | 按实测几乎全部漏掉 | 不改 |

三种方案都要建候选队列和确认命令。OpenRouter 上架日只作线索，发布日期仍以官方公告为准。

### 11.3 近 12 个月通用旗舰发布（待你确认）

拟用的登记规则：

1. 日期取任何人都能用的第一天：公开预览算，受邀或限量预览不算（沿用 Gemini 4 Carbon 记 11-10 的做法）。
2. 通用旗舰指当天该实验室公开可用的最高档通用模型；低于仍在售更高档位的新版本不登记。
3. 同一型号从公开预览转正式版，只登记第一次。

下表为拟登记项（不含已登记的九个现任旗舰）。xAI 官网拒绝自动读取，相关日期来自搜索结果。

| 实验室 | 型号 | 发布日期 | 官方链接 | 需要你看的点 |
| --- | --- | --- | --- | --- |
| OpenAI | GPT-5.2 | 2025-12-11 | https://openai.com/index/introducing-gpt-5-2/ | |
| OpenAI | GPT-5.4 | 03-05 | https://openai.com/index/introducing-gpt-5-4/ | |
| OpenAI | GPT-5.5 | 04-23 | https://openai.com/index/introducing-gpt-5-5/ | API 04-24 |
| OpenAI | GPT-5.6 Sol | 07-09 | https://openai.com/index/gpt-5-6/ | 06-26 限量预览不算 |
| OpenAI | GPT-6 Astra | 09-03 | https://openai.com/index/gpt-6-astra/ | 页面不印日期（取 RSS）；当天只向限量组织开放，随后几天放开，确切放开日未从官方页确认 |
| Anthropic | Claude Opus 4.6 | 02-05 | https://www.anthropic.com/news/claude-opus-4-6 | |
| Anthropic | Claude Opus 4.7 | 04-16 | https://www.anthropic.com/news/claude-opus-4-7 | 公告称低于限量开放的 Mythos Preview |
| Anthropic | Claude Opus 4.8 | 05-28 | https://www.anthropic.com/news/claude-opus-4-8 | |
| Anthropic | Claude Fable 5 | 06-09 | https://www.anthropic.com/news/claude-fable-5-mythos-5 | 06-12 暂停访问，07-01 恢复 |
| Anthropic | Claude Fable 5.1 | 09-01 | https://www.anthropic.com/claude-fable-and-mythos-5-1 | 公告页只写 9 月，日期取 https://support.claude.com/en/articles/12138966-release-notes |
| Google | Gemini 3.1 Pro | 02-19 | https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-pro/ | 公开预览 |
| Google | Gemini 4 Argon | 10-13 | https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-argon/ | |
| DeepSeek | V3.2 | 2025-12-01 | https://api-docs.deepseek.com/news/news251201/ | |
| DeepSeek | V4 Pro（预览） | 04-24 | https://api-docs.deepseek.com/news/news260424/ | 08-13 正式版按规则 3 不另登 |
| DeepSeek | V4.1 Flash | 09-10 | https://api-docs.deepseek.com/news/news260910/ | Flash 线；与现任 1017 版按旗舰登记的做法一致 |
| Moonshot | Kimi K2.5 | 01-27 | https://www.kimi.com/en/blog/kimi-k2-5 | |
| Moonshot | Kimi K2.6 | 04-20 | https://www.kimi.com/en/blog/kimi-k2-6 | |
| Moonshot | Kimi K3 | 07-16 | https://www.kimi.com/en/blog/kimi-k3 | |
| 腾讯 | Hy3（预览） | 04-24 | https://www.tencent.com/tencent-unveils-hy3-preview-model-enhances-agent-capabilities-and-real-world-usability/ | 07-06 正式版按规则 3 不另登 |
| 智谱 | GLM-4.7 | 2025-12-22 | https://docs.z.ai/guides/llm/glm-4.7 | |
| 智谱 | GLM-5 | 02-12 | https://docs.z.ai/guides/llm/glm-5 | |
| 智谱 | GLM-5.1 | 04-07 | https://docs.z.ai/guides/llm/glm-5.1 | |
| 智谱 | GLM-5.2 | 06-16 | https://docs.z.ai/guides/llm/glm-5.2.md | |
| 智谱 | GLM-5.3 | 08-18 | https://docs.z.ai/guides/llm/glm-5.3.md | Epoch 记 08-14 |
| 阿里 | Qwen3.5 | 02-16 | https://qwen.ai/blog?id=qwen3.5 | |
| 阿里 | Qwen3.6-Plus | 04-02 | https://qwen.ai/blog?id=qwen3.6 | |
| 阿里 | Qwen3.7-Max | 05-20 | https://qwen.ai/blog?id=qwen3.7 | |
| 阿里 | Qwen3.8-Max | 08-03 | https://qwen.ai/blog?id=qwen3.8 | 百炼清单记 08-02 |

另需你决定的项：

| 型号 | 问题 | 建议 |
| --- | --- | --- |
| Claude Opus 5（07-24，https://www.anthropic.com/news/claude-opus-5） | 低于在售的 Fable 5 | 不登记 |
| Claude Opus 5.5（09-22，https://www.anthropic.com/claude-opus-5-5） | 水平接近 Fable 5.1，档位名低于 Fable | 不登记 |
| Gemini 3.5 Flash（05-19，https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5/） | 公告称其代理与编码能力最强，但属 Flash 线；未查到 3.5 Pro 发布 | 按规则 2 不登记 |
| Qwen3.6-Max-Preview（约 04-18，https://qwen.ai/blog?id=qwen3.6-max-preview） | 日期未在浏览器核实；百炼清单记 04-14 | 核实日期后再登记 |
| Grok 4.5（https://x.ai/news/grok-4-5） | 日期冲突：07-08、07-14、07-16 | 你打开页面确认 |
| Grok 4.6（08-12，https://x.ai/news/grok-4-6） | 我读不到页面 | 你打开页面确认 |
| Grok 4.20（约 02 至 03 月）、Grok 4.3 Beta（04-17） | 没找到官方公告页 | 保持候选，不用于计时 |
| Claude Opus 4.5（2025-11-24）、Gemini 3 Pro（2025-11-18） | 早于 12 个月窗口几天，是窗口起点时的现任旗舰 | 建议一起登记，回放更完整 |

### 11.2 补充调研：OpenRouter 与第三方跟踪站（2026-11-27，只读，0 次模型请求）

用户要求优先用 OpenRouter 新上架模型，或跟踪大模型进展的第三方资讯站。

三个已接入来源读 11-27 保存的生产原文件，与现任 9 个和上表 28 个型号比对（OpenRouter 取上架时间的 UTC 日期，同型号取最早）：

| 来源 | 现任 9 个：找到 / 日期一致 | 待登记 28 个：找到 / 一致 / 相差不超过 1 天 |
| --- | --- | --- |
| OpenRouter 模型清单（每日） | 8 / 8 | 28 / 19 / 26 |
| AA 模型接口（每周） | 8 / 6 | 26 / 22 / 25 |
| Epoch 能力指数（每周） | 6 / 6 | 26 / 20 / 24 |

- 三者都没有 GLM-5.4。OpenRouter 超过 1 天的两项（Hy3、Qwen3.8-Max）是预览版或早期版本已下架，清单只剩后续版本；每日保存快照后，向前发现不受影响。
- 机械去重（跳过别名与 `-latest`，合并 `:batch`、`:free`）按这份清单回放：09-28 起九家 24 个候选，11-01 起 14 个，其中 5 个是已登记的现任旗舰。

第三方跟踪站（用网页读取工具读页面，所得是页面摘述；未下载数据文件，条款页未读）：

| 站点 | 读到的情况 |
| --- | --- |
| [models.dev](https://github.com/anomalyco/models.dev) | 社区提交维护；有 JSON 接口和 `release_date` 字段，无公告链接字段；仓库 MIT 许可，数据许可未见单独说明；覆盖与新鲜度未测 |
| [llm-stats](https://llm-stats.com/llm-updates) | 自称每小时更新并人工核对；页面无导出（另有接口页未读）；读到的最新条目 11-20 |
| [DemandSphere](https://www.demandsphere.com/research/ai-frontier-model-tracker/changelog) | 公司手工维护的变更日志；称有 JSON 与 RSS，地址和许可未核实；读到的最新条目 11-22 |
| [LLM Gateway](https://llmgateway.io/timeline) | 页面标注 11-24 更新；无导出；没有 GLM-5.4 |
| [LLM Reference](https://www.llmreference.com/changelog) | 只有月度计数 |
| aireleasetracker.com | 连接两次中断，未读到 |

对 11.3 清单有影响的发现（均待用户判定，上表未改）：

- GLM-5：三个来源都记 02-11（OpenRouter 02-11 16:59 UTC），上表官方页日期 02-12。
- Qwen3.7-Max：Epoch、AA 记 05-19，OpenRouter 05-21，官方页 05-20。
- GPT-6 Astra：OpenRouter 上架 09-04，Epoch、AA 记 09-03。
- Grok 4.5、4.6：三个来源一致，为 07-08、08-12。Grok 4.20（Epoch 02-17、OpenRouter 03-31）、Grok 4.3（OpenRouter、AA 04-30）仍无官方公告页。
- Qwen3.6-Max-Preview：AA 记 04-20，与百炼清单 04-14、此前的约 04-18 都不同。
- DeepSeek V4.1 Flash（09-10）：当时 V4 Pro 仍在售，按规则 2 字面不应登记，与已确认的现任 1017 版处理冲突。

### 11.2 实现与演练（2026-11-27，工作树，未合并；未写生产库，0 次模型请求）

用户决定："按此实现"（OpenRouter 出候选、用户确认）。

实现：
- `ats.data.model_release_discovery` 从每天保存的清单读时计算候选；基线 2026-11-27 与九家实验室前缀写在 `model_releases.yaml` 的 `discovery`。
- 命令 `ats data model-release-candidates | -confirm | -dismiss | -add`。确认与手工添加在 `model_releases.yaml` 末尾追加并先过登记表校验；忽略写 `model_release_dismissed.yaml`。
- 每日数据采集日报在有待确认候选时列出（最多 10 个型号名）；读取失败或没有基线清单时写明原因。

演练（真实清单，隔离库与配置副本）：11-27 保存的清单中，上架早于 11-01 的 435 条作为基线日快照，全部 477 条作为次日快照，经定价采集管道入库。
- 查看：9 个候选，即 11-01 起的 14 个减去 5 个已登记旗舰。
- 确认 `openai/gpt-6.2-sol` 并以 `-pro` 作别名、忽略两个：剩 5 个；日报段落列出这 5 个。
- 手工添加非官方链接被拒，文件未变。
- 确认时用的公告链接是演练占位值，只写进配置副本。

生产库只读（本工作树的配置）：清单快照只有 11-27，待确认 0 个，日报不加段落。合并后首个出现新型号的日子需只读核对日报内容。

测试：`tests/test_model_release_discovery.py` 20 项、`tests/test_collection_alerts.py` 新增 1 项。读到本次改动的 23 个测试文件 327 通过、1 失败（`test_anthropic_consumer_boundaries`，改动前就失败）。新文件 ruff 无报告。

已知局限：清单不收录的发布（如 GLM-5.4）要手工添加；基线日以后实验室补挂的旧型号也会出现一次，忽略即可。

### 11.3 历史发布登记（2026-11-27，用户确认："11.3 按建议"）

登记 35 条：已确认 32 条（上表 28 条、Grok 4.5 与 4.6、Claude Opus 4.5、Gemini 3 Pro），待定 3 条（Grok 4.20、Grok 4.3、Qwen3.6-Max-Preview，不计时）。与上表不同的两处按用户判定：GPT-6 Astra 记 09-04，GLM-5 记 02-11。不登记：Claude Opus 5、Opus 5.5、Gemini 3.5 Flash。连同现任 9 条，登记表共 44 条；九家现任旗舰与登记前相同。登记口径四条写在 `model_releases.yaml` 文件头。

公告链接核对：
- 32 条已确认记录的链接都在各实验室自己的域名上（登记表校验通过）。
- Grok 4.5、4.6：x.ai 拒绝自动读取，页面没有打开过；日期取 OpenRouter、Epoch、AA 一致值。
- Claude Opus 4.5（页面印 Nov 24, 2025）、Gemini 3 Pro（页面印 Nov 18, 2025，公开预览）本次读页面补核。
- DeepSeek V3.2（12-01）、智谱 GLM-4.7、5.1、5.2、5.3（发布说明页日期一致）、腾讯 Hy3 预览（内置浏览器读到 April 24, 2026）本次复读一致。
- Kimi K2.5、K2.6：页面可打开，正文没有印日期；01-27、04-20 与 OpenRouter、Epoch、AA 一致。
- 其余链接沿用本日调研记录。

生产库只读核对（成绩型号名匹配到登记记录）：32 条新增已确认记录各匹配到至少一个来源叫法；逐条看过匹配结果，除下面两项外没有指向别的型号或版本：
- DeepSeek V4 Pro：来源把 0813 版成绩列在 `deepseek-v4-pro` 名下，会按预览日 04-24 标注。
- Qwen3.8-Max：来源把 0902 版成绩列在 `qwen3.8-max` 名下，会按 08-03 标注。

这两项会让对应版本的发布日期偏早，影响领先窗口计算；本次未改匹配逻辑，留到 `ant-thesis-monitor` 定领先窗口口径时处理。

相关测试 14 个文件 237 通过、1 失败（`test_anthropic_consumer_boundaries`，改动前就失败）。

## 第 12 组：GPU 租赁价格来源调研（12.1，2026-11-27，待你决定；未实现 12.2）

### 免费来源

| 来源 | 型号与价格形态 | 免费历史 | 取数与条款 | 可追溯性 | 结论 |
| --- | --- | --- | --- | --- | --- |
| [GetDeploying 周度数据集](https://getdeploying.com/dataset/gpu-prices) | H100、H200、B200、GB200、A100 等 115 型号；各供应商挂牌价的中位数，带租赁形态列（文件内是否含现货与预订行未核实） | 近 53 周；首周未标；07-30 前每周只采一次，趋势页称早期手工维护、为近似值 | CSV、JSON，无需密钥；文件按 CC BY 4.0 发布，数据页带脚本读取示例，robots 未禁该目录。[站点条款](https://getdeploying.com/terms)禁止未经同意抓取站内内容，且称许可不扩大自动访问权限，每周自动取文件是否允许条款未说清 | 只有汇总值与供应商数；逐供应商明细要付费接口 | 建议主用 |
| [Computable GPU Index](https://github.com/getcomputable/gpu-index) | H100、H200、B200、B300；29 家供应商面板的按需挂牌价 | 实时接口 90 天；更早数据称在归档文件，未核实 | 无密钥接口，15 分钟一次；数据 [CC BY-NC 4.0](https://github.com/getcomputable/gpu-index/blob/main/LICENSE-DATA.md)（限非商业），用于金融产品或商业定价风控系统另需书面许可 | 最好：代码公开、逐供应商回执、可复算、已发布值不改。但方法版本 3 个月内多次变更（09-03 v8 到 v10，09-15 与 10-15 之间到 v16），接口把各版本拼在一条序列；项目新，运营方同时做算力撮合 | 备选 |
| [Ornn](https://data.ornn.com/markets) | H100、H200、B200、B300、A100 等；成交价日结 | 3 个月日频（H100 自 08-28 起 91 点） | 无密钥 JSON，说明页公开邀请取数、robots 放行；[条款](https://data.ornn.com/terms)限内部使用，禁止未经书面授权的抓取和缓存 | 方法公开；供应商与成交明细不公开；运营方关联公司出售算力 | 拿到书面确认前不接 |
| [Vast.ai](https://vast.ai/pricing.md) | 市场挂牌价 | 90 天日中位数 | [条款](https://vast.ai/terms)禁止自动采集、存储和用于指数 | 单一市场 | 不接 |
| [SemiAnalysis 免费页](https://semianalysis.com/gpu-pricing-index/) | H100、A100、B200 综合指数 | 页面历史表自 2023 年 | [指数专门条款](https://semianalysis.com/gpu-pricing-index-terms/)（11-27 读取）：自动或重复采集、存入数据库或数据管道、纳入定期出版物，均需书面许可；一次性研究笔记可截图或引用个别数值并署名 | 方法为综合指数 | 不接 |
| [Silicon Data 免费档](https://www.silicondata.com/pricing) | H100 等指数 | 30 天，只能网页看 | 接口属付费档；[条款](https://www.silicondata.com/terms-of-use)禁止未经同意存库 | 指数 | 不接 |
| [Azure Retail Prices](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices) | 单一云厂商的挂牌价与现货价 | 无历史查询，需从现在积累 | 无需认证；使用条款未读 | 官方价目 | 不接：只代表一家超大云挂牌价 |
| [AWS 价目与现货历史](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-the-aws-price-list-bulk-api.md) | 单一云厂商 | 现货 90 天 | 文档要求账户授权 | 官方价目 | 不接：需要账号 |
| [Lambda](https://lambda.com/pricing)、[SF Compute](https://sfcompute.com/legal/terms-of-service) | 单家挂牌价或撮合市场 | 无 | 无公开接口；SF Compute 条款禁止复制市场数据 | 单家 | 不接 |
| AI Compute Tracker、fabryka | — | — | 前者两次连接中断，后者返回 402 | — | 未能核查 |
| data.getcomputable.com 根地址 | Computable 归档 | — | 根地址返回 404 | — | 归档文件未核实 |

同期 H100 读数相差很大，说明不同来源不能拼接：GetDeploying 周中位挂牌价 4.03（11-23 当周）、Computable 按需面板 3.80（11-27）、Ornn 成交价 3.19（11-26）、Silicon Data 指数 3.07（11-26，同页另一处写 2.53），单位均为美元每 GPU 小时。

### 付费选项

| 来源 | 价格 | 覆盖 |
| --- | --- | --- |
| Ornn Premium | 500 美元/月 | 成交价日频全历史；小时数据在更高档，价格未读 |
| Silicon Data Pro | 998 美元/月 | 指数全历史与接口；对外再分发另授权 |
| SemiAnalysis GPU Pricing API | 未公布 | 164 个以上型号、9 年以上、每小时 |
| GetDeploying API | 499 美元/月或 4990 美元/年 | 逐供应商明细、日频 |
| Computable、Vast.ai 数据许可 | 未公布，邮件洽询 | 商业使用授权 |

### 建议

主用 GetDeploying 周度数据集，只取按需价：许可对用途限制最少，接入当天就有约 12 个月历史可算趋势。代价是它是挂牌价中位数，供应商进出会改变读数，运营者是个人。Computable 的可追溯性更好，但许可限非商业（本系统读数最终影响真实交易决策，是否算非商业条款没有说清），且方法版本变更频繁，按同一版本算趋势目前凑不满 3 个月。接入时先取一次周度文件，核对列、租赁形态和首周日期，再请你抽查验收。

**决定**：2026-11-27 用户选定"用 GetDeploying"。

## 第 12 组：GPU 租赁价格接入（12.2，2026-11-27，工作树，未合并、未写生产库）

### 数据文件实测

- 11-27 08:50 UTC 取一次 `weekly.csv`：1,139,934 字节，SHA-256 以 `60ee2815a0ba3264` 开头，17,390 行、115 个型号。
- 列为 `gpu_slug, date, billing_type, reservation_months, min_price, max_price, median_price, provider_median_price, provider_count, offering_count`。
- 租赁形态含 `ON_DEMAND`（4,723 行）、`SPOT`、`RESERVATION`（按期限分行）等；只取按需。
- 日期都是周一。六个 NVIDIA 型号各有连续 53 周按需数据（2025-11-24 至 2026-11-23），无缺周。已下架型号保留更早日期。

### 隔离库演练（同一份文件，0 次模型请求）

- 首次入库 318 条，隔离 0；原样重读 `no_change`（318 条 `unchanged`）。
- 读出的 318 个（型号、周）价格与供应商数，与文件按需行完全一致。
- 与来源网站趋势页 11-23 当周公布值核对（当天早些时候读取）：H100 4.03（45 家）、H200 4.86（39 家）、B200 8.50（24 家）、B300 9.50（18 家）、GB200 10.50（5 家）、A100 2.00（43 家），六项一致。

| 型号 | 05 月值 | 08 月值 | 09 月值 | 10 月值 | 最新周 11-23 | 8 至 10 月方向 | 样本偏少 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H100 | 2.990 | 3.370 | 3.359 | 3.607 | 4.034 | 有涨有跌（-0.3%、+7.4%） | 否 |
| H200 | 3.645 | 4.400 | 4.443 | 4.875 | 4.858 | 上涨 | 否 |
| B200 | 5.500 | 6.370 | 6.505 | 7.990 | 8.500 | 上涨 | 否 |
| B300 | 7.500 | 7.890 | 7.890 | 8.990 | 9.500 | 有涨有跌（0.0%、+13.9%） | 是 |
| GB200 | 16.000 | 18.770 | 16.000 | 16.000 | 10.500 | 有涨有跌（-14.8%、0.0%） | 是 |
| A100 | 1.788 | 1.760 | 1.754 | 1.830 | 1.995 | 有涨有跌（-0.3%、+4.3%） | 否 |

单位美元每 GPU 小时。11 月要到 12-08 的采集后才算整月。

### 实现

- 来源 `getdeploying_gpu_prices`、数据集 `gpu_rental_price_weekly`、指标 `ai.gpu.rental.listed_price`，每周二 08:10 UTC 任务 `getdeploying_gpu_prices_weekly_ingest`。
- 读取入口 `DataProducts.gpu_rental_price(gpu_model, as_of)`：返回周值、月值、趋势和状态（`covered`、`uncovered`、`stale`、`methodology_drift`；趋势另有 `insufficient_history`）。
- 每次取回整份文件存档（约 1.1 MB，一年约 60 MB）。

### 给后续判读的提醒

- `连续下跌月数` 把任何幅度的下降都算作下跌（如 H100 9 月 -0.3%）。ANT 论点监测的"连续 3 个月下跌"要不要设最小跌幅，留到 `ant-thesis-monitor` 定。
- GB200、B300 供应商少，供应商进出就能造成大幅跳变（GB200 由 16.0 到 10.5 时供应商由 3 增到 5）。判读建议以 H100 为主。
- 11-27 与 SemiAnalysis 公开图做过一次人工对照（同坐标叠图，不提取、不存其数值，图未入库）：本来源不能完全替代。2025-11-24 之前无历史；没有成交合约价；H100 按需价方向大体一致，但上涨起步偏晚、10 月下旬起涨幅明显更大；B200 漏掉 2025-12 至 2026-02 的回落，10 月下旬之后方向相反；A100 方向对不上。H200、B300、GB200 无对照。
- 用户决定（2026-11-27）："我没有钱买数据， 也没法在 workflow中加入人工复核。 差异不大的话，可以使用GetDeploying。" 因此不买数据，流程里不设人工对照 SemiAnalysis 的步骤。按上一条对照，只有 H100 方向大体一致。
- 来源对价格的说明（[数据页](https://getdeploying.com/dataset/gpu-prices) 11-27 读取）：计入所有未标售罄的挂牌价，包括可用性未知或排队的（占多数），未核实能否租到；供应商进出也会让中位数变动。三种租赁形态各自取中位数，供应商组成不同，形态之间的价差不是同一批供应商的比价（A100 的 12 个月预订价在 53 周里有 41 周高于按需价）。

### 12.2 抽查验收（2026-11-27）

用户对 11-23 当周六个型号三种价的抽查答复："通过，勾选"。12.2 勾选。验收时代码在工作树，未合并、未写生产库；合并后首个周二的生产采集需只读核对（按需与旁证各型号有数、隔离 0）。

### 加采现货价与 12 个月预订价（2026-11-27，用户决定："加上，只作旁证"）

- 入库按需（判读）、`spot`、`reserved_12m`（旁证）三种序列；读取结果的旁证在 `series[].corroboration`。覆盖状态只看按需价。
- 旁证行数值异常只隔离该行（运行记 `partial`）；文件结构异常和按需行异常仍整次拒收。
- 同一份已下载文件、空的隔离库演练（未重新下载、未写生产库、0 次模型请求）：首次入库 873 条、隔离 0；原样重读 `no_change`（873 条 `unchanged`）。读出的 873 个（型号、形态、周）价格与供应商数与文件一致；按需 318 条与此前相同。
- 覆盖：除 GB200 的 12 个月预订价只有 13 周（趋势"历史不足"）外，各型号三种形态都有至少 32 周。旁证多数标样本偏少；GB200 现货和预订价最新周只有 1 家供应商。
- `tests/test_gpu_rental_price.py` 29 项通过，三个相关文件 ruff 无报告；读取所改配置与文档的 23 个测试文件 400 通过、1 跳过、5 失败（`test_factset_earnings_insight` 4 项、`test_anthropic_consumer_boundaries` 1 项，改动前就失败）。

### 测试

以下是只采按需价时的结果。

- 新增 `tests/test_gpu_rental_price.py` 23 项通过；三个新文件 ruff 无报告。`base.py`、`registry.py` 的 5 条 ruff 报告都在本次没改的行上。
- 相关测试 66 个文件：935 通过、3 跳过、19 失败。失败所在的 3 个文件在未改动的 HEAD（`35616fd` 导出副本）上同样是 19 失败、55 通过。
- 19 项中 5 项是此前已知的（`test_factset_earnings_insight` 4 项、`test_anthropic_consumer_boundaries` 1 项）。
- 另 14 项在 `test_phase_f_research_gate`，基线文档只登记了 2 项。ANT 改动之前的 `6d8f24c`（11-25）上同样是 14 失败、13 通过。原因：层复核改用 `LayerVerdictEventView`（11-16 随事件影响路由引入），测试里的假模型只认 `LayerVerdictView`，报 `unexpected model schema`。本组不修，基线文档未更新。
