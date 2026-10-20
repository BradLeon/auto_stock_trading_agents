# Phase E Scheduler Owner Inventory

This inventory records the scheduler behavior observed before Dispatcher migration. It is a migration checklist, not a claim that disabled or legacy jobs are already owned by the target runtime. Configuration decides which optional jobs are active in a given deployment.

## Current scheduled entry points

| Existing job / entry | Trigger and active gate | Current work | Target owner | Migration boundary |
|---|---|---|---|---|
| `daily_cycle` → `_daily` | Weekdays at `schedule.run_at`; added when any daily stage is enabled | Executes enabled stages serially: event triggers, news backfill, PEAD daily, technical review, Information digest, portfolio/risk snapshot, risk digest, Journal marks, Chief daily | Dispatcher for registered analysis tasks; Data Platform for news acquisition; Clerk/Internal State for portfolio and ledger jobs; notification/report owners for digests | Split the cascade by owner. Preserve per-stage switches during compatibility; no task inherits the old implicit order. |
| `_event_triggers` | Daily stage `pead_event_triggers` and/or `macro_sector_event_triggers`; reads `config/events.yaml` for today's date | Calls Macro, Sector, or PEAD monitor directly from configured trigger strings | Schedule Calendar + Trigger Ledger + Dispatcher for analysis; Data Platform owns event discovery and publication | Migrate only analysis routing. Treat the YAML as a manual overlay; remove direct Agent calls only after workflow ownership switches. |
| `_news_backfill_daily` | Daily stage `news_backfill` | Acquires Yahoo news history for PEAD targets, observe symbols and signal-chain symbols; writes shared document assets | Data Platform collection controller | Remains outside the analyst task DAG. Dispatcher tasks consume released data products. |
| `pead_daily` | Daily stage `pead_daily`; trading-session gated | Optional research acquisition, PEAD monitoring and pre-earnings preparation | Data Platform for acquisition; Dispatcher for Information/Fundamental routines and other explicitly registered analysis | Separate acquisition from projections. Keep existing PEAD session and prep windows until equivalent Calendar routes are verified. |
| `pead_score_<window>` | Configured BMO/AMC weekday windows, subject to job switches and wide misfire grace | Checks actual-print evidence; scores confirmed releases; handles unknown-session dual-window attempts, transcript upgrades and bounded backfill; may request Chief after final score | Trigger Ledger + Dispatcher for event analysis; Chief only through an explicit complete decision request | Preserve `_confirm_reported`, `_score_plan`, lookback and transcript-upgrade semantics. A planned event alone must never count as an actual release. |
| `technical_daily` | Daily stage `technical_daily` | Calls Technical Analyst directly | Dispatcher (`technical-review`) | Independent analysis task; no implied Chief call. |
| `_chief_daily` | Daily stage `chief_daily`; trading-session gated | Calls Chief after the serial cascade with scheduled source metadata | Dispatcher completeness gate, then existing Chief approval lifecycle | Do not keep the old scheduler's implicit all-upstream-success assumption. Only an explicit decision profile may enter Chief. |
| `weekly_review` → `_weekly_review` | Saturday at `weekly_review_at` and `weekly_review_tz`, when enabled | Macro review, Sector reviews, cross-sections, configured research collection and Chain report | Dispatcher for Macro/Sector/Layer analysis; Data Platform for collection; Chain/report owner for reports | Split by owner and declared dependencies; current cascade's Macro→Sector ordering is not a valid analyst-view dependency in the target model. |
| `_factset_weekly_ingest` | Saturday at `factset_refresh_at` and `factset_refresh_tz`, when enabled | Runs FactSet earnings-insight Data Platform pipeline and publishes/releases source data | Data Platform refresh controller | Remains a data refresh job. Dispatcher only consumes published products. |
| `_journal_reconcile` | Weekdays at configured journal reconciliation time, when both journal and job switches are enabled | Reads broker executions and reconciles them against the local ledger; missed session data can be unrecoverable | Clerk | Keep its independent trigger and recovery policy until Clerk-owned scheduling is active; do not put it in an analyst DAG. |
| `_journal_marks` / `_perf_snapshot` | Daily stages, session dependent | Scores prediction horizons, rebuilds ledger output, snapshots broker portfolio and risk | Clerk for ledger-derived marks and performance; Runtime/Internal State owner for broker portfolio and risk snapshot | Preserve deterministic formulas and as-of/coverage gaps; remove these writes from an analyst run. |
| `_intel_digest` / `_perf_risk_digest` | Daily stages | Generates and publishes digest/report artifacts; optional LLM use | Information reporting / performance reporting owner | A digest is not itself the Information Analyst projection. Migrate only after output ownership is explicit. |
| `ats schedule --now` | Explicit operator invocation | Runs the entire `_daily` cascade once | Dispatcher for explicitly requested analysis; separate existing owners for data and ledger jobs | Replace with explicit task/workflow selection; do not silently replay every old daily stage. |
| `ats schedule --window {amc,bmo}` | Explicit operator invocation | Runs a PEAD score window immediately | Trigger Ledger + Dispatcher, preserving window identity and print confirmation | Manual invocation must be auditable and idempotent when retried with the returned trigger key. |

