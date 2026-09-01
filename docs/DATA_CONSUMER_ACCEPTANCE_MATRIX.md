# 数据消费者验收矩阵

本矩阵是 `unify-data-layer-architecture` 的消费者切换基线。它以实际导入和运行路径为准，
而不是仅以 `config/data/consumer_release.yaml` 的计划状态为准。数据源是否可发布由来源级
质量门决定；本表只安排各消费者在其切换时应验证的读取、输出和回滚。

最后核对：2026-09-01。对应自动化检查：`tests/test_consumer_topology.py`。

## 读取路径分类

| 类型 | 含义 | 验收方式 |
| --- | --- | --- |
| 直接持久化数据读取 | Agent/Workflow 经 `ats.data` 兼容入口或 `ats.data.products` 读取受管数据产品 | 验证产品输入、血缘、输出和回退 |
| 间接证据读取 | Agent 不读特定 dataset；Chain 先把来源转为文档/观测/claim evidence，Agent 再读取证据投影 | 分别验证 Chain 来源和 Agent 注入，禁止误写成直接 series 读取 |
| runtime 输入 | 即时行情、期权、宏观市场输入；不进入持久化数据集 | 验证运行时可达性及降级，不做持久化对账 |
| 编排边界 | 只调度/汇总上游或读写 Workflow memory | 验证上游状态、端到端运行和回滚；不伪造数据产品对账 |
| deferred 输入 | 已登记但当前不采集的数据 | 明确空覆盖是预期状态，不作为其他消费者失败 |

## 叶子消费者

| 消费者 | 类型 | 实际入口与当前数据路径 | 数据产品/输入 | 本轮任务 |
| --- | --- | --- | --- | --- |
| `chain_regional` | 直接持久化数据读取 | `ats.chain.sources.fetch()` → `_platform_fetch()` → `DataProducts.metric_series()/derive()`；模式由 `read_mode("chain_regional", source_id=...)` 决定 | `regional_tw_exports`、`regional_kr_exports` | 13.1 |
| `macro_agent` | 直接持久化数据读取 + runtime | `ats.agents.macro.assemble.build()` → `regional.fetch(consumer="macro_agent")`；FRED/新闻仍属 runtime | 两个区域出口 dataset；FRED/新闻不持久化 | 13.2 |
| `pead_fundamentals` | 直接持久化数据读取 | `ats.graph.pead.prep_fetch()/score_fetch()` → `ats.data.fundamentals.fetch()`；该兼容入口按 consumer mode 调用平台 `DataProducts` | `company_financials` | 13.3；完成后同时重新判断旧任务 10.1 的财务部分 |
| `sector_constituent_financials` | 成分股持久化财务读取 + runtime | `ats.agents.sector.assemble._snapshots()` → `fundamentals.fetch_constituent_financials()`；财报指标复用 PEAD 的完整报表包，市值/估值/Beta 保持 runtime | `company_financials` 的成分股报表包派生指标；runtime 市值/估值/风险 | 17.2 |
| `pead_consensus` | 直接持久化数据读取 | `ats.graph.pead.prep_fetch()` → `ats.data.consensus.fetch()`；兼容入口按 consumer mode 读取平台 snapshot | `market_consensus` | 13.5 |
| `sector_consensus` | 直接持久化数据读取 | `ats.agents.sector.assemble._snapshots()` → `consensus.fetch(..., consumer="sector_consensus")` | `market_consensus` | 13.6 |
| `pead_research` | 直接非结构化数据读取，写入仍在 memory | `ats.agents.pead.research.run()` → `research.stored_articles(..., consumer="pead_research")` → `get_unstructured_read_router()`；processing lease、insight/event 写入仍属于 memory | TrendForce、SemiAnalysis 等 `research_article` 共享资产；官方披露由 PEAD Graph、IBKR News 由 monitor/Graph 分别消费 | 14.1 ✓（2026-08-30） |
| `evidence_chain` | 直接非结构化数据读取，证据判断写入仍在 memory | `ats.chain.report.render()` → `get_unstructured_read_router(consumer="evidence_chain")` | release、filing、transcript、research/RSS/news | 14.2 ✓（2026-08-30） |
| IBKR→Yahoo | 失败范围 fallback | `ats.data.pipelines.unstructured.source_acceptance`；IBKR 健康或真实零新闻时不得调用 Yahoo | `ibkr_news` 主来源；`yfinance_live_news` 仅失败切片兜底 | 14.3 ✓（2026-08-30） |

