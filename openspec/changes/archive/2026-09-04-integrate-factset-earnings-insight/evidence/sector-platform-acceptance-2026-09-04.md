# FactSet Sector platform acceptance — 2026-09-04

Acceptance date: 2026-09-04 (Asia/Taipei)

Scope: OpenSpec task 9.3. This is the independent sector-partition release and consumer observation following the completed 9.2a analysis-quality acceptance.

Licensed FactSet PDF bytes and chart images remain in the internal artifact store. This record contains only release metadata, counts, and report paths.

## Cell-quality gate

The operator-provided `EarningsInsight_082826.pdf` was independently decoded from its original embedded rasters after local OCR dependency discovery was repaired for non-interactive macOS processes. The decoder did not read the golden manifest.

| Check | Result |
|---|---|
| Report date / PDF hash / text hash / page count | pass |
| Required chart tables | 8 / 8 |
| Canonical GICS entities | 11 / 11 per applicable table |
| Reviewed golden cells | 231 |
| Independently decoded candidate cells | 231 |
| Missing / extra / duplicate / mismatched / unresolved cells | 0 / 0 / 0 / 0 / 0 |
| Golden review provenance | 8 accepted chart decisions, 231 confirmed cells |

The explicit local OCR search order is `ATS_TESSERACT_PATH`, `PATH`, `/opt/homebrew/bin/tesseract`, then `/usr/local/bin/tesseract`. This preserves deterministic local-only decoding for scheduled macOS processes whose PATH omits Homebrew.

## Production source release

After the cell gate passed, `factset_earnings_insight_sector` was set to `platform` and the normal `FactSetWeeklyPipeline` was run against the same local PDF with the independently established acceptance result.

| Field | Value |
|---|---|
| Final sector pipeline run ID | `e3235c89d2e60baea781346e` |
| Paired index no-change run ID | `31fb36cbefa34e0b0c2f083d` |
| Sector release ID | `ed1e8fae378f3f24892c566d` |
| Selected report version | `SP500:2026-08-28:research_article@111e4bf657fee2b9` |
| Report date | `2026-08-28` |
| Sector release state | `platform` |
| Quality result | pass; 231 observed and 231 admitted cells; zero quarantined cells |
| Decoder result | succeeded; 8 tables; no reason codes |

This operator-triggered import is the permitted equivalent observation before the next scheduled Saturday window. It used the normal pipeline and did not invoke network acquisition.

During the first release attempt, a same-hash reprocessing path incorrectly used the retry timestamp as `known_at` and created 256 duplicate observations. The path was fixed to inherit the existing document version's first `fetched_at`; the final runs above then retained the original `2026-09-03T09:15:02.795578+00:00` decision-time boundary. The 256 accidental duplicate observations and their 257 evidence links were removed after confirming that no current release manifest referenced them. PDF artifacts, document versions, candidates, original observations, and releases were retained.

## Consumer smoke and real Sector review

- `sector_factset` was switched to `platform` only after the source release and targeted regression tests passed.
- Targeted FactSet/Sector regression command passed: `115 passed in 10.75s`.
- AI hardware Sector review completed successfully at `2026-09-04T00:43:51.900682+00:00` and produced `行业分析-AI硬件-2026-09-04.md` plus eight layer reports.
- The persisted review has all eight layer verdicts, uses Macro review date `2026-09-03`, report date `2026-08-28`, and the selected FactSet version above.
- Its final comparison contains three agreements and four divergences. It uses formal FactSet GICS background, records that GICS does not map to L1–L8, and keeps the cross-layer recommendation unchanged: add L6 storage and trim L5 chip design.
- The report explicitly states that Macro and FactSet do not modify any layer verdict, company call, confidence, or evidence chain.

Non-blocking runtime controls behaved as designed: two L7 proposed names outside the configured universe (`005930.KS` and `INTC`) were dropped before report persistence.

## Decision

Task 9.3 passes. Both `sector_core` and `sector_factset` are in `platform` mode. The cutover remains reversible through the existing consumer/source release controls; it does not delete artifacts, document versions, candidates, observations, or vintages.
