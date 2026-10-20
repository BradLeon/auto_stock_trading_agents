# Anthropic Economic Index Task 7 Acceptance

Date: 2026-09-05

## Accepted release scope

- Repository commit pinned by discovery: `2ea58ff75e4247d26810c37f10c179edc2466cac`.
- Release: `release_2026_06_26` only.
- Calendar-month observations: `2026-04` and `2026-05` only.
- Earlier 2026 weekly releases were not ingested.
- Validation database and artifacts were isolated under `/private/tmp` and were not published to the platform store.

The two manually downloaded monthly inputs matched the official Hugging Face LFS SHA-256 values:

| Input | Bytes | SHA-256 |
|---|---:|---|
| Claude.ai | 219,174,671 | `f974b358bce0e5a8417510c61da4342234cd0de9d9d0b62acf4c6dbcf8ec7b68` |
| 1P API | 77,282,477 | `62197f003e001945ad130c2f26f5e07f3fda45ff41644df91444b04fd524a19f` |

Local-file overrides were transport inputs only. Persisted lineage retained the official commit-pinned URLs and full upstream hashes.

## Ingestion result

Run `325e5a4a446c79cd37c900aa` completed in 168.53 seconds:

- status: `succeeded`
- accepted observations: 153,218
- quarantined observations/relations: 0
- created taxonomy relations: 22,077
- unchanged duplicate relations: 6
- relation candidates: 0

Monthly observations reconciled as follows:

| Period | Product | Observations |
|---|---|---:|
| 2026-04 | Claude.ai | 34,303 |
| 2026-04 | 1P API | 29,258 |
| 2026-05 | Claude.ai | 37,911 |
| 2026-05 | 1P API | 32,998 |

There were 134,470 `calendar_month` observations and 18,748 `research_snapshot` observations. No non-Global monthly observation was admitted.

Claude.ai and 1P API observations resolved to different artifacts (`08d0908782ae4952095ef3b4` and `41ed832531efc5fb7b19507b`, respectively); neither product borrowed or filled the other product's series.

## Taxonomy and snapshot findings

The official SOC structure is a wide hierarchy. The parser admits every non-empty stable code on a row. When task statements declare a base `.00` O*NET-SOC code that the structure only expands as `.01/.02/.03`, the stable code from the task file creates the parent occupation entity; names are never used as join keys.

The newest monthly files contain task IDs not present in the latest published task taxonomy (`release_2025_09_15`). Measured mapping coverage was 78.85% for Claude.ai and 78.45% for 1P API. This is an explained upstream-version mismatch: affected task observations are retained with warnings rather than silently dropped or mapped by name. The 99% threshold remains the expected healthy target, not a reason to discard a valid newer monthly slice when the official taxonomy lags.

`task_penetration.csv` publishes task text but no Task ID. These rows therefore use deterministic source-native content-hash entities with `stable_onet_mapping=unavailable`; they are not presented as O*NET Task IDs. Both penetration and observed exposure retain `ratio_0_1` and `period_basis=research_snapshot` and are excluded from monthly change calculations.

## Consumer checks

- An omitted period in `ai_work_adoption_snapshot(source_product="claude_ai")` selected `2026-05`: 718 jobs and 37,911 observations.
- Explicit `period="2026-04"` remained queryable: 696 jobs and 34,303 observations.
- Each full Claude.ai snapshot completed in approximately 25 seconds after removing repeated full-table and per-job scans.
- Fixture integration tests cover repeated identical ingestion (`no_change`), same-period revision vintages, historical `as_of`, and product independence.

The April-to-May delta is accepted only as a calculation test. No trend conclusion should be published until another comparable release provides additional same-methodology periods.
