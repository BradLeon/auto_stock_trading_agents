## Context

See `proposal.md` for motivation and `specs/data/factset-earnings-insight/spec.md` for normative behavior.

The legacy implementation is split between `config/macro.yaml`, `src/ats/data/factset.py`, and Macro assembly/review code. It downloads the current PDF during the consumer path, reads at most the first 16 text pages, and returns one transient `EarningsBackdrop`. That path cannot answer which report was visible at an earlier decision time, cannot expose sector tables, and has no central quality or release state.

The unified data layer already separates immutable unstructured document versions, structured observations/vintages, release modes, and DataProducts. FactSet spans both sides: prose and original charts are authoritative document evidence, while a bounded subset of repeated weekly values is useful as a queryable time series.

The current release-acceptance anchor is the 2026-08-28 report (`082826`): pages 1–16 have useful extractable text and pages 17–36 are largely raster charts. It demonstrates two failure classes the design must surface rather than hide: wording drift (the legacy parser misses “Ten of eleven sectors”) and an apparent internal revenue-growth discrepancy (7.5% versus 7.7%). Earlier local PDFs may be retained for operator-controlled reprocessing, but their extraction quality and historical consumer conclusions are outside this release scope.

FactSet content is licensed research. Raw PDF and chart images are internal evidence assets, not a new redistribution surface.

## Goals / Non-Goals

**Goals:**

- Fetch each weekly report once, preserve its exact bytes, and make every published number reproducible from an evidence anchor.
- Publish an applicability-aware index core first and an 11-sector core only after deterministic table validation.
- Preserve report date, target period, estimate state, and real ingestion time as separate concepts so `as_of` queries do not leak backfilled information.
- Give Macro and Sector one typed, source-independent read interface with explicit freshness and degradation states.
- Make source rollout and consumer cutover reversible without deleting collected data.

**Non-Goals:**

- Building a complete FactSet terminal substitute or extracting every chart and company ranking in V1.
- Treating Topic of the Week company mentions as official company disclosures or PEAD inputs.
- Asking an LLM or vision model to adjudicate conflicting source values.
- Redistributing the original PDF or chart artwork outside explicitly authorized internal research views.
- Generalizing the first implementation into an arbitrary-document OCR platform before the FactSet contract is proven.

## Decisions

### 1. One acquisition creates two governed projections

The acquisition coordinator will fetch the stable URL once and write one content-addressed raw artifact. It will then drive:

1. an unstructured projection registered as source `factset_earnings_insight_doc`; and
2. a structured projection registered as source `factset_earnings_insight_metrics`, dataset `sp500_earnings_insight`.

The two projections share `document_id`, `document_version_id`, raw `artifact_id`, report date, final URL, and PDF SHA-256. Reprocessing the same hash may create new processing-run metadata and candidates, but not a new source document version or observation vintage.

This reuses existing unstructured and structured admission paths while avoiding two downloads. A single hybrid physical table was rejected because it would couple document retention to numeric release and weaken store ownership rules. Keeping the legacy consumer fetch as a fallback was rejected because it would preserve an unaudited second source of truth.

### 2. Raw binary-to-document association is generic, not FactSet-specific

Add a document-artifact association owned by the unstructured store with this logical contract:

| Field | Meaning |
|---|---|
| `document_version_id` | Immutable extracted document version |
| `artifact_id` | Content-addressed binary or derived crop |
| `role` | `source_pdf`, `page_image`, or `chart_crop` |
| `page_number` | One-based source page when applicable |
| `region_json` | Normalized `[x0,y0,x1,y1]` in page coordinates when applicable |
| `media_type` | PDF or image MIME |
| `content_hash` | Integrity check for the linked bytes |

`structured_evidence_links` will support `anchor_kind` (`text_span` or `image_region`), `page_number`, optional character offsets, optional chart identifier, and `region_json`. Existing text evidence remains valid; the new fields are nullable for backward compatibility.

Adding only `raw_pdf_path` to the FactSet row was rejected because paths are environment-specific and cannot represent multiple chart crops or future binary document sources.

### 3. Stable business metric IDs are provider-neutral and dimensions carry FactSet state

The catalog will register the following 31 V1 metric families. Ratios use decimal storage and render as percentages; counts use integers; EPS and valuation values use decimal numbers with explicit units.

