# FactSet analysis-quality acceptance — 2026-09-03

Acceptance date: 2026-09-03 (Asia/Taipei)

Scope: OpenSpec task 9.2a. This record evaluates the operator-triggered Macro and AI-hardware Sector reviews against the analysis-quality checklist. It does not promote `sector_factset`, delete the legacy reader, or declare the whole migration complete.

Licensed FactSet report prose is not copied into this repository. Page references below point to the governed local document version.

## Runs and selected input

| Review | Persisted run identifier (`as_of`, UTC) | Result | Output |
|---|---|---|---|
| Macro `macro` | `2026-09-03T14:00:44.649949+00:00` | success | `宏观分析-宏观-2026-09-03.md` |
| Sector `ai_hardware` | `2026-09-03T14:25:48.172577+00:00` | success | `行业分析-AI硬件-2026-09-03.md` plus eight layer reports |

Both reviews selected report date `2026-08-28` and version `SP500:2026-08-28:research_article@111e4bf657fee2b9`.

## Macro acceptance

- PASS — the persisted analysis material contains all 25 applicable released index observations in eight named groups.
- PASS — eight deterministic diagnostics were persisted and rendered:
  - EPS growth minus revenue growth: `36.5` percentage points.
  - EPS surprise minus revenue surprise: `23.3` percentage points.
  - positive/negative guidance ratio: `1.8`.
  - positive minus negative guidance count: `28`.
  - forward P/E versus five-year average: `-1.5075%`.
  - forward P/E versus ten-year average: `+3.1579%`.
  - trailing P/E versus five-year average: `+8.1967%`.
  - trailing P/E versus ten-year average: `+12.3404%`.
- PASS — the eight required interpretations were all present and each retained accepted metric IDs plus governed narrative pages:

| Required lens | Accepted metric IDs | Accepted pages |
|---|---:|---|
| growth quality | 2 | 13 |
| concentration | 2 | 3, 6, 12 |
| surprise drivers | 3 | 3, 6, 13 |
| guidance and margin consistency | 4 | 13 |
| valuation | 6 | 15 |
| analyst expectations | 4 | 15 |
| conflicts and limitations | 3 | 3, 6, 12, 13 |
| market and sector implications | 4 | 3, 6, 12, 15 |

- PASS — concentration was not inferred from headline growth alone: the report explicitly assessed the contribution of Alphabet and Amazon and cited governed pages.
- PASS — the report visibly separates program-read facts and diagnostics from model interpretation.
- PASS — the Macro conclusion also used available local rates, inflation, employment, credit, market-price, oil/VIX, regional, and prior-review context; offline semantics no longer suppress locally stored inputs.
- PASS — unsupported metric IDs and page references are filtered before persistence. Exact configured Chinese theme labels are now normalized to their configured keys; unknown labels remain rejected.

## Sector acceptance

- PASS — all eight company-evidence layer verdicts were completed before the final Macro/FactSet comparison was loaded and run.
- PASS — the final comparison read the latest formal Macro review dated `2026-09-03`.
- PASS — the persisted and rendered comparison contains three agreements, three divergences, and an explicit recommendation-impact conclusion.
- PASS — `sector_factset` remained shadow. The report states that the eleven-sector data was not a formal input, and records report date and version rather than silently treating it as unavailable or production-ready.
- PASS — the recommendation-impact section explains that Macro context reinforces selectivity but does not replace layer evidence; shadow FactSet sector data has no recommendation effect.
- PASS — runtime fingerprints and focused regression tests verify that final top-down comparison cannot change layer verdicts, company calls, confidence, or evidence chains. The report states this boundary explicitly.
- PASS — standard GICS sectors are treated only as comparison context and never as AI-hardware supply-chain layers.

Non-blocking observations from the real run: one NVDA and one ASML evidence cluster could not be adjudicated, and proposed non-universe L7 names were dropped. These pre-existing layer-evidence controls behaved as designed and did not affect the FactSet/Macro integration acceptance.

## Automated verification

Command:

```text
uv run --python 3.12 --isolated --extra data --extra dev pytest tests/test_factset_earnings_insight.py tests/test_macro_strategy.py tests/test_macro_regime.py tests/test_layer_review.py tests/test_layer_report.py tests/test_sector.py -q
```

Final result after report-rendering cleanup: `190 passed in 14.90s`.

## Decision

Task 9.2a passes. The 2026-09-03 reports demonstrate the required analysis-quality behavior and auditable evidence boundaries.

The following remain deliberately out of scope and incomplete:

- 9.3: promote `sector_core`/`sector_factset` only after its separate cell-quality and rollout gates pass.
- 9.5: delete the old direct Macro reader only after its observation-window condition is met.
- 9.6: finish architecture/operator documentation and run the full repository test suite before migration completion.