## 组合消费者与编排边界

| 消费者 | 类型 | 实际入口与依赖 | 特殊边界 | 本轮任务 |
| --- | --- | --- | --- | --- |
| `sector_agent` | 直接持久化数据读取 + 间接证据读取 + runtime | `assemble.build()` 直接调用 `regional.fetch(consumer="sector_agent")`，并在 `_snapshots()` 调用基本面、Consensus 与价格 snapshot；`_chain_evidence()` 读取 Chain claim evidence | `trendforce_dram` / `industry_dram_contract_price` **不是** sector 的直接 dataset。`config/sources.yaml:dram_contract_price` 先作为 `hbm_pricing`、`supply_tightness` 的 Chain 来源，再经 claim evidence 注入 `_chain_evidence()` | 15.1 ✓（2026-08-30；受用户确认的 `unknown` 降级判读） |
| PEAD Graph | 直接持久化数据读取 + runtime | `ats.graph.pead` 的 prep/score：财务、Consensus、release/filing/transcript；研究洞察和连续新闻分别由 `pead_research` 与 `pead_monitor` 的受管读取写入 Workflow memory 后进入分析上下文；价格、期权、日历为 runtime | runtime 价格/期权不得写入结构化库；dossier/score/report 留在 memory | 15.2 ✓（2026-08-30） |
| `chief_graph` | 编排边界 | `ats.graph.chief.assemble_context()` → `workflow_data_boundary("chief_graph")`，随后汇总 Agent/Workflow memory 产物 | Chief dossier、决策、交易状态属于 memory，不是结构化或非结构化输入 | 16.1 ✓（2026-08-31） |
| `runtime_scheduler` | 编排边界 | `ats.runtime.scheduler._daily()` → `workflow_data_boundary("runtime_scheduler")`，并触发采集 pipeline、PEAD、Chief 等 | 负责触发与失败隔离；自身没有可替代的持久数据读模型 | 16.2 ✓（2026-08-31） |

## 非消费者边界

| 项目 | 分类 | 当前处理 |
| --- | --- | --- |
| `private_company_events` / `accepted_document_evidence` | deferred 输入 | 已登记、无 coverage；未作为本轮任一直接消费者的可用输入，不阻止已发布数据源或消费者验收 |
| IBKR/yfinance 行情、期权、ThetaData | runtime 输入 | 通过 runtime 适配器按需查询，不持久化、也不进入本矩阵的结构化数据发布对账 |
| Workflow reports、claim assessment、dossier、Chief 决策与交易结果 | Workflow memory | 可引用数据血缘，但不应登记为数据层输入或作为 source 发布证据 |

## 执行与记录规则

1. 每个任务先运行本矩阵所列实际入口的无副作用/fixture 回归，再运行受管数据的隔离 smoke；不以 mock 成功替代真实产品读取。
2. 直接消费者记录：输入 snapshot/文档版本、数据血缘、输出、失败回退和 rollback。
3. 间接证据消费者记录两段链路：来源到 Chain evidence、Chain evidence 到 Agent context。
4. 编排边界记录上游通过状态、端到端回归和回滚，不创建虚假的 legacy/platform 数据对账。
5. 单个消费者通过后才可调整其 consumer release；不会反向阻塞已经通过来源质量门的 source release。

### 14.1 验收记录（2026-08-30）