## Current rollout snapshot

`config/settings.yaml` currently enables `pead_event_triggers` and `pead_daily` in the daily cascade; it disables the other daily stages and the weekly review and reconciliation jobs. FactSet weekly ingest and both PEAD score windows are enabled. These values are deployment settings, not target defaults. `ats schedule --live` currently propagates live mode into PEAD/Chief paths; Phase E migration must not create or widen broker-write authority.

## Phase D role entry readiness

| Target task | Existing callable entry | Scope / projection | Phase E readiness |
|---|---|---|---|
| `layer-review` | `agents.layer.layer_review.run` (the CLI wrapper iterates configured layers) | One `layer` scope per configured AI hardware layer; publishes `layer_analysis` when its `store` is supplied | Role logic and payload exist. Dispatcher adapter and run/attempt envelope association are not yet wired. |
| `information-brief` | `agents.information.entry.run_information_pass` | Currently runs over configured PEAD targets; emits `information_brief` projections by document/entity | Role logic exists. The scheduler adapter must pass explicit entity scope and distinguish pass summary from per-entity projections. |
| `sector-review` | `agents.sector.review.run` (CLI wrapper renders result) | `sector` scope; layered path consumes declared Layer projections and publishes `sector_allocation` | Role logic and dependency check exist. Dispatcher adapter and exact Layer dependency refs remain to be wired. |
| `fundamental-routine` | `agents.fundamental.entry.run_fundamental_pass(routine_request(...))` | `entity` scope; publishes `fundamental_expectation_update` | Explicit trigger request exists. Dispatcher adapter, per-run association and freshness contract remain to be wired. |
| `fundamental-event` | `agents.fundamental.entry.run_fundamental_pass(event_request(...))` | `entity` scope plus required fiscal period/cutoff; publishes `fundamental_event_review` | Explicit trigger request exists. Calendar/release adapter must provide confirmed release material and fiscal label. |
| `macro-review` | `agents.macro.review.run` (CLI wrapper renders result) | Portfolio-wide review; publishes `macro_review` | Role logic and payload exist. Dispatcher adapter and data-vintage association remain to be wired. |
| `technical-review` | `agents.technical.review.run` (CLI wrapper renders result) | One `entity` scope per resolved runtime universe; publishes `technical_review` | Deterministic role logic exists. Dispatcher adapter must freeze its resolved universe and holdings snapshot. |

`agents.chief.assemble.build_chief_snapshot` already validates the six decision categories and their freshness. It remains downstream of the Dispatcher completeness gate. Until an adapter is registered, the scheduler must report the task as `missing`; a task whose declared dependency did not succeed is `blocked`. Neither condition can be represented by a successful empty projection.

## Target ownership rules

