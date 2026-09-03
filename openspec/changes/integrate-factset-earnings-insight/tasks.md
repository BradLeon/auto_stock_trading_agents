## 1. Catalog, configuration, and storage contracts

- [x] 1.1 Register `factset_earnings_insight_doc` and `factset_earnings_insight_metrics` in `config/data/sources.yaml`/`unstructured.yaml`, including stable URL, internal-use policy, 10-day freshness, expected PDF MIME, timeout, retry, and artifact retention settings; extend catalog tests to assert the resolved contracts.
- [x] 1.2 Register dataset `sp500_earnings_insight`, the V1 metric IDs/units/dimensions, entity `SP500`, all eleven `GICS_*` entities, and FactSet label aliases in `config/data/structured.yaml`; add catalog validation for duplicate IDs, invalid units, and missing entity aliases.
- [x] 1.3 Add independent rollout entries for document collection, `index_core`, `sector_core`, `macro_factset`, and `sector_factset`, defaulting all new data paths to shadow/off as appropriate; test that source and consumer modes can be changed independently.
- [x] 1.4 Add `factset_refresh_at: "08:10"` and `factset_refresh_tz` to schedule configuration and typed settings, including startup validation that refresh precedes Saturday weekly review in the same timezone.
- [x] 1.5 Extend the unstructured store schema/repository with the generic document-artifact association (`role`, page, normalized region, MIME, hash) and migration tests covering existing databases and idempotent upserts.
- [x] 1.6 Extend structured evidence links with text/image anchor kind, page, optional character offsets, chart ID, and region JSON; verify existing text-only evidence rows remain readable after migration.
- [x] 1.7 Register `earnings.revision.improved_sector_count` as the thirty-first V1 metric and add the FactSet provider mapping, count unit, SP500 scope, target-quarter/estimate-state semantics, and required `comparison_date`, `revision_direction`, and `sector_total` dimensions.

## 2. Immutable report acquisition and document projection

- [x] 2.1 Introduce a FactSet source module under `src/ats/data/sources/` that follows the stable redirect and returns final URL, status, ETag, Last-Modified, MIME, bytes, byte count, fetch time, and source failure taxonomy without writing consumer-owned files.
- [x] 2.2 Validate `%PDF`, FactSet Earnings Insight title/date anchors, parseability, and bounded page count before admission; map failures to `unreachable`, `unauthorized`, `not_pdf`, or `parse_failed` and add response-level unit tests.
- [x] 2.3 Persist PDF bytes through the structured artifact store by SHA-256 and create one unstructured `research_article` document/version for `SP500`/`FACTSET`; link the `source_pdf` artifact and retain both stable and final URLs.
- [x] 2.4 Make repeated fetch and reprocessing idempotent: the same PDF hash must not create another document version, structured vintage, or processing projection, while a new extractor version may create a new run and candidate set.
- [x] 2.5 Extract and persist all page text with page offsets/section headings, inventory page images and chart regions, and retain independent PDF/text hashes; add a fixture proving prose and raster pages coexist in one document version.
- [x] 2.6 Add an operator-controlled local-PDF import path that uses the same validation/projection pipeline as network acquisition and stamps actual import time as `known_at`; do not retain the legacy Obsidian directory as a read dependency.

## 3. Index text extraction and admission