- 以 `pead_research` 的 `platform` 只读路由读取实际迁移库的近 30 天资产：获得 4 篇
  SemiAnalysis 和 25 篇 TrendForce，均保留稳定 document ID、来源、采集时间和正文；历史记录
  缺失 article-native ID 或发布时间时，读取器以 document ID 和采集血缘恢复可审计的兼容读。
- `ibkr_news`、`yfinance_live_news` 与 `yahoo_news` 被明确排除，避免 wire 新闻被误作为长期研究文章；
  新闻由 monitor/Graph 在后续任务验收。
- `partial` SemiAnalysis 可进入 `pead_research`，但仅限该来源的可验证预览正文；读取、提取提示及
  `article_id → document/version` 输出血缘均保留 `partial`/截断原因，绝不把它描述为全文。其他
  `partial` 或 `teaser` 研究资产仍被拒绝。官方披露与 IBKR/Yahoo 新闻继续排除在本消费者外。
- 2026-08-31 的 platform-only 隔离验收在 30 天窗口选择了 29 篇实际研究文章；29 篇均有
  immutable document/version 血缘，no-LLM 处理循环在临时 Workflow memory 中按每轮 8 篇上限运行。
  当前窗口没有 SemiAnalysis `partial` 实例；该边界由自动测试覆盖，发布不要求 legacy 输出等价。

### 14.2 验收记录（2026-08-30）

- 发现并修复了增量迁移 CLI 的 `Path` 作用域错误；随后以备份
  `var/data_migration_backups/ats.62669a9758b0ff5f.sqlite` 完成真实迁移。`unstructured-documents`
  补入 6 份文档、6 个版本、2 条别名和 179 个 chunks；`unstructured-evidence` 补入 156 条观测。
  两个 migration manifest 的所有表均 source/target digest 一致。
- 平台侧 NVDA 实例可同时读到 earnings release、SEC regulatory filing、电话会纪要及 152 个
  实体关联 chunks；document、candidate、version、entity、alias、chunk、fact、projection、
  observation 和 failure lineage 均与兼容源库一致。`observation_failures` 现也经平台路由读取，
  而 claim assessment 等 Workflow 写入仍留在 memory。
- 共享第三方资产在平台库可读：TrendForce 25/25、SemiAnalysis 4/5、IBKR News 30/54 为已准入
  正文；未准入的 1 条 SemiAnalysis 和 24 条 IBKR 项保持明确缺口，不被伪装为正文。已准入的
  SemiAnalysis 与 IBKR 文档均有 `chain` 成功处理记录；本轮结果为 0 个已配置概念的新增观测，
  表示没有相关命题事实而非读取失败。
- 通用 `data cutover-check --consumer evidence_chain` 会比较本任务范围外的
  `structured_observations`；该 mismatch 不能作为非结构化 `evidence_chain` 的发布门。2026-09-01
  以真实行业配置在隔离 Workflow memory 重放 platform no-LLM 报告：77,957 字、26 条 claim
  assessment、1,401 条引用均可重放。当前受管文档中 130 条引用具有 immutable document/version，
  另有 1,271 条历史证据在 document-version 存储建立前已固化为带 source URL、实体、时间与原文片段的
  `evidence_snapshot`；它们被明确标识而非冒充完整文档，且没有不可重放引用。已通过独立复核、发布和
  `platform → legacy → platform` 演练，发布记录为 `1cdc59f3f4c14d11b35d4a0867b275c6`。

### 14.3 验收记录（2026-08-30）

- `assess_ibkr_news_with_fallback()` 作为唯一的 acceptance failover 入口，CLI 的
  `data source-acceptance --source ibkr_news` 同样使用它。IBKR 成功完成（包括零新闻）不会调用
  Yahoo；整源不可用按默认 PEAD 范围调用，单切片失败只传入失败 ticker。
- Yahoo 不继承 IBKR 的可发布结论，仍独立执行标题主体、标题锚点正文、质量和人工标题/URL 审阅门；
  验收入口不写 document、evidence、Agent、Workflow、订单或交易数据。

### 15.1 验收记录（2026-08-30）

