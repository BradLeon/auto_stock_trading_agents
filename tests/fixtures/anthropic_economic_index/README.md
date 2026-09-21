# Anthropic Economic Index 2026-06-26 fixtures

These are deterministic **minimal test slices**, not redistributed copies of the
full upstream files. They preserve the official schema and the April/May period
set, while values are deliberately reduced/constructed to exercise filtering,
lineage, revision, and taxonomy behavior.

- Repository: `Anthropic/EconomicIndex`
- Repository metadata commit used by discovery fixture: `caa39af`
- Full release commit: `caa39af46a284950ad03e6a4390e92ffb1337269`
- Official release periods: `2026-04`, `2026-05`
- Allowed fixture rows: Global SOC level 0/1 and O*NET level 0, plus one
  non-Global and one unsupported-category row to prove exclusion.

Official full-file identities obtained from the commit-pinned Hugging Face tree
metadata:

| File | Bytes | LFS SHA-256 |
|---|---:|---|
| `release_2026_06_26/data/aei_claude_ai_2026-06-26.csv` | 219174671 | `f974b358bce0e5a8417510c61da4342234cd0de9d9d0b62acf4c6dbcf8ec7b68` |
| `release_2026_06_26/data/aei_1p_api_2026-06-26.csv` | 77282477 | `62197f003e001945ad130c2f26f5e07f3fda45ff41644df91444b04fd524a19f` |

Local fixture SHA-256 values:

| File | SHA-256 |
|---|---|
| `aei_claude_ai_2026_06_26.csv` | `fc06a48f033790b157f8a46823460d7ca4f76921b04b85637ddaeb6eef61b332` |
| `aei_1p_api_2026_06_26.csv` | `c4120135d0e4ac1e47ef243d83a4807e1e753a6ce64c276cc18bd3e47e101690` |
| `metadata.json` | `360cf0f1864443e9daac0e851baef20f9639a13cb00714a29d9d21d6505d97e7` |
| `onet_task_statements.csv` | `edda65e09614b56a457f60b54cccf92e14d234aa20d1934970be2070e4efb14d` |
| `soc_structure.csv` | `55ed19bce891dab55e7dd3f840edcf041cb983ac90f92395d9fd128cd93ba016` |
| `job_exposure.csv` | `aa747a67d18a39496348e575f31a642259013bb3db3c813066a53898e0847e61` |
| `task_penetration.csv` | `d51bd2a14e4214aeb042a81dd5feb75af0b9b866fd54d45a8704adb9d9a8647b` |

Revision tests modify the minimal Claude.ai fixture in memory. Such revisions are
synthetic and are never presented as Anthropic-published values.
