## 1. Baseline and architecture guards

- [x] 1.1 Inventory current public imports, CLI entrypoints, configuration files, repositories, and data-layer tables; record the baseline in the change documentation.
- [x] 1.2 Define the initial `ats.data.core` contracts for entity, source, lineage, quality, and ingestion-run metadata without changing existing runtime behavior.
- [x] 1.3 Add an architecture test that asserts the allowed dependency direction among adapters, pipelines, stores, products, runtime, workflows, and memory.
- [x] 1.4 Run the baseline import smoke tests and affected data-product tests; record results before moving implementations.

## 2. Unified namespace and compatibility facades

- [x] 2.1 Create the `ats.data.catalog`, `ats.data.products`, `ats.data.adapters`, `ats.data.pipelines`, `ats.data.stores`, `ats.data.runtime`, and `ats.data.compat` package skeletons.
- [x] 2.2 Move or re-export `DataProducts` and `get_data_products` through `ats.data.products` while keeping `ats.data_platform` as a compatibility facade.
- [x] 2.3 Add the structured data compatibility entrypoint under `ats.data` while keeping `ats.structured` imports working without duplicate business logic.
- [x] 2.4 Add tests proving old and new public imports resolve to the same implementation and preserve existing return contracts.
- [x] 2.5 Run all data-product, CLI discovery, and representative Agent/Workflow tests before proceeding.

## 3. Unified catalog and configuration

- [x] 3.1 Define typed catalog models for sources, datasets, entities, adapters, schedules, quality gates, fallback rules, and runtime/excluded status.
- [x] 3.2 Add the `config/data/` directory with `catalog.yaml`, `structured.yaml`, `unstructured.yaml`, `schedules.yaml`, and provider configuration templates.
- [x] 3.3 Implement one catalog loader that validates IDs, references, adapter registration, status values, and legacy overlay precedence.
- [x] 3.4 Map the current `structured_data.yaml`, `sources.yaml`, and `news_sources.yaml` into the catalog without changing their effective source behavior.
- [x] 3.5 Add read-only catalog and configuration validation commands with clear distinction between operational mutations and inspection.
- [x] 3.6 Test legacy configuration loading, new configuration loading, invalid references, duplicate IDs, and runtime/excluded sources.

## 4. Structured data implementation migration

- [x] 4.1 Move structured catalog and registration logic into `ats.data.catalog` and `ats.data.adapters.structured` with compatibility exports from `ats.structured`.
- [x] 4.2 Move structured ingestion, admission, normalization, release, and quality orchestration into `ats.data.pipelines.structured`.
- [x] 4.3 Move the structured repository and artifact ownership into `ats.data.stores.structured` while preserving the current SQLite schema and query semantics.
- [x] 4.4 Move structured discovery, selection, derivation, and reporting surfaces into `ats.data.products.structured`.
- [x] 4.5 Verify SEC/company financials, defeatbeta `stock_statement`, Consensus, regional series, and evidence-derived observations through source-level smoke tests.
- [x] 4.6 Run structured query, as-of/vintage, quality, release/rollback, compatibility, and consumer regression tests; stop the migration on any reconciliation failure.
- [x] 4.7 Implement the ordered company-financials report-package chain (defeatbeta → yfinance → SEC Facts → issuer IR); retain raw lineage and reporting currency, prohibit runtime market persistence, preserve TSM ordinary-share versus ADR EPS, market-adjusted versus issuer EPS, and official versus Provider debt semantics; repair pre-governance persisted series through a backed-up, latest-vintage-aware semantic migration; and verify AMZN/MSFT/KLAC/TSM current-quarter coverage in isolated and production-compatible runs.

## 5. Unstructured data implementation migration