- [x] 3.1 Define typed FactSet candidate, report-period, estimate-state, metric-group, evidence-anchor, and extraction-run models, including raw token/value and extractor version.
- [x] 3.2 Implement document classification and report-phase detection (`pre_reporting`, `in_progress`, `substantially_complete`) using title, table-of-contents, and section anchors, with an `unknown_template` quarantine result for unrecognized layouts.
- [x] 3.3 Replace the monolithic legacy regex behavior with section-specific text extractors for reporting coverage; EPS/revenue scorecard and surprise; growth; margin; guidance; bottom-up EPS; forward/trailing P/E references; geography; ratings; and target upside.
- [x] 3.4 Normalize every candidate to canonical entity, target quarter/year or snapshot date, `estimated|blended|actual|not_applicable`, unit, and source metadata; parse “Ten of eleven sectors” and equivalent digit/word variants into `earnings.revision.improved_sector_count=10` with raw tokens, text evidence, `comparison_date`, `revision_direction`, and `sector_total=11`.
- [x] 3.5 Implement deterministic range, unit, period/state, and composition checks, including `1.0 ± 0.01` totals for distributions, non-negative integer guidance counts, and `0 <= earnings.revision.improved_sector_count <= sector_total`; absence must remain null with a reason rather than become zero.
- [x] 3.6 Implement duplicate evidence merging and conflict quarantine for distinct values sharing one observation identity; add a regression fixture for the 7.5%/7.7% revenue-growth discrepancy and assert neither value is silently selected.
- [x] 3.7 Publish admitted index candidates through the existing structured ingestion/release pipeline with document/version/text-span evidence links and an `index_core` quality manifest by applicable metric group.

## 4. Sector chart extraction and quality gates

- [x] 4.1 Add a chart registry keyed by normalized title, axes/legend anchors, expected columns, applicable report phase, and extractor version; do not use page number as the sole classifier.
- [x] 4.2 Add an optional deterministic local OCR adapter with explicit dependency discovery and `extractor_unavailable` status; ensure missing OCR skips sector extraction without blocking document or index processing.
- [x] 4.3 Implement the `082826` deterministic layout-table decoder over local raster/OCR evidence, so the scheduled pipeline supplies populated `ChartTable` instances rather than the current empty default; emit per-cell candidates with chart ID, one-based page, normalized bounding box, raw token, parsed value, and image-region evidence.
- [x] 4.3a Make chart columns report-applicability-aware: record `revenue_surprise` as `not_applicable` for `082826`, validate only its disclosed `eps_surprise` sector column, and never zero-fill an omitted source column.
- [x] 4.4 Normalize all sector labels to the eleven canonical GICS entities and reject tables with missing, duplicate, or unknown rows; add tests for label aliases and exact 11-row completeness.
- [x] 4.5 Implement chart-table validators for expected columns, value ranges, scorecard count reconciliation, and percentage compositions; quarantine the entire affected table rather than publishing a partial sector ranking.
- [x] 4.6 Publish passing sector candidates as a separate `sector_core` release partition with its own manifest, leaving it shadow when any annotated cell, row, or table check fails.

## 5. Acceptance corpus and source validation

- [x] 5.1 Complete the checked-in acceptance manifest for current report `082826`, recording its PDF hash, report metadata, selected text spans (including revision-breadth wording/count/total/comparison date where applicable), chart identities, expected applicability, and every applicable V1 sector cell without committing licensed PDF bytes. Historical manifests are optional regression assets, not release gates. Complete only after 5.1a–5.1c evidence is present.
- [x] 5.1a (A) Define and validate the `082826` golden-cell schema: `chart_id`, page, GICS entity, column, value, unit, raw token, period/state, exact image region, and review status; exclude the `SP500` aggregate row from Sector cells. Reject placeholder regions.
- [x] 5.1b (B) Persist all 231 confirmed `082826` golden cells plus explicit `not_applicable` fields in the checked-in manifest; prove the manifest expands to exactly 231 schema-valid cells.
- [x] 5.1c (C) Archive the eight review decisions and review provenance in the manifest/evidence report; record that each chart was confirmed and that unresolved cells would block release.
- [x] 5.2 Add synthetic PDF fixtures for CI and an internal-corpus runner that reports explicit skip reasons when licensed artifacts are unavailable.
- [x] 5.3 Run document acceptance for current report `082826` and require correct report date, immutable hash, idempotent import, complete page text/chart inventory, and zero unexpected template classifications.
- [x] 5.4 Run index acceptance for current report `082826` and require 100% expected applicable fields, period/state/unit correctness, evidence coverage, and explicit quarantine of annotated source conflicts.
- [x] 5.5 Run sector acceptance for current report `082826` and require 100% per-cell agreement, exact row/column completeness, and zero unresolved GICS labels before changing `sector_core` from shadow. Complete only after 5.5a–5.5d evidence is present.
- [x] 5.5a (D) Implement an independent `082826` PDF-raster decoder that never reads the golden manifest and produces chart candidates from original images only.
- [x] 5.5b (E) Emit eight populated `ChartTable` objects with all 231 applicable candidate cells, normalized units/period/state, raw OCR tokens, and exact per-cell image regions; record `not_applicable` rather than fabricating omitted columns.
- [x] 5.5c (F) Implement and invoke the golden-vs-candidate comparator; fail on missing, extra, duplicate, mismatched value/unit/period/state/page/region, or unresolved cells.
- [x] 5.5d (G) Run and archive the real `EarningsInsight_082826.pdf` acceptance report; require 231/231 matching cells before allowing `sector_core` to leave shadow.

