# TrendForce DRAM Contract Price Data Validation — 2026-08-29

## Scope

This report validates only the persistent structured dataset
`industry_dram_contract_price`. It does not test any Agent, Workflow, market-price
or option-data consumer.

## Result

`trendforce_dram` completed a real production ingestion run (`9965c39c4b04a9c10987751a`):

| Check | Result |
|---|---|
| New data-layer adapter and catalog registration | Passed |
| Isolated public-page ingestion | 3 accepted, 0 quarantined |
| Production ingestion | 3 accepted, 0 quarantined |
| Raw artifact / field lineage | Passed; one immutable HTML artifact retained |
| Source and dataset release check | Passed; source published to `platform` |
| Consumer gate | Not evaluated and not required for data publication |

The public page reported `2H Jun`, was updated on 2026-08-28, and was ingested on
2026-08-29. The adapter stores that source label in raw lineage and normalizes the
observation window to `2026-06-16` through `2026-06-30`; it does not invent history.

| Item | Average price (USD/item) | Published change |
|---|---:|---:|
| DDR5 8GB SO-DIMM | 115.0 | +2.68% |
| DDR4 16Gb 2Gx8 | 42.0 | +5.00% |
| DDR4 8Gb 1Gx8 | 21.0 | +5.00% |

## Quality interpretation

The dataset's five-dimension report is `passed`: coverage, accuracy/reconciliation,
freshness, completeness and availability all pass. Freshness has two independent
meanings here:

- Page / ingestion freshness: the latest page was known on 2026-08-29.
- Underlying market-session lag: the source's latest disclosed session ended on
  2026-06-30, about 60 days before validation. The configured maximum is 90 days.

This validates the data pipeline and its provenance, not the economics of a
proprietary contract-price assessment. The public table exposes only its latest
session; historical intervals will accumulate only through future scheduled runs.

## Operator checks

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data availability \
  --dataset industry_dram_contract_price

PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data quality \
  --dataset industry_dram_contract_price --format markdown

PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series \
  --dataset industry_dram_contract_price \
  --metric industry.dram_contract_price \
  --entity DRAM_CONTRACT_PRICE

# Inspect one returned observation's immutable source HTML and metadata.
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data lineage 95f4d22a1163d4dfc37e1f73
```
