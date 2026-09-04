# FactSet Earnings Insight isolated corpus acceptance

Run date: 2026-09-03

Environment: temporary SQLite structured/document repositories and temporary artifact root. Checked-in source and consumer modes remained `shadow`/non-platform. Licensed PDF bytes were read from the operator-provided local corpus and were not copied into the repository.

## Document acceptance

| Report | Report date | Pages | Chart images | Re-import |
|---|---:|---:|---:|---|
| 070226 | 2026-07-02 | 36 | 50 | no_change |
| 071026 | 2026-07-10 | 41 | 52 | no_change |
| 072426 | 2026-07-24 | 38 | 50 | no_change |
| 073126 | 2026-07-31 | 41 | 53 | no_change |
| 080726 | 2026-08-07 | 37 | 45 | no_change |
| 082826 | 2026-08-28 | 37 | 45 | no_change |

Result: 6/6 report dates, immutable PDF/text hashes, page inventories, chart-image inventories, idempotent imports, and template classifications passed.

## Structured acceptance

| Report | Accepted index candidates | Declared missing | Revision-breadth applicability | Sector cell gate |
|---|---:|---:|---|---|
| 070226 | 15 | 16 | not applicable/pass | pending manual annotation |
| 071026 | 7 | 23 | not applicable/pass | pending manual annotation |
| 072426 | 16 | 15 | not applicable/pass | pending manual annotation |
| 073126 | 9 | 22 | not applicable/pass | pending manual annotation |
| 080726 | 22 | 9 | not applicable/pass | pending manual annotation |
| 082826 | 25 | 6 | 10/11 vs 2026-06-30/pass | pending manual annotation |

The document partition is accepted. `index_core` remains shadow because the full per-phase applicable-field acceptance set is not yet 100%. `sector_core` remains shadow because the six-report manual per-cell corpus is incomplete and local deterministic OCR is unavailable. No incomplete partition was promoted.