| Metric ID | Entity scope | Required dimensions / notes |
|---|---|---|
| `earnings.reporting.coverage` | SP500, GICS | target quarter; reported/total counts when available |
| `earnings.eps.above_estimate_share` | SP500, GICS | target quarter, estimate state |
| `earnings.eps.inline_estimate_share` | SP500, GICS | target quarter, estimate state |
| `earnings.eps.below_estimate_share` | SP500, GICS | target quarter, estimate state |
| `earnings.revenue.above_estimate_share` | SP500, GICS | target quarter, estimate state |
| `earnings.revenue.inline_estimate_share` | SP500, GICS | target quarter, estimate state |
| `earnings.revenue.below_estimate_share` | SP500, GICS | target quarter, estimate state |
| `earnings.eps.surprise_pct` | SP500, GICS | target quarter, estimate state |
| `earnings.revenue.surprise_pct` | SP500, GICS | target quarter, estimate state |
| `earnings.eps.yoy_growth` | SP500, GICS | target quarter or year, estimate state |
| `earnings.revenue.yoy_growth` | SP500, GICS | target quarter or year, estimate state |
| `earnings.net_profit_margin` | SP500, GICS | target quarter, estimate state |
| `earnings.margin.increase_share` | SP500, GICS | target quarter versus comparison period |
| `earnings.margin.unchanged_share` | SP500, GICS | target quarter versus comparison period |
| `earnings.margin.decrease_share` | SP500, GICS | target quarter versus comparison period |
| `earnings.guidance.positive_count` | SP500, GICS | guidance target quarter |
| `earnings.guidance.negative_count` | SP500, GICS | guidance target quarter |
| `earnings.revision.improved_sector_count` | SP500 | target quarter, estimate state, comparison date, revision direction, and sector total |
| `earnings.bottom_up_eps` | SP500 | target quarter/year and snapshot date |
| `valuation.forward_pe` | SP500, GICS | forward horizon and snapshot date |
| `valuation.trailing_pe` | SP500 | trailing horizon and snapshot date |
| `valuation.forward_pe.average_5y` | SP500 | comparison reference |
| `valuation.forward_pe.average_10y` | SP500 | comparison reference |
| `valuation.trailing_pe.average_5y` | SP500 | comparison reference |
| `valuation.trailing_pe.average_10y` | SP500 | comparison reference |
| `revenue.geographic.us_share` | SP500, GICS | report snapshot |
| `revenue.geographic.international_share` | SP500, GICS | report snapshot |
| `consensus.rating.buy_share` | SP500, GICS | report snapshot |
| `consensus.rating.hold_share` | SP500, GICS | report snapshot |
| `consensus.rating.sell_share` | SP500, GICS | report snapshot |
| `consensus.target.upside` | SP500, GICS | report snapshot |

Every observation key includes entity, metric, target period or snapshot date, estimate state (`estimated`, `blended`, `actual`, or `not_applicable`), unit, source, and `known_at`. `report_date` is source metadata, never substituted for `known_at`. The revision-breadth observation additionally carries `comparison_date`, `revision_direction`, and `sector_total`; these dimensions distinguish otherwise similar comparisons across weekly reports. When the source supplies counts and shares, raw counts are stored and shares may be derived centrally; absence is null with a reason, never zero.

The canonical entities are `SP500` and `GICS_10`, `GICS_15`, `GICS_20`, `GICS_25`, `GICS_30`, `GICS_35`, `GICS_40`, `GICS_45`, `GICS_50`, `GICS_55`, `GICS_60`. Provider labels are aliases resolved before admission.

Provider-prefixed metric IDs were rejected because the concepts should remain comparable if a second licensed source is added. A wide weekly table was rejected because periods and estimate states differ within one report and would produce ambiguous null columns.

### 4. Extraction is template-aware, candidate-first, and deterministic at release

The parser is divided into four stages:

1. **Document classification:** validate `%PDF`, title, report date, expected page count bounds, and section/table-of-contents anchors.
2. **Text candidates:** parse named sections and sentences with small section-specific extractors. Each candidate contains normalized entity, metric, period, state, unit, raw token, parsed value, page, character span, extractor version, and run ID.
3. **Chart candidates:** locate charts by normalized title/axis/legend anchors rather than fixed page alone. Render or extract the image, apply layout-specific crops and a deterministic local table decoder over OCR/raster evidence, and emit populated `ChartTable` cells with image-region evidence. A vision model may suggest a candidate during development, but its output cannot satisfy release gates.
4. **Admission:** apply schema, range, composition, completeness, duplication, and evidence checks; publish only the passing subset.