- [x] 5.1 Classify current SEC, earnings release, transcript, RSS, newsletter, research, and news modules as adapters, pipelines, stores, or products.
- [x] 5.2 Move provider access into `ats.data.adapters.unstructured` and keep fetching/parsing separate from persistence and consumer views.
- [x] 5.3 Move document admission, normalization, extraction, chunking, and evidence preparation into `ats.data.pipelines.unstructured`.
- [x] 5.4 Move document metadata, immutable versions, chunks, aliases, and source cache ownership into `ats.data.stores.unstructured` without deleting existing files or tables.
- [x] 5.5 Expose unified unstructured document, evidence, search, and company-package products under `ats.data.products.unstructured`.
- [x] 5.6 Run document integrity, entity association, deduplication, lineage, transcript, earnings release, SEC filing, RSS, and research-source regression tests.

## 6. Storage ownership and memory separation

- [x] 6.1 Define repository interfaces for structured observations, artifacts, documents, evidence, and ingestion runs under `ats.data.stores`.
- [x] 6.2 Route data-layer reads and writes through the new repositories while keeping the current SQLite file and legacy tables available.
- [x] 6.3 Leave trading decisions, trades, performance, dossiers, and Agent/Workflow run memory under `ats.memory` and remove new data-layer schema additions from `memory.store`.
- [x] 6.4 Add dual-read or reconciliation checks where old and new repositories share legacy tables, with explicit failure reporting.
- [x] 6.5 Test database initialization, migration, restart, rollback, and existing Workflow behavior before considering physical database separation.

## 7. Phase-one documentation, staged rollout, and compatibility

- [x] 7.1 Update `docs/DATA_ARCHITECTURE.md` with the unified target tree, dependency diagram, ownership rules, and current migration status.
- [x] 7.2 Add or update the developer guide with component contracts, extension points, import rules, data flow diagrams, and test strategy.
- [x] 7.3 Add or update the operations guide with configuration locations, source add/remove workflow, trigger modes, validation/release/rollback commands, budgets, and acceptance criteria.
- [x] 7.4 Add or update the user guide with dynamic discovery, structured/unstructured query examples, runtime market boundaries, and Agent/Workflow usage patterns.
- [x] 7.5 Add documentation checks that verify command examples, configuration paths, compatibility status, and runtime/excluded wording against the implementation.
- [x] 7.6 Introduce per-source and per-consumer feature flags, shadow comparison, release criteria, and rollback records for staged cutover.
- [x] 7.7 Mark legacy modules and configuration paths as compatibility surfaces; do not remove or claim their consumers have migrated.
- [x] 7.8 Run the phase-one architecture and data-layer regression suite and publish an acceptance report that explicitly excludes physical data migration, consumer cutover, and legacy removal.

## 8. Legacy inventory and migration preparation

- [x] 8.1 Inventory every legacy data module, configuration alias, SQLite table, artifact/document location, and public import; map each to its target `ats.data` owner and rollback path.
- [x] 8.2 Inventory all Agent and Workflow consumers of legacy data paths, including PEAD, Sector, Evidence/Chain, Chief and scheduled entrypoints; record each consumer's target product/runtime interface and dependent sources.
- [x] 8.3 Define versioned migration manifests and backups for structured observations/artifacts and unstructured documents/versions/chunks/aliases/evidence, including stable IDs, batching keys, resume behavior, and reconciliation thresholds.
- [x] 8.4 Add dry-run validation that rejects an incomplete inventory, unowned legacy asset, missing backup, or migration plan without an explicit rollback path.

## 9. Persistent data migration and reconciliation

- [x] 9.1 Implement resumable, idempotent migration for legacy `measurement_*` and existing `structured_*` observations, vintages, derived records, artifacts, catalog metadata and ingestion runs into the owned data repositories without losing lineage or quality status.
- [x] 9.2 Implement resumable, idempotent migration for non-structured document metadata, immutable versions, source cache, chunks, aliases, evidence, and related asset references.
- [x] 9.3 Run source- and data-domain-level migration rehearsals against isolated copies; reconcile counts, IDs, hashes, period/vintage coverage, lineage, quality and consumer query results.
- [x] 9.4 Run the approved migration against the selected production data copies, record manifests and discrepancies, and keep legacy writes/reads recoverable until cutover is accepted.

