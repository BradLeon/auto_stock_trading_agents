> 说明：1–9 记录旧九项 benchmark 实现基线的已完成工作。2026-09-17 审阅后，默认体系调整为十一项 benchmark；与新版 proposal/spec/design 冲突的旧行为由 10–14 的未完成迁移任务取代。旧 synthetic fixture 只保留为解析测试，不再构成 platform 验收证据。

## 1. Source Feasibility and Frozen Acceptance Baseline

- [x] 1.1 Run a read-only source feasibility probe and document the free public Git/CSV/JSON/README routes; record AA entitlement failure as optional-only without persisting secrets.
- [x] 1.2 Capture allowed real-source validation snapshots for the nine benchmark routes, with source URLs/commits, fetched-at, content hashes, update contracts and saving constraints; fixtures remain test-only.
- [x] 1.3 Capture official release/model-card fixtures for all nine Labs, including at least one self-reported score, one missing self-report, one model alias, one withdrawn/unavailable model and one non-comparable OSWorld result.
- [x] 1.4 Encode the approved 2026-09-16 nine-by-nine review table and benchmark method notes as a frozen acceptance fixture, with Alibaba/Qwen replacing ByteDance and explicit NA reasons.

## 2. Registries and Structured Persistence

- [x] 2.1 Add configuration registries for the nine Labs, model aliases/lineages, flagship tier and replacement rules, nine benchmark identities, score ranges, metric semantics, B eligibility and source priority.
- [x] 2.2 Extend structured reference entities/relations and repository migrations to persist model identities, flagship effective periods, benchmark method versions, comparability groups and optional bridge metadata without breaking existing observations.
- [x] 2.3 Add versioned benchmark score observation metadata for inference configuration, sample/statistical fields, source identity and score/published/known/fetched timestamps, with exact artifact-slice lineage.
- [x] 2.4 Add semantic coverage-state persistence and history for `not_evaluated`, `pending_publication`, `not_self_reported`, `not_applicable`, `non_comparable`, `source_unavailable` and `withdrawn`.
- [x] 2.5 Test migrations, uniqueness, idempotency, revision vintage, artifact binding, duplicate active flagship rejection and preservation of existing structured datasets.

## 3. Model and Benchmark Source Adapters

- [x] 3.1 Implement the public benchmark adapter using official Git/CSV/JSON/README contracts; keep Artificial Analysis as an explicitly authorized optional adapter only.
- [x] 3.2 Implement official Lab release/model-card discovery for the nine Labs, including stable model identity, aliases, availability/withdrawal events and self-reported benchmark evidence.
- [x] 3.3 Implement normalization for all nine benchmark methods and scores, including OSWorld Partial versus Strict identities, reasoning effort/configuration, percent/range validation and source-type separation.
- [x] 3.4 Implement conservative method-diff classification for compatible, breaking and review-required revisions across task set, harness, grader, metric semantic and inference protocol.
- [x] 3.5 Register adapters in the structured source registry and add fixture contract tests for successful parse, no change, added score, revised score, method change, ambiguous model, range failure, source outage and withdrawal.
- [x] 3.6 Replace the AA-first runtime path with source-specific public adapters for LiveBench, Terminal-Bench 4.0, Terminal-Bench-Science, OSWorld, AutomationBench, EnterpriseOps-Gym, SciCode, CritPt and MMMU-Pro coverage; preserve fixture-only tests and expose source transport/lineage.

## 4. Incremental Discovery, Scheduling, and Quality Gates

- [x] 4.1 Add independent daily model-discovery and benchmark-score probe entry points that prefer one bulk fetch per source cycle and emit typed change events.
- [x] 4.2 Add hot-observation scheduling for days 0–30 daily, days 31–90 every three days and later weekly, plus a weekly full model/score/method audit and request-budget/backoff controls.
- [x] 4.3 Extend structured ingestion quality checks to quarantine unresolved model identities, missing method identity, out-of-range scores, duplicate active flagships and breaking/review-required method changes.
- [x] 4.4 Ensure `no_change` runs do not duplicate observations, score revisions append vintages, unavailable sources preserve the latest accepted value, and weekly full-audit misses are surfaced as operational warnings.
- [x] 4.5 Add schedule/source/dataset registrations in `config/data/structured.yaml` and `config/data/schedules.yaml`, including freshness thresholds, source priority, retention/licensing metadata and direct platform acceptance gates.

## 5. Governed Capability Data Product and Queries

- [x] 5.1 Implement a Frontier AI capability DataProduct that selects current or historical flagship cohorts, produces the fixed nine-by-nine matrix, retains exact NA states and exposes all candidate observations with selection reasons.
- [x] 5.2 Implement latest/all-vintage/as-of queries by Lab, model, benchmark method, configuration, source and comparability group, with historical cohort replay and immutable lineage.
- [x] 5.3 Enforce comparability-group boundaries in cross-sectional, time-series and delta queries; return separate series and non-comparable warnings across breaking versions.
- [x] 5.4 Implement governed current/previous global-frontier selection and separate panel-frontier diagnostics, preserving source conflicts and preventing averaging or silent self-report substitution.
- [x] 5.5 Return point estimates, sample sizes, confidence intervals or explicit `confidence_unavailable` inputs needed by A/B calculations, and test all selection/as-of/comparability cases.