## 6. Typed DataProducts and historical queries

- [x] 6.1 Add `src/ats/data/products/earnings_insight.py` with typed report, index, sector, status, warning, and lineage records; export it through the public data-products namespace.
- [x] 6.2 Implement `DataProducts.earnings_insight_snapshot(as_of=None)` to select one internally consistent released report snapshot, join governed document references, and perform no network or physical-table access from consumers.
- [x] 6.3 Return typed `registered_no_data`/`unavailable` when no release exists and return the last release with report age, latest refresh failure, and `stale` after the freshness boundary.
- [x] 6.4 Add latest, `as_of`, and all-vintage query/CLI coverage; prove that consecutive reports for the same target period remain distinct and that a backfilled PDF is invisible before its real ingestion `known_at`.
- [x] 6.5 Add observation-to-source tracing that resolves both text spans and chart regions, and ensure source/license metadata accompanies internal chart views.
- [x] 6.6 Implement a one-way compatibility mapper from `EarningsInsightSnapshot.index` to the existing `EarningsBackdrop` DTO, mapping `earnings.revision.improved_sector_count` to `sectors_higher`, and regression-test missing fields, conflicts, stale data, and estimated/blended/actual labels.

## 7. Macro and Sector workflow integration

- [x] 7.1 Refactor Macro assembly/review to obtain FactSet context exclusively through the DataProducts product behind `macro_factset`, with no network, PDF, or Obsidian access in the platform branch.
- [x] 7.2 Add Macro dual-read/shadow comparison output for value, period/state, report date, freshness, warnings, and rendered review text; validate the current `082826` report before platform cutover.
- [x] 7.3 Add the GICS sector matrix to Sector assembly behind `sector_factset` and label it explicitly as top-down market context distinct from AI-hardware layers and company evidence.
- [x] 7.4 Add consumer tests proving Chief, Risk, PEAD, Technical, and company evidence workflows do not directly query or independently weight the FactSet product.
- [x] 7.5 Add degradation tests proving unavailable FactSet data omits the corresponding conclusions without failing weekly review, while stale data is surfaced with its original report date.
- [x] 7.6 Complete the analysis-ready FactSet packet only after 7.6a–7.6c pass.
- [x] 7.6a Expose all 25 applicable released SP500 observations in eight named groups with report date, freshness, quality warnings, period/state, evidence anchors, and lineage; do not reduce them to the legacy `EarningsBackdrop` subset.
- [x] 7.6b Deterministically calculate EPS-minus-revenue growth, EPS-minus-revenue surprise, positive/negative guidance ratio and net count, and forward/trailing P/E deviations from five- and ten-year averages; retain every input observation ID.
- [x] 7.6c Select at most six bounded excerpts from governed page text for concentration, excluding major companies, GAAP/Non-GAAP, sector contribution, margin drivers, and valuation/ratings; retain version, page and character range and never send the full report body.
- [x] 7.7 Complete Macro quality integration only after 7.7a–7.7c pass.
- [x] 7.7a Supply the complete packet to Macro while retaining the compatibility backdrop; redefine offline as no network acquisition while still reading local DataProducts, regional data, and prior formal Macro memory.
- [x] 7.7b Persist a separate eight-part FactSet earnings-cycle assessment for growth quality, concentration, surprise drivers, guidance/margin consistency, valuation, analyst expectations, conflicts/limitations, and market/sector implications; reject metric IDs and page numbers absent from the supplied packet.
- [x] 7.7c Update the Macro Skill and report so facts and model interpretation are visibly separate, all 25 supplied observations and deterministic diagnostics are shown, every required question is answered, and important prose claims cite validated pages.
- [x] 7.8 Complete layered Sector quality integration only after 7.8a–7.8c pass.
- [x] 7.8a Finish all eight company-evidence layer verdicts first, then read the latest formal Macro review and the eleven-sector FactSet GICS context once for final synthesis; never pass either input into a layer analyst.
- [x] 7.8b Persist and render normal-Chinese answers for Macro background, FactSet background, agreements, divergences, and impact on cross-layer increase/decrease recommendations, including explicit not-released, shadow, stale, or missing reasons.
- [x] 7.8c Prove top-down context cannot mutate any existing layer verdict, company call, confidence, or evidence chain and that standard GICS sectors are never treated as AI-hardware supply-chain layers.
- [x] 7.9 Add focused automated tests for the `082826` packet values/excerpts, Macro prompt/persistence/report and citation filtering, offline local reads, Sector final-only comparison, unavailability reasons, and layer-result immutability.