Text validation includes percentage bounds, non-negative counts, period/state extraction, and duplicate consistency. `earnings.revision.improved_sector_count` must be an integer from zero through its explicit `sector_total` (11 in the current S&P 500 sector taxonomy), and word-number forms such as “Ten of eleven sectors” must retain both raw tokens. Composition groups such as above/inline/below, margin increase/unchanged/decrease, geography, and rating distributions must sum to `1.0 ± 0.01` after source rounding. Sector tables must contain exactly the expected 11 GICS rows with no duplicate or unknown labels. Scorecard count triplets must reconcile to a reported total where one is printed.

The first chart adapter will use a fixed-layout raster pipeline backed by a locally available deterministic OCR engine and an `082826` layout decoder that converts recognized labels/tokens into `ChartTable` rows. If the OCR dependency is absent, the run records `extractor_unavailable`; document and index-text processing continue, while sector candidates remain shadow. Adding cloud OCR as an implicit fallback was rejected because it changes data residency, cost, and reproducibility. Parsing charts by page number alone was rejected because page positions shift during earnings season.

When the same observation identity has distinct source values, all candidates are retained with `conflict`; none is selected automatically. This deliberately handles discrepancies such as 7.5% versus 7.7% without inventing an authority rule.

### 5. Quality is evaluated by applicability groups, then released independently

The report phase determines which metric groups are applicable:

- `pre_reporting`: estimates, guidance, valuation, ratings, target, geography;
- `in_progress`: pre-reporting groups plus coverage, scorecard, surprise, blended growth and margin;
- `substantially_complete`: actual/blended outcome groups plus next-period estimates and guidance.

Missing non-applicable groups do not fail a report. For each applicable group, a release manifest records expected cells, observed cells, admitted cells, conflicts, quarantined cells, and evidence coverage.

There are two independently releasable partitions:

- `index_core`: all applicable SP500 text fields must have valid period/state/unit and evidence; any conflict quarantines only the affected metric, and required headline fields must be complete before the partition releases.
- `sector_core`: every applicable chart table must contain all 11 expected sectors and all expected columns. Expected columns are report- and template-applicability-aware: a registered field not disclosed in the current chart, such as `revenue_surprise` in `082826`, is explicit `not_applicable`, never zero-filled, and does not fail the release. During initial cutover it also requires 100% per-cell agreement with the manually annotated acceptance set. The acceptance runner performs the comparison itself; a non-empty annotation file is not evidence of agreement.

For `082826`, the system first writes a reviewable candidate bundle grouped by the eight required chart IDs. Each row carries chart/page, canonical GICS entity, column, normalized value/unit, raw token, period/state, and image region. The operator reviews one chart at a time and records `accepted`, a corrected value, or `unreadable`; coordinates are system-owned. Accepted/corrected rows become the checked-in golden manifest. The `SP500` aggregate row is an index cross-check and is excluded from Sector golden cells.

The `082826` sector growth chart's `Today` column is the publishable value and carries `comparison_date=2026-08-28`, the report publication date. Its `30-Jun` column remains image evidence for the comparison and does not create a second Sector observation.

Sector acceptance is executed as a contiguous A–G chain: validate the golden schema; persist the eight reviewed tables and review provenance; decode original PDF chart rasters without reading the golden manifest; emit populated `ChartTable` cells with exact image regions; compare golden and candidate cells; and archive a real-PDF acceptance report. A checkpoint may report its own evidence, but neither `5.1` nor `5.5` is complete until its entire stated chain has passed. This prevents a review matrix, a decoder scaffold, or a self-comparison from being treated as release evidence.

The release state is one of `registered_no_data`, `shadow`, `platform`, `stale`, or `unavailable`, matching existing rollout semantics. Freshness defaults to 10 calendar days from report date; this accommodates holiday weeks while making a missing next issue visible. A source success does not imply both partitions released.

A single all-or-nothing report gate was rejected because unstable sector OCR would block higher-quality index text. Per-cell best-effort publication of an incomplete sector table was rejected because consumers would mistake missing rows for comparative weakness.

### 6. DataProducts exposes one typed weekly snapshot

Add `src/ats/data/products/earnings_insight.py` with typed records conceptually shaped as:

```text
EarningsInsightSnapshot
  report: report_date, document/version/artifact references, official_url
  index: observations grouped by period and metric
  sectors: map[GICS entity, observations grouped by period and metric]
  status: index_release, sector_release, freshness, warnings
  lineage: selected observation IDs, known_at values, extractor/release versions
```

`DataProducts.earnings_insight_snapshot(as_of=None)` performs released structured queries and document joins behind store interfaces. It accepts an explicit decision time and does not initiate refresh. It returns a valid unavailable object rather than `None` or an exception when no release exists. A compatibility mapper creates the legacy `EarningsBackdrop` during Macro cutover; it is a DTO adapter, not a second parser.