## 6. Raw Capability A/B Evaluation

- [x] 6.1 Implement versioned pure-function evaluation for A using same-group current versus previous global frontier, `delta_min = max(2pp, 0.2 × historical_sd)`, and confirmed/provisional/non-comparable/insufficient-history states.
- [x] 6.2 Implement versioned pure-function evaluation for B using registry eligibility, a default 50% threshold, confidence-lower-bound confirmation, point-estimate provisional crossing, not-crossed gap and not-applicable states.
- [x] 6.3 Enforce the agreed semantics: LiveBench and AutomationBench Public A-only, OSWorld Partial A-only, strict/binary/accuracy routes B-eligible only when registered, and no human/economic threshold output without a predefined governed threshold.
- [x] 6.4 Add deterministic tests for confirmed/provisional A expansion, threshold crossing/reversion, NA exclusion, source conflicts, method-version breaks, panel/global frontier divergence and absent confidence data.

## 7. Observer, Narrative, and Visual Review Artifacts

- [x] 7.1 Add an independent `raw_capability` Evidence Observer and compact Agent context with claim/version, cohort/method versions, A/B conclusions, coverage, freshness, facts, warnings, observation IDs and lineage pointers.
- [x] 7.2 Generate the Chinese Markdown report with natural-language A/B answers first, followed by the unified matrix, A decision table, B three-level threshold/model/evidence table and benchmark method cards.
- [x] 7.3 Implement comparable global-frontier history small multiples with model annotations, series breaks at method changes, sparse time ticks and panel/global frontier disclosure.
- [x] 7.4 Implement a nine-by-nine coverage/score heatmap that visually distinguishes numeric, NA reason, self-report and non-comparable cells without treating missing values as zero.
- [x] 7.5 Implement the B current-score versus highest-defined-threshold chart with confirmed, provisional and not-crossed encoding, and suppress undefined human/economic threshold sections.
- [x] 7.6 Reuse Chinese font glyph validation and produce PNG, CSV/JSON and sidecars whose rows hash, observation IDs, method versions and source notes match the report packet.

## 8. L1 Integration and Reproducibility

- [x] 8.1 Register `raw_capability` as an independent `ai_hardware/L1_app` evidence section in sector configuration and layer runner without altering production or commercialization claim/version/status.
- [x] 8.2 Add event-triggered incremental recomputation for eligible flagship, score add/revision, method change, withdrawal and A/B state change, while keeping unaffected benchmark outputs stable.
- [x] 8.3 Extend snapshot manifest and offline replay so the same artifacts reproduce cohort selection, nine-by-nine matrix, accepted rows hash, A/B decisions and chart data without network access.
- [x] 8.4 Add CLI/operations entry points for model discovery, benchmark probing, weekly audit, current report, historical `as_of` replay and source/coverage health inspection.

## 9. End-to-End Acceptance, Platform Release, and Documentation

- [x] 9.1 Backfill the current benchmark/model history into an isolated database and reconcile every available value, NA reason, source identity and method version against the frozen review fixture.
- [x] 9.2 Run real-source acceptance for each public transport (Git, CSV, JSON, README), score addition/revision, no-change, source unavailable and method-change paths; document observed latency, validators/commit path and request budget.
- [x] 9.3 Run raw-capability unit/integration tests plus existing structured ingestion/query, production Observer, commercialization Observer, Ramp, RPS, Anthropic Economic Index and OpenRouter regression suites.
- [x] 9.4 Generate a reviewable report with working embedded images, readable Chinese glyphs, valid sidecar links, concise benchmark explanations and textual summaries for each visualization.
- [x] 9.5 Add `docs/FRONTIER_AI_RAW_CAPABILITY_OPERATIONS.md` covering source policy, model eligibility, benchmark versions, schedules, NA semantics, A/B rules, quality gates, replay, incidents and rollback; update `docs/EVIDENCE_OBSERVER.md`.
- [x] 9.6 After every acceptance gate passes, configure the dataset and Observer as `platform`, verify the formal L1 report includes raw capability by default, and prove source/revision failure does not break the two existing L1 Observers.

## 10. Eleven-Benchmark Registry and Source Contract Migration

- [x] 10.1 Replace EnterpriseOps-Gym in the default panel with Toolathlon Verified and add SpreadsheetBench 2 and Humanity's Last Exam, producing the governed eleven-benchmark registry while retaining EnterpriseOps history outside the default matrix.
- [x] 10.2 Update benchmark method cards, score ranges, metric semantics, A/B eligibility and measurement scopes for all eleven benchmarks; record FrontierMath Erdős, FrontierScience Research, collective intelligence and safety alignment as explicitly out of scope for this version.
- [x] 10.3 Add exact-model-version eligibility and alias tests so nearby variants such as GLM-5.3-Flash cannot silently populate the GLM-5.3 flagship column.
- [x] 10.4 Encode the source priority `benchmark_maintainer/independent_third_party > competitor_reported > lab_self_reported`, retaining all candidates and selection reasons without averaging or source-class promotion.
- [x] 10.5 Add source-policy metadata and fail-closed gates for login, subscription, paywall, robots/terms, request budget and parser drift; permit auditable manual imports only with public source lineage and immutable artifacts.

