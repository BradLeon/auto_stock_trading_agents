## Why

The current non-structured document inventory proves only that a file was once
stored.  It does not prove that every active PEAD company has the latest
earnings release, regulatory filing, and call transcript for one resolved
earnings event.  NVIDIA happened to have all of the required event inputs,
while Marvell's current SEC release was found but quarantined because the
event period was never resolved; the missing period also prevented its
regulatory-filing download from running.

The active PEAD portfolio has changed.  This is the appropriate point to make
the active PEAD roster, rather than the larger target-plus-observe universe,
the bounded coverage contract for official earnings documents.

## What Changes

- **BREAKING** Change the PEAD target roster to add `MSFT` and remove `CRDO`,
  `VRT`, and `KLAC`.  Removed symbols no longer receive PEAD monitor, scoring,
  or scheduled official-document work; they remain available to other research
  and sector workflows.
- Introduce an event-bound official-disclosure package for each current PEAD
  target.  The package resolves the newest disclosed earnings event before
  collecting documents.
- Collect and validate, independently, the event's earnings release,
  appropriate SEC/foreign-issuer regulatory filing, and earnings-call
  transcript.  A stored legacy document or a manual file is not a passing
  substitute until it passes the same identity, period, semantic, provenance,
  freshness, and body-quality checks.
- Make the package report operational states per role (`accepted`, `missing`,
  `not_yet_available`, `unreachable`, `quarantined`) instead of silently
  treating a skipped acquisition or a historical file as success.
- Add a repeatable, isolated real-source acceptance run and a human-readable
  per-company report.  It will show entity binding, actual/latest disclosure
  date, fiscal label, source/form/accession, body quality, and each failure
  reason.

## Capabilities

### New Capabilities

- `data/pead-official-disclosure-coverage`: Defines event-bound, role-complete
  official-document acquisition and acceptance for the active PEAD roster.

### Modified Capabilities

- None.

## Impact

- Affected configuration: `config/pead.yaml`, per-symbol PEAD configuration,
  and entity/source metadata where event resolution needs an authoritative
  identity.
- Affected ingestion paths: `ats.data.earnings_events`, SEC official-document
  retrieval, transcript adapters, document admission and asset repositories.
- Affected operational surface: scheduler/CLI document acquisition and
  coverage/quality reporting; removed PEAD targets must no longer be scheduled
  as PEAD work.
- The work uses SEC and already configured transcript providers, runs only
  against the resulting active PEAD roster, and keeps no trading, LLM,
  decision, or order side effects.