Macro receives only the index partition and uses it for earnings breadth, growth, margin, guidance, and valuation context. Sector receives the GICS matrix as a top-down overlay and preserves the distinction from the project’s AI-hardware layers. Chief, Risk, and PEAD do not call this product directly; they inherit already-persisted Macro/Sector findings. Technical and company-level evidence workflows remain out of scope.

Returning a generic dictionary was rejected because silent field and unit drift is the main risk in this source. Letting agents query observations individually was rejected because it would duplicate selection rules and permit mixed-report snapshots.

### 7. Refresh is a separate Saturday job before the existing weekly review

Add `schedule.factset_refresh_at` with default `08:10` and `schedule.factset_refresh_tz` defaulting to the weekly review timezone. `src/ats/runtime/scheduler.py` registers `factset_weekly_ingest` on Saturday before the existing `weekly_review_at` job (`08:50` in current configuration). The job runs the acquisition, document projection, candidate extraction, validation, and eligible releases as one observable pipeline run.

The weekly review never waits on or invokes network fetch. It reads the latest released snapshot as of its run start. If refresh failed, DataProducts returns the last release with `stale` and the current attempt failure; if no release exists, it returns `unavailable`. Scheduler startup validates that the refresh time precedes the review time in the same timezone. Misfires run once within a bounded grace period and do not manufacture multiple vintages.

For a cutover that is approved before the next Saturday window, an operator MAY produce the same acceptance evidence without changing the scheduler: run the registered FactSet import pipeline against the licensed current PDF, then run Macro and Sector reviews directly in that order. The import and the two reviews MUST use their normal production routing and persist their run identifiers, timestamps, selected report version, effective modes, and results. This is an observation substitute only; it neither bypasses quality/release gates nor changes the scheduled production order. `sector_factset` remains shadow until its independent promotion task is complete.

Hiding refresh inside Macro assembly was rejected because Sector and future consumers need the same version and because retries would otherwise be coupled to agent execution. An external-only scheduling assumption was rejected because this repository already owns the weekly review order and needs an enforceable dependency boundary.

### 8. Rollout uses separate source and consumer controls

Register source modes for `factset_earnings_insight_doc`, `factset_earnings_insight_index`, and `factset_earnings_insight_sector`, plus consumer modes `macro_factset` and `sector_factset`. Source mode controls acquisition/admission/release; consumer mode controls read routing. This permits index platform release while sector remains shadow, and permits consumer rollback without data deletion.

The release-acceptance corpus is a small checked-in manifest for `082826`, containing PDF SHA-256, expected report metadata, selected text spans, and expected table cells. Licensed PDF bytes remain in the configured internal artifact location and are not committed. Historical manifests may be added later for regression coverage, but are not a release gate. Tests that require the corpus skip with an explicit reason if the licensed artifact is unavailable; fixture-level synthetic PDFs cover CI mechanics.

### 9. Observability is report- and partition-oriented

Each run emits: stable/final URL, response status, artifact hash, report date, extractor version, page/text/chart counts, candidate/admitted/conflict/quarantine counts by metric group, index/sector release result, and elapsed time. Never log PDF text or signed/authenticated URL parameters.

Operational CLI/reporting will support:

- inspect current source status and most recent failure;
- list report versions and hashes;
- show quality by partition and metric group;
- trace an observation to text span or chart region;
- query latest, `as_of`, and all vintages;
- reprocess an artifact with a new extractor version without redownloading it.

### 10. Analysis consumers receive a bounded evidence packet, not the compatibility DTO or full prose

`EarningsInsightSnapshot` remains the report-consistent source of truth. A typed analysis packet groups every released index observation by decision topic, computes formula-backed diagnostics with input observation IDs, and selects at most six narrative excerpts from `data_document_pages`. Narrative selection uses registered section/title anchors and bounded patterns for concentration, accounting basis, sector drivers, margin, valuation, and ratings. Each excerpt retains version, page, and character offsets. The legacy `EarningsBackdrop` remains a compatibility adapter only.

Sending the first sixteen pages wholesale was rejected because it increases token cost, licensed-text exposure, noise, and prompt-injection surface. Keeping the compatibility DTO as the main prompt was rejected because it discards most accepted observations and all explanatory evidence.

### 11. Macro has a first-class earnings-cycle assessment

Macro receives the full packet regardless of network mode. Its persisted output and report add a structured FactSet assessment covering growth quality, breadth/concentration, surprise quality, margins/guidance, valuation/sentiment, conflicts/limitations, market implications, and cited observation/page references. Deterministic tables render all applicable observations and diagnostics; the model explains them but cannot alter their values.