## 11. Real Public Adapters and Event Ledger

- [x] 11.1 Implement or revise the low-frequency public structured-page adapter for AutomationBench-AA, SciCode, CritPt, MMMU-Pro and Humanity's Last Exam, using schema/content hashes and no paid Artificial Analysis API dependency.
- [x] 11.2 Implement Toolathlon Verified official-leaderboard ingestion with verified-status, harness, sample/statistical fields and exact model identity.
- [x] 11.3 Implement SpreadsheetBench 2 project/paper and official model-card event ingestion, preserving exact-check versus visualization-judge method identities and sparse coverage states.
- [x] 11.4 Replace the single-score AutomationBench and OSWorld paths with a dual output: one declared uniform matrix comparability group plus a versioned event ledger keyed by task set, harness, tool setting, grader and metric semantic.
- [x] 11.5 Add Scale HLE and Lab release/model-card event adapters as lower-priority or confirmation evidence, preserving confidence intervals and source fingerprints separately from the Artificial Analysis uniform matrix.
- [x] 11.6 Add frozen real-source parser tests and immutable raw snapshots for every new/changed transport, including no-change, score revision, source-policy-blocked, parser drift, event addition and exact-model mismatch cases.

## 12. Eleven-by-Nine Data Product, Evaluation, and Reporting

- [x] 12.1 Migrate the governed DataProduct and structured queries from the nine-by-nine baseline to the fixed eleven-by-nine matrix, returning semantic NA states without dropping rows or model columns.
- [x] 12.2 Add event-ledger query and packet output that keeps non-comparable AutomationBench, OSWorld and SpreadsheetBench evidence visible without mixing it into the uniform ranking.
- [x] 12.3 Re-evaluate A/B eligibility and threshold calculations for Toolathlon Verified, SpreadsheetBench 2 and Humanity's Last Exam; keep AutomationBench-AA and LiveBench A-only and OSWorld Partial ineligible for B.
- [x] 12.4 Update the Chinese narrative to answer A and B directly, identify uneven frontier expansion, distinguish confirmed/provisional/not-crossed/insufficient-coverage states and cite the minimum supporting model, score, delta/gap and method version.
- [x] 12.5 Replace the nine-by-nine report table and heatmap with eleven-by-nine outputs, add event-ledger presentation, and update frontier/threshold visualizations, CSV/JSON, sidecars, rows hash and compact Agent context from the same accepted observation set.
- [x] 12.6 Update manifest replay and historical `as_of` tests to reproduce the eleven-by-nine matrix, event ledger, source selections, A/B decisions and visualization data without network access.
- [x] 12.7 Represent each exact model release as one matrix column; select the highest observed same-source-priority configuration for that release while retaining all effort/harness variants and rejecting fuzzy product-variant merges.

## 13. Real-Data Backfill and Acceptance

- [x] 13.1 Import the 2026-09-17 reviewed public observations and coverage states into an isolated database with full source/artifact lineage; do not import the review CSV as an authority when the original public artifact is available.
- [x] 13.2 Reconcile the generated eleven-by-nine matrix and event ledger against `var/reports/frontier_ai_raw_capability_review/2026-09-17/`, including every numeric value, NA reason, source class, exact model version and comparability note.
- [x] 13.3 Prove the source-selection order with conflict fixtures covering maintainer/independent third party, competitor report and Lab self-report, and prove lower-priority evidence remains queryable but cannot overwrite the uniform matrix.
- [x] 13.4 Run real-source acceptance for Git, JSON, official leaderboard HTML, public structured-page payloads, papers and release/model-card events; record validators/hashes, observed latency, request budget and policy-gate outcome.
- [x] 13.5 Run raw-capability unit/integration tests and the existing structured data, production, commercialization, Ramp, RPS, Anthropic Economic Index and OpenRouter regression suites.
- [x] 13.6 Generate a final review report with working images, Chinese glyph validation, eleven benchmark method cards, concise chart interpretations and no synthetic values in any platform artifact.

## 14. Platform Configuration and Operations Documentation

- [x] 14.1 Update `config/data/structured.yaml`, schedules and source registry for the eleven-benchmark panel, source-specific frequencies, event probes, hot-observation windows, policy gates, freshness and retention metadata.
- [x] 14.2 Update `docs/FRONTIER_AI_RAW_CAPABILITY_OPERATIONS.md` and `docs/EVIDENCE_OBSERVER.md` with the final benchmark panel, source routes, priority rules, event ledger, policy fallback, NA semantics and incident procedures.
- [x] 14.3 After all new acceptance gates pass, publish the migrated dataset and Observer directly as `platform`, verify the formal L1 report uses the eleven-benchmark system by default, and prove a blocked or drifting source only degrades its own evidence slice.
