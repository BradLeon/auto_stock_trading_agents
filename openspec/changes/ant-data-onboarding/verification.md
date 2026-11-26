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

**签字：用户 2026-11-26 确认**（2.1–2.4 的代码、测试与隔离库试跑结果）。生产写入尚未执行，2.2、2.4 在生产库核对后勾选。

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

### 留给第 4 组

信息简报读取目标实体的全部文档类型。ANT 进入 targets 后，回看窗口内的注册类文件会成为简报候选并产生模型调用，是否排除在第 4 组决定。
