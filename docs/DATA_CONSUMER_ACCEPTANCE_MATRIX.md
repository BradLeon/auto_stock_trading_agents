# 数据消费者验收矩阵

本矩阵是 `unify-data-layer-architecture` 的消费者切换基线。它以实际导入和运行路径为准，
而不是仅以 `config/data/consumer_release.yaml` 的计划状态为准。数据源是否可发布由来源级
质量门决定；本表只安排各消费者在其切换时应验证的读取、输出和回滚。

最后核对：2026-08-30。对应自动化检查：`tests/test_consumer_topology.py`。

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
| `sector_fundamentals` | 直接持久化数据读取 + runtime | `ats.agents.sector.assemble._snapshots()` → `fundamentals.fetch_light()`；该轻量接口当前仍取得即时 provider 指标，尚未等同于完整财务包 | `company_financials` 的轻量合同，以及 runtime 市值/估值 | 13.4 |
| `pead_consensus` | 直接持久化数据读取 | `ats.graph.pead.prep_fetch()` → `ats.data.consensus.fetch()`；兼容入口按 consumer mode 读取平台 snapshot | `market_consensus` | 13.5 |
| `sector_consensus` | 直接持久化数据读取 | `ats.agents.sector.assemble._snapshots()` → `consensus.fetch(..., consumer="sector_consensus")` | `market_consensus` | 13.6 |
| `pead_research` | 直接非结构化数据读取，写入仍在 memory | `ats.agents.pead.research.run()` → `research.stored_articles(..., consumer="pead_research")` → `get_unstructured_read_router()`；processing lease、insight/event 写入仍属于 memory | 官方披露、TrendForce、SemiAnalysis、IBKR News 的共享文档资产 | 14.1 |
| `evidence_chain` | 直接非结构化数据读取，证据判断写入仍在 memory | `ats.chain.report.render()` → `get_unstructured_read_router(consumer="evidence_chain")` | release、filing、transcript、research/RSS/news | 14.2 |
| IBKR→Yahoo | 失败范围 fallback | `ats.data.pipelines.unstructured.source_acceptance`；IBKR 健康或真实零新闻时不得调用 Yahoo | `ibkr_news` 主来源；`yfinance_live_news` 仅失败切片兜底 | 14.3 |

## 组合消费者与编排边界

| 消费者 | 类型 | 实际入口与依赖 | 特殊边界 | 本轮任务 |
| --- | --- | --- | --- | --- |
| `sector_agent` | 直接持久化数据读取 + 间接证据读取 + runtime | `assemble.build()` 直接调用 `regional.fetch(consumer="sector_agent")`，并在 `_snapshots()` 调用基本面、Consensus 与价格 snapshot；`_chain_evidence()` 读取 Chain claim evidence | `trendforce_dram` / `industry_dram_contract_price` **不是** sector 的直接 dataset。`config/sources.yaml:dram_contract_price` 先作为 `hbm_pricing`、`supply_tightness` 的 Chain 来源，再经 claim evidence 注入 `_chain_evidence()` | 15.1 |
| PEAD Graph | 直接持久化数据读取 + runtime | `ats.graph.pead` 的 prep/score：财务、Consensus、release/filing/transcript/research/news；价格、期权、日历为 runtime | runtime 价格/期权不得写入结构化库；dossier/score/report 留在 memory | 15.2 |
| `chief_graph` | 编排边界 | `ats.graph.chief.assemble_context()` → `workflow_data_boundary("chief_graph")`，随后汇总 Agent/Workflow memory 产物 | Chief dossier、决策、交易状态属于 memory，不是结构化或非结构化输入 | 16.1 |
| `runtime_scheduler` | 编排边界 | `ats.runtime.scheduler._daily()` → `workflow_data_boundary("runtime_scheduler")`，并触发采集 pipeline、PEAD、Chief 等 | 负责触发与失败隔离；自身没有可替代的持久数据读模型 | 16.2 |

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