- 以 `ATS_STRUCTURED_SECTOR_AGENT_MODE=platform`、`ATS_STRUCTURED_SECTOR_FUNDAMENTALS_MODE=platform` 和
  `ATS_STRUCTURED_SECTOR_CONSENSUS_MODE=platform` 运行
  `sector review ai_hardware --no-llm --no-report`，实际装配成功退出。上下文包含 6 个 layer、
  18 个 PEAD block、11 个 insight、19 个 event；区域层使用迁移后的台湾财政部与韩国 ECOS 数列，
  基本面与 Consensus 走相应的受管读取入口，Chain 层读取已迁移的 evidence 投影。
- 运行中发现 `--no-llm` 对横截面相对判断错误地把 entity 映射当作 cluster 列表，已改为显式返回
  `unknown`，而非伪造排名；`test_no_llm_cross_section_judge_returns_explicit_unknowns` 覆盖此路径。
- `trendforce_dram` 没有直连 Sector dataset 或注入 `_snapshots()`；拓扑测试确认其只能先经
  Chain 的 `hbm_pricing`/`supply_tightness` claim evidence，再由 `_chain_evidence()` 进入上下文。
  即时价格仍是 runtime 输入，未被当成持久化数据验收。
- 已生成完整 LLM Sector 报告，但本次以确定性 `unknown` 判读保留 Chain 证据：默认路径的
  `evidence_adjudicator`（DeepSeek）在网络层长期无响应，普通 `sector review` 会阻塞在该子调用。
  这不是数据缺失；报告明确把共同命题标为 evidence insufficient，未将其伪造成支持或反驳。
  用户已确认这一受控降级可作为 15.1 的完成标准；`sector_agent` 仍保持 `shadow`，其最终 release 仍由 task 17.1 决定。

### 15.2 验收记录（2026-08-30）

- `ATS_STRUCTURED_PEAD_GRAPH_MODE=platform` 下，NVDA `Q2 FY2027` 的受管事件包精确选出
  SEC earnings release、SEC regulatory filing（10-Q）和 transcript 的不可变 document/version；平台读取只读取
  已保存的 `local_path`，不会在评分时触发 SEC、网页或 transcript 抓取。单元测试也断言此模式不调用 legacy transcript fetch。
- 使用平台 PEAD 财务与 Consensus 模式，实际运行 `pead prep NVDA --no-llm` 与
  `pead score NVDA --no-llm` 均正常退出，生成 FY2027 Q2 的 prep、scorecard 与报告。运行中的
  ThetaData 连接拒绝和 TWS option probe 仅属于 runtime 期权输入；它们未替代或污染受管财务、Consensus、官方文档读取。
- `pead_research`（14.1）提供已受管的 TrendForce/SemiAnalysis 研究洞察，`pead_monitor` 使用平台
  `ibkr_news` 文档读取 NVDA 新闻；后者实际运行 `pead monitor NVDA --no-llm` 成功读取 3 个新事件。
  研究洞察、monitor 结果和最终报告均属于 Workflow memory，它们引用的数据资产仍可由 document/version 追溯。
- 为避免 publisher-only 新闻误入 ticker，上游 Chain 现保留发布机构实体，同时只在标题验证通过时增加
  ticker 实体关联；增量 IBKR 采集后迁移了 10 份文档、10 个版本、16 条实体关联及 65 条 evidence，两个迁移 manifest
  均 source/target digest 一致。`pead_monitor` 当时继续为 `shadow`；后续的消费者发布验收见
  `DATA_CONSUMER_RELEASE_RECORDS.md`。
- 后续以完整 LLM 重新运行 NVDA `prep` 与 `score`：最终 scorecard 为 `+0.20 / +1.2`（中性观望），
  已写入完整 Surprise Scorecard。发现 prep 为期权 expiry 保存的下一次日历财报日会错误带入 score 报告头，
  已修复为 platform event package 的 release（其次 filing/transcript）发布日期；回归测试覆盖该场景，最终报告正确显示 `2026-08-26`。