## 10. Agent and Workflow consumer cutover

- [ ] 10.1 Move PEAD and company-fundamental consumers to `ats.data.products` / `ats.data.runtime`; compare old and new inputs, scores, reports and failure behavior in shadow mode, after the selected report package passes issuer/provider identity, core-field coverage and period semantics checks.
- [x] 10.2 Move Sector and macro/regional consumers to the unified products; verify source selection, derived calculations, freshness and report outputs in shadow mode.
- [x] 10.3 Move Evidence, Chain, research and document consumers to unified unstructured products; verify document identity, text/version selection, evidence lineage and report outputs in shadow mode.
- [x] 10.4 Move Chief and scheduled Workflow entrypoints to the unified consumer interfaces; complete end-to-end execution, release records and per-consumer rollback drills.
- [x] 10.5 以数据层发布证据重新收口来源和数据集的状态：保留已完成的消费者分类记录，但本轮只将通过原始 artifact/lineage、实体/时间、完整性、时效、质量与可复算检查的数据源范围发布为 `platform`；不将未切换消费者、编排边界或旧路径网络异常作为发布阻塞或成功证据。
- [x] 10.6 建立 TrendForce 文章、SemiAnalysis（IMAP/RSS）和 IBKR News 的来源基线：核对 catalog/旧实现/配置的实体或主题范围、时间窗口、原生标识、游标、限流、正文最低质量和 legacy 回退状态；明确 TrendForce 文章与 DRAM 合约价结构化数据集相互隔离。
- [x] 10.7 为三类来源补齐共享非结构化资产与覆盖账本的确定性测试：验证 provenance、canonical URL、内容 hash、发布时间、正文质量、去重、运行状态和 `partial`/`unreachable` 缺口均可追溯；验收不得触发 Agent、Workflow、订单或交易副作用。
- [x] 10.8 完成 TrendForce 文章新路径的索引发现、正文准入、付费墙/过期 RSS 缺口分类和隔离验收；输出按时间窗口和主题的覆盖、成功、失败、重复及质量报告，并仅在通过来源门槛的范围发布。
- [x] 10.9 完成 SemiAnalysis 的 IMAP/RSS 新路径：在同一时间窗口枚举所有匹配邮件和 RSS 项，以 Message-ID/UID、canonical URL 和内容 hash 去重，优先采纳可验证的邮件正文；未订阅时允许将可验证的预览正文作为 `partial` 资产发布，且版本、血缘与报告必须保留该完整性标签；隔离验收并按候选、准入、重复、失败和缺口发布报告。
- [x] 10.10 完成 IBKR News 的只读 TWS 新路径：按配置标的、provider 和时间切片采集并区分连接失败、权限/订阅缺失、provider/限流失败与真实零新闻；恢复 legacy 的动态 provider 覆盖，并确保扩展超时的底层请求使用 TWS wire 日期格式；提供不写入数据的诊断，输出连接、server version、可用 provider、conId、请求格式、API error 回调和 `historicalNewsEnd`/超时状态；隔离验收后输出覆盖/正文/时间/实体及失败原因报告。
- [x] 10.11 汇总三类来源的数据源级发布结论与回滚记录；仅对通过覆盖、来源真实性、正文、去重、时效、血缘和质量门的范围更新 source release 状态，未通过范围保持 `legacy`/`shadow`。本任务不得切换消费者或删除旧逻辑。
- [x] 10.12 新增 `yfinance_live_news`：按当前 PEAD 标的获取 Yahoo/yfinance 实时候选，与 defeatbeta Yahoo 日级镜像和 IBKR News 分开注册；以标题 ticker/公司名/实体别名执行严格主体相关性准入，正文再验证标题锚点，记录 `association_rejected`、正文缺口与去重血缘；在隔离验收中输出包含每个 PEAD 标的标题、URL、publisher、时间和主体判定的审阅清单，未经人工审阅不得写入 source release overlay 或切换消费者。

## 11. Legacy retirement and final acceptance

