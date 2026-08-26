---
name: structured-data-consumer
description: Discover, query, compare, derive, and trace governed structured research data in this repository. Use when an Agent needs company financials, Consensus, regional/industry series, private-company events, historical as-of data, or must decide whether a requested market input is persistent or runtime.
---

# Structured Data Consumer

Use the repository's dynamic data products instead of guessing Provider coverage or reading physical tables directly.

## Start with discovery

Run commands from the repository root. If `ats` is not installed in the active environment, replace `ats` with `PYTHONPATH=src .venv/bin/python -m ats.runtime.cli`.

1. Run `ats data catalog --format markdown` to see registered versus actually queryable datasets.
2. Run `ats data describe <dataset-or-metric-or-entity>` for semantics and current coverage.
3. Run `ats data availability --dataset <dataset> --entity <entity>` before relying on a particular entity.
4. Run `ats data examples --dataset <dataset>` and adapt the generated command. Examples are selected from current accepted observations; do not substitute a static metric list from this Skill.

If discovery reports `registered_no_data`, `no_coverage`, stale, conflict, or another gap, preserve that status. Do not turn missing values into zero or silently switch to a semantically different metric.

## Choose the consumption surface

- Use `DataProducts` in Agent or Workflow code. It is the stable contract for series, cross-sections, quality, source selection, derivations, and snapshot manifests.
- Use `ats data ...` for interactive discovery, reproducible checks, and operator/user handoff.
- Use the governed read-only SQL view or Pandas output for exploratory analysis after confirming dataset and metric semantics.
- Use IBKR/yfinance/ThetaData runtime adapters for current prices, OHLCV, option chains, Greeks, or IV. These inputs are deliberately absent from the structured repository and structured snapshot replay.

Deterministic Workflows must call DataProducts or compatibility APIs directly and use feature flags. They must not depend on this Skill or Prompt text for correctness.

## Query safely

- Latest accepted series: `ats data series --dataset <dataset> --metric <metric> --entity <entity>`.
- Historical visibility: add `--as-of <ISO-8601>`; this is not the same as filtering the metric period.
- Revision history: add `--vintages`.
- Derivation: `ats data derive --dataset <dataset> --metric <metric> --entity <entity> --operation yoy|mom|rolling` and add `--window N` for rolling.
- Cross-section: `ats data cross-section --dataset <dataset> --metric <metric> --entities A,B,C --period <period>`.
- Lineage: run `ats data lineage <observation-id>` from the query result or generated examples.

Prefer strict quality behavior. Report the selected source, `known_at`, period, unit/currency, quality warnings, fallback/conflict state, and `as_of` alongside conclusions. For important analyses, request a structured snapshot manifest through DataProducts so persistent inputs can be replayed.

## Mutation boundary

This is a consumption Skill. Do not ingest, publish, roll back, alter mappings, or change source configuration unless the user explicitly asks for an operator/developer action. For those tasks, follow `docs/STRUCTURED_DATA_OPERATIONS.md` and the active OpenSpec change.