`--offline` means “no live network acquisition,” not “discard local governed products.” A live production review still combines the packet with rates, inflation, employment, credit and market pricing.

### 12. Macro and FactSet enter Sector only after all layer verdicts are fixed

The layered Sector path loads the latest formal Macro review and the FactSet sector overlay once, after producing all eight layer verdicts. The rotation prompt may use these inputs to explain alignment, divergence, and the final cross-layer add/trim recommendation, but it cannot mutate layer allocations, confidence, or name calls. The persisted Sector review and report expose what top-down context was available, what was used, and why unavailable/shadow/stale context was omitted.

Passing top-down context into each layer was rejected because GICS sectors are not AI-hardware layers and Macro already affects portfolio risk at Chief. Excluding top-down context from the final synthesis was also rejected because it hides economically relevant contradictions from the reader.

## Risks / Trade-offs

- **[FactSet changes PDF layout or protection]** → Classify templates by anchors and extractor version; quarantine unknown templates while retaining the raw document and alerting operators.
- **[Raster OCR produces plausible wrong numbers]** → Require exact entity/column completeness, composition checks, per-cell evidence, and 100% annotated-corpus accuracy before sector release; never use model confidence as admission.
- **[The source contradicts itself]** → Preserve both candidates, mark conflict, and exclude the affected observation until a deterministic policy or corrected report is available.
- **[Historical backfill appears contemporaneous]** → Keep report date separate and set `known_at` to the real first ingestion time; only an independently preserved retrieval record may establish an earlier known time.
- **[A holiday or publication delay marks valid data stale]** → Use a 10-day threshold and expose age/report date so consumers can degrade explicitly rather than hard fail.
- **[Licensed charts leak into outputs]** → Keep raw/crops behind internal artifact access, prefer regenerated charts from released observations, and include source/license metadata in internal views.
- **[A new OCR system increases native dependencies]** → Make it an optional chart adapter; index text release works without it, and startup/source acceptance reports dependency availability.
- **[More structured fields create false precision]** → Limit V1 to repeated, decision-useful metric families; preserve raw wording and state/period semantics, and leave long-tail company tables as document evidence.
- **[Narrative excerpts overfit one weekly template]** → Select by registered topic plus section anchors, cap excerpts, preserve page references, and surface an explicit missing topic instead of widening to the full document.
- **[Macro is counted twice]** → Allow Macro only in the final Sector synthesis, prohibit it from changing layer verdicts, and make its effect on the cross-layer recommendation explicit in the report.

## Migration Plan

1. Add catalog/schema support for document-artifact links, image evidence anchors, FactSet sources, entities, metrics, dataset, quality profiles, schedules, and independent release controls. Run migrations without enabling collection.
2. Implement acquisition and document projection. Ingest the current `082826` PDF into an isolated database/artifact namespace; verify hash, report date, idempotency, complete text, and chart inventory.
3. Implement index-text candidates and admission. Produce the `082826` annotated source-acceptance report, resolve all applicable required-field gaps, and release only `index_core` in shadow.
4. Add DataProducts and the `EarningsBackdrop` compatibility mapper. Run a Macro dual-read comparison for the current report; compare values, period/state labels, freshness, and rendered review text.
5. Promote the index source to platform, then switch `macro_factset` to platform. Observe one scheduled run and one weekly review, or execute the approved operator-triggered equivalent (FactSet import followed by Macro and Sector review) before the next Saturday window. Roll back the consumer flag if the Macro regression gate fails; keep source collection enabled and record the drill.
6. Implement chart adapters and annotate all applicable sector cells in the `082826` report. Require 100% accepted-cell agreement, exact 11-sector completeness, and zero unresolved label mapping before promoting `sector_core`.
7. Add the bounded analysis packet and first-class Macro earnings-cycle assessment; rerun `082826` with all 25 observations and cited narrative evidence before accepting Macro analysis quality.
8. Wire Macro memory and the Sector top-down overlay only into the final layered Sector synthesis, run shadow smoke/regression and `082826` report review, then promote `sector_factset` independently after one successful scheduled refresh.
9. After both consumers complete the quality and observation windows, remove direct download/PDF parsing from Macro and retire the external Obsidian folder as a runtime dependency. Preserve a documented artifact import command for operator-controlled backfill.

Rollback changes only consumer/source release modes: set the affected consumer to legacy/off and leave artifacts, documents, candidates, observations, and vintages intact. Schema rollback is not required for incident response; additive tables/columns remain dormant. The legacy parser is removed only after the observation window, so an earlier rollback remains possible until that final step.