- A workflow ID has one active scheduler owner at a time: `legacy`, `shadow`, or `dispatcher`.
- Data refresh, Clerk reconciliation, operational maintenance and analyst work keep separate owners.
- `shadow` uses isolated run/projection writes and cannot submit broker orders.
- Every migrated job records its old trigger window, new trigger identity, input snapshot, output comparison, rollback route and retirement condition.
- The owner switch is per workflow ID. A failure in one migrated workflow does not silently switch unrelated workflows.

## Phase E opt-in operations

Phase E remains opt-in. `config/workflow/workflow_owners.yaml` defaults every workflow to
`legacy`, and `config/workflow/phase_e_schedules.yaml` disables recurring jobs until a
workflow has an explicitly approved `shadow` or `dispatcher` owner. `ats schedule --phase-e`
starts only those enabled Phase E entries plus the configured calendar refresh; it does not
start the legacy daily cascade or enable Chief/Trader execution.

Useful commands:

```text
ats events refresh                 # refresh sources and reconcile admitted earnings documents
ats events status                  # inspect Calendar Data Product freshness/quality
ats events candidates --status conflict
ats events cancel --event-id <id> --actor <name> --reason <reason> --evidence-json '{...}'
ats workflow run --task macro-review --scope-kind portfolio --scope-id portfolio
ats workflow triggers --workflow-id macro-review --schedule-id macro-review \
  --scheduled-from 2026-09-24T00:00:00Z --scheduled-to 2026-09-25T00:00:00Z
ats workflow history --workflow-id <workflow> --trigger-key <stable-key>
ats workflow retry --workflow-id <workflow> --trigger-key <stable-key> \
  --actor <name> --reason <reason>
```

`ats workflow retry` is an explicit same-key compensation for a failed or incomplete
trigger. It preserves the request, plan identity, trigger key, and run ID; the actor/reason
are appended to Trigger Ledger and run history. It does not compensate completed, skipped,
or superseded work. A manual `ats workflow run` response includes its generated `trigger_id`;
replay the same request with `--trigger-id <trigger_id>` to use the same logical trigger.
Trigger queries accept UTC or offset-qualified ISO timestamps and normalize schedule
windows to UTC.

Calendar refresh persists date/time, event identity, versions, source lineage, conflicts,
manual overrides, and release-confirmation references only; it never stores financial
actuals. The Fed parser records meeting-start and policy-statement dates separately and
adds a press-conference event only when the official calendar lists one. BLS/BEA/Fed source
failures preserve last-good events and are surfaced in `ats events status`.

Planned earnings events route once inside the configured 14-day pre-earnings window to
Information and Fundamental routine analysis. Earnings event analysis is released only
after the document platform admits a filing or company release; transcript-only evidence
does not promote the event. The document-ingestion integration must call
`ScheduleCalendarStore.confirm_release(...)` with the current event version and admitted
document references. FOMC and macro release routing uses the same confirmation contract;
calendar arrival by itself is not evidence that a release occurred. Revised event versions
supersede unclaimed triggers, and any event-triggered decision-cycle request is checked
against the current calendar version again before entering Chief.

After a planned release date/time is due, if no required material has been admitted, the
calendar refresh writes an idempotent Trigger Ledger row with status `waiting_material` and
reason `release_material_not_admitted`; it does not create a workflow run or attempt an
event analysis. Date-only events become due on that date in the route timezone, without
assuming a clock time. Each later calendar refresh rechecks the event; once `confirm_release`
publishes the admitted material as a new calendar version, the old wait row becomes
`superseded` and the released version is dispatched normally. Inspect these rows with
`ats workflow triggers --workflow-id <workflow> --status waiting_material`.

Use `events override` for an audited date correction and `events withdraw` to withdraw a
manual override. Custom non-macro events should set `stable_id` in `config/events.yaml` so
date changes append a new version under the same identity. Only use `events cancel` with
operator evidence; removing a YAML row does not silently delete a published event.

The initial workflow owners and recurring schedule entries are all disabled/legacy, so
source refresh and adapter fixtures can be validated before any per-workflow shadow or
dispatcher cutover. Phase E dispatch never enables a new broker-write path; decision-cycle
entry remains explicitly profiled and is not configured by calendar routes.