## 8. Scheduling, operations, and observability

- [x] 8.1 Implement one pipeline entry point for acquisition, document projection, candidate extraction, validation, and independent eligible releases; emit run/report/partition counters and sanitized provenance without PDF text or signed URL parameters.
- [x] 8.2 Register `factset_weekly_ingest` in `src/ats/runtime/scheduler.py` for Saturday at the configured refresh time, with bounded misfire grace and one-run coalescing; verify it executes before the weekly Macro→Sector review.
- [x] 8.3 Add scheduler tests for timezone ordering, holiday/unchanged-report no-op behavior, failed refresh with previous release, and failed refresh with no release.
- [x] 8.4 Add operational commands/reports to inspect source status, list report hashes/versions, display partition quality, trace evidence, query vintages, and reprocess an artifact with a named extractor version.
- [x] 8.5 Extend source validation, release-check, health, lineage, and snapshot-manifest coverage so `index_core` and `sector_core` results and the latest attempt failure are independently visible.

## 9. Cutover, rollback, and retirement

- [x] 9.1 Apply additive migrations and import current report `082826` into an isolated environment with all source and consumer modes non-platform; archive acceptance and quality reports. Historical PDFs may be imported separately for operator-controlled reprocessing.
- [x] 9.2 Promote `index_core`, switch `macro_factset` after dual-read gates pass, and observe one successful scheduled refresh plus one weekly review; before the next Saturday window, an approved operator-triggered equivalent MAY satisfy this observation by running the registered FactSet import pipeline followed directly by Macro and Sector reviews under their effective production modes. Record run identifiers, selected report version, effective modes, results, and a consumer-flag rollback drill. Keep `sector_factset` shadow; its promotion remains 9.3.
- [x] 9.2a Regenerate and review the `2026-09-03` Macro and Sector reports against the analysis-quality checklist: all 25 index observations available, required diagnostics correct, concentration evidence page-cited, Macro covers every required lens, Sector records Macro/FactSet alignment or explicit unavailability, and layer verdicts remain unchanged by top-down context. Archive the acceptance result before 9.3 or 9.5.
- [ ] 9.3 Promote `sector_core` only after the 100% cell gate and 9.2a analysis-quality acceptance, switch `sector_factset` after Sector smoke/regression tests, and observe one successful scheduled refresh plus Sector review.
- [x] 9.4 Verify rollback changes routing only and preserves PDF artifacts, document versions, candidates, evidence links, structured observations, and vintages.
- [ ] 9.5 After the observation window and 9.2a analysis-quality acceptance, delete Macro’s direct FactSet download/parser invocation, remove the runtime dependency on `config/macro.yaml` FactSet folder settings, and retain only the governed import/ingest route.
- [ ] 9.6 Update data-layer architecture, source inventory, operator runbook, copyright/internal-use guidance, metric dictionary, and consumer ownership documentation; run the focused and full test suites before declaring migration complete.
