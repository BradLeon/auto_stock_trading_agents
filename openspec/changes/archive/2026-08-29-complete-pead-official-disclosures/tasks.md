## 1. Scope and roster contract

- [x] 1.1 Update the PEAD target configuration to add MSFT and remove CRDO, VRT, and KLAC without changing the observe roster or deleting historical assets.
- [x] 1.2 Make all PEAD official-document scheduling and acceptance entry points derive their scope from the target configuration rather than a separate hard-coded list.
- [x] 1.3 Add roster/config regression tests proving the eleven-symbol scope and the exclusion of CRDO, VRT, and KLAC; run this test group before continuing.

## 2. Latest earnings-event resolution

- [x] 2.1 Implement an event-first resolver that collects authoritative date and fiscal-period evidence, persists the decision/conflicts, and returns an explicit event binding to document collectors.
- [x] 2.2 Add deterministic tests for domestic and foreign fiscal calendars, missing periods, conflicting evidence, and the MRVL Q2 FY2027 `period_unresolved` regression; run this group before continuing.
- [x] 2.3 Ensure an unresolved event produces an explicit package status and cannot silently skip a filing collector or reuse a previous-quarter document.

## 3. Role-complete package collection and admission

- [x] 3.1 Implement per-event, independent release, regulatory-filing, and transcript collection results using the existing domestic and foreign SEC form rules.
- [x] 3.2 Revalidate legacy and manual assets through the package admission contract; remove the manual path's ability to satisfy a role without identity, period, semantic, provenance, and body-quality checks.
- [x] 3.3 Classify and persist `accepted`, `missing`, `not_yet_available`, `unreachable`, and `quarantined` role states with actionable reason metadata.
- [x] 3.4 Add focused tests for wrong issuer, old period, correct EX-99/10-Q, foreign 6-K/20-F/40-F selection, transcript delay, frontend noise, and manual-file rejection; run this group before continuing.

## 4. Operational interface and report

- [x] 4.1 Add one CLI/operational entry point that runs the current PEAD roster in an isolated store/artifact root and records no LLM, scoring, Chief, broker, order, or trading side effects.
- [x] 4.2 Generate JSON and Markdown acceptance outputs with target roster, resolved event, role status, entity/period/freshness checks, form/accession/source provenance, body checks, and per-role failure reasons.
- [x] 4.3 Add CLI/report and side-effect regression tests, including continuation after one issuer fails; run this group before external-source validation.

## 5. Bounded real-source acceptance and remediation

- [x] 5.1 Run the complete directed unit/integration suite and strict OpenSpec validation; fix all deterministic failures before networked acceptance.
- [x] 5.2 In a fresh isolated store, execute the official-document acceptance run only for GOOG, NVDA, SKHY, TSM, ASML, COHR, LRCX, LITE, AVGO, MRVL, and MSFT.
- [x] 5.3 Inspect every accepted asset for canonical entity, event period, latest disclosure date, role/form/accession provenance, and readable content; remediate deterministic ingestion/validation defects and rerun only the affected role/entity.
- [x] 5.4 Publish a dated per-company completeness and accuracy report that distinguishes accepted packages from provider timing, official-source outages, and unresolved or quarantined candidates; record any genuinely external remaining gaps without relabeling them as success.
