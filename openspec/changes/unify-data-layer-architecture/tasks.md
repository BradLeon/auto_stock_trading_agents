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

## 7. Documentation, staged rollout, and deprecation

- [x] 7.1 Update `docs/DATA_ARCHITECTURE.md` with the unified target tree, dependency diagram, ownership rules, and current migration status.
- [x] 7.2 Add or update the developer guide with component contracts, extension points, import rules, data flow diagrams, and test strategy.
- [x] 7.3 Add or update the operations guide with configuration locations, source add/remove workflow, trigger modes, validation/release/rollback commands, budgets, and acceptance criteria.
- [x] 7.4 Add or update the user guide with dynamic discovery, structured/unstructured query examples, runtime market boundaries, and Agent/Workflow usage patterns.
- [x] 7.5 Add documentation checks that verify command examples, configuration paths, compatibility status, and runtime/excluded wording against the implementation.
- [x] 7.6 Introduce per-source and per-consumer feature flags, shadow comparison, release criteria, and rollback records for staged cutover.
- [x] 7.7 Mark legacy modules and configuration paths deprecated only after all affected consumers pass reconciliation and full regression tests.
- [x] 7.8 Run the complete data-layer test suite and publish a migration acceptance report before removing any duplicate implementation.