### 10.1 / 17.1 发布验收补充（2026-08-31）

- 使用相互隔离的临时 memory 数据库，按 `legacy` 与 `platform` 分别运行 NVDA `Q2 FY2027` 的 no-LLM prep 和 score；两者的 scorecard 都是 `0.0 / 1.2 / 中性观望`，均未产生订单。
- legacy 的 score 报告头保留错误的下一次日历财报日 `2026-11-17`，且 transcript discovery 记录 Tavily TLS 失败；platform 则选择本季已准入的 SEC release `@74410a311c875bb1`、SEC 10-Q `@ff7ebb72ec311c38` 和 DefeatBeta transcript `@94d79efe7b3adcf2`，事件日为 `2026-08-26`。
- 该差异以 `governed_event_binding_upgrade` 保存为消费者输出 comparison，并写入独立 lineage/period/freshness/output verification；发布 assessment 通过后，`pead_graph` 完成 `platform → legacy → platform` rollback drill 并发布为 `platform`。Workflow dossier、报告和建议仍仅保存于 memory。

### 16.1 验收记录（2026-08-31）

- `chief_graph` 的输入合同明确区分：上游 Agent 已发布的财务、Consensus、文档与证据产品为持久输入；
  broker portfolio/execution state 是 runtime 输入；Chief context、dossier、决策、审批、交易和 run record
  仅为 `ats.memory` 输出。没有把后者登记为结构化或非结构化数据产品。
- 使用 `execute=False`、`dry_run=True`、`use_llm=False`、`offline=True` 实际运行 Chief 决策图；运行正常结束，
  给出“无行动 — 零决策”，未连接 broker、未调用外部模型、未进入审批或下单节点。上游实际数据路径的
  完整性由 13–15 的各直接/组合消费者验收承担，而不是由 Chief 重复伪造一份新旧数据对账。
- `tests/test_workflow_data_cutover.py` 验证 Chief 在装配开始时读取边界元数据，且 memory 输出不经 data router
  转发；隔离 release overlay 的 apply/rollback drill 证明 `chief_graph` 能单独从 `shadow` 回退到 `legacy`，不影响
  `runtime_scheduler`。这只是编排回退记录，不代表其可发布为 `platform` 数据消费者。

### 16.2 验收记录（2026-08-31）

- `_daily()` 在运行任何阶段前读取 `runtime_scheduler` 的边界合同；其持久输入是官方披露、研究/新闻文档与
  Chain source products，财报日历和市场时段为 runtime 输入，报告、score、观察、决策与 run state 保持在 memory。
  源码拓扑测试同时断言 scheduler 使用统一的 `ats.data.runtime`、`ats.data.products` 和
  `ats.data.pipelines`，没有回退到已删除的 legacy 数据导入。
- 每日阶段现在由统一的 best-effort 边界顺序执行。新增 `schedule --now --no-llm` 安全验收入口；它仍运行
  同一套统一 products/runtime/pipeline 路径，却把事件触发、PEAD、Chief 和 intel digest 的外部模型调用全部关闭，
  且默认 `dry_run`。已实际执行一次该入口：Yahoo 兜底的远端数据集、IBKR 端口与沙盒外 Obsidian 写入不可达时均被
  记录为单阶段失败，调度调用仍正常返回，没有下单。
- 新增回归模拟新闻回填批次失败，随后 PEAD、技术、摘要、
  performance/risk、journal 与 Chief 阶段仍按顺序执行；故单一发布商或批次故障可记录、可观察，但不会中断其余
  数据路径或干运行决策收口。各下游实际数据读取已分别在 tasks 13–15 验收，scheduler 本身不重复采集或持久化它们。
- job 注册回归覆盖 daily、weekly、两个 PEAD score window 与 reconciliation；窗口的 dry-run 防护仍有效。
  隔离 release overlay 已演练 `runtime_scheduler: shadow → legacy` 回退；同样仅用于其编排状态，不能将其标成
  可发布的 `platform` 数据消费者。