- [ ] 11.1 Freeze new writes and imports to each legacy path only after its data-domain and consumer cutovers pass; add enforcement tests for the retirement boundary.
- [ ] 11.2 Delete retired legacy modules, configuration aliases, duplicate repository logic and obsolete schema only one approved retirement object at a time; preserve backups, manifests and recovery tooling.
- [ ] 11.3 Run full data-layer, migration recovery, all affected Agent/Workflow end-to-end and regression suites after each retirement batch; stop on any reconciliation or behavior failure.
- [ ] 11.4 Publish a final production migration and cutover acceptance report covering data domains, consumers, retirement objects, stable observation evidence and rollback verification.
- [ ] 11.5 Reconcile the final implementation with this change, validate OpenSpec strictly, and archive only after no legacy retirement object remains pending.

## 12. Consumer topology and acceptance baseline

- [x] 12.1 建立并以实际 import/运行路径校验来源 → 数据产品 → Agent/Workflow 矩阵；区分直接读取、经 Evidence/Chain 间接读取、runtime 输入、编排边界与 deferred 输入。特别确认 TrendForce DRAM 经 Chain evidence 注入 `sector_agent`，而非由它直接查询 series。

## 13. Leaf consumers: structured data

- [x] 13.1 验证 `chain_regional` 正确读取台湾与韩国出口新路径，完成 level、yoy、mom、血缘与正常运行验收。
- [x] 13.2 验证 `macro_agent` 正确消费区域出口新路径，检查时间、单位、上下文输出与失败回退。
- [x] 13.3 验证 `pead_fundamentals` 正确消费已发布财务包，检查实体、报告期、EPS/债务口径、CapEx/现金流、派生指标和正常运行。
- [x] 13.4 验证 `sector_fundamentals` 正确消费公司财务新路径，检查轻量财务合同与失败回退。
- [x] 13.5 验证 `pead_consensus` 正确消费市场 Consensus snapshot，检查 known-at、期间、来源与正常运行。
- [x] 13.6 验证 `sector_consensus` 正确消费市场 Consensus snapshot，检查行业横截面输出与正常运行。

## 14. Leaf consumers: unstructured data

- [ ] 14.1 验证 `pead_research` 正确消费官方披露、TrendForce、SemiAnalysis、IBKR News；确认 SemiAnalysis `partial` 标签不被当作全文。
- [ ] 14.2 验证 `evidence_chain` 正确消费官方披露、电话会纪要、TrendForce、SemiAnalysis 与 IBKR News；检查 document/version、实体、时间、正文、证据血缘及正常运行。
- [ ] 14.3 验证 IBKR 故障时 Yahoo fallback 仅处理失败范围；IBKR 健康或零新闻时不调用 Yahoo，且 Yahoo 仍执行自身主体/正文质量门。

## 15. Composite analysis consumers

- [ ] 15.1 在 13.2、13.4、13.6、14.2 通过后，验证 `sector_agent` 的完整上下文与正常运行；分别确认区域序列、财务、Consensus 和 Chain 证据均来自新路径，并确认 TrendForce DRAM 仅通过 Chain evidence 进入上下文。
- [ ] 15.2 在 13.3、13.5、14.1、14.2 通过后，验证 PEAD Graph 的完整 prep/score/report 流程；检查财务、Consensus、release、filing、transcript、research/news 的新路径输入与运行结果。

## 16. Orchestration boundaries

- [ ] 16.1 在上游直接消费者通过后，验证 `chief_graph` 能正确汇总已发布数据产品，同时确认 dossier、决策和交易状态仍留在 memory、未被误判为数据层输入。
- [ ] 16.2 在上游 Agent 通过后，验证 `runtime_scheduler` 能正确触发新路径采集与消费流程，并完成失败回退与正常运行验收。

## 17. Per-consumer release records

- [ ] 17.1 为每个消费者保存真实新路径输入、输出、数据血缘、失败处理、回滚结果和通过/未通过原因；仅对通过者调整 consumer release 状态。runtime 与 deferred 输入按其边界验收，不以无持久化数据为失败。
