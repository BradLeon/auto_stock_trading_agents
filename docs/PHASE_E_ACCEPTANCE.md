# Phase E Reliability Acceptance

This checklist maps the three OpenSpec delta specs to reproducible tests. The suite is
provider-free: external calendar adapters are exercised with checked-in fixtures, and
Agent adapters use deterministic test doubles. Live provider terms, credentials, ingestion
admission, and shadow/dispatcher rollout remain deployment checks.

## Requirement-to-test matrix

| Spec area | Requirement | Tests / evidence |
|---|---|---|
| Dispatcher runtime | Registry, dependency boundaries, plan expansion, fixed scope/config hashes | `tests/test_phase_e_planning.py` |
| Dispatcher runtime | Parallel branches, failed-dependency blocking, retries, timeout fencing, exact projection gate, event-version Chief guard | `tests/test_phase_e_dispatcher.py`, `tests/test_memory_task_scope.py` |
| Trigger Ledger | Additive bootstrap, state history, idempotent claim, leases, event supersession, schedule-window queries | `tests/test_workflow_store.py` |
| Trigger Ledger | Stable identities, timezone-normalized cron ticks, duplicate delivery, dual workers, lease loss, misfires, same-key compensation | `tests/test_workflow_triggers.py` |
| Trigger Ledger | Legacy/shadow/dispatcher ownership boundaries and calendar version reconciliation | `tests/test_workflow_ownership.py` |
| Event Calendar | Candidate identity, conflict/quarantine, version/as-of semantics, overrides/withdrawal, release references, cancellation | `tests/test_schedule_calendar_store.py` |
| Event Calendar | Fed/BLS/BEA fixture parsing, parser-drift rejection, refresh idempotency/failure, manual identity, earnings admission gate | `tests/test_calendar_refresh.py` |
| Event Calendar | Calendar product freshness, lineage, conflict quality | `tests/test_schedule_calendar_product.py` |
| Event routing | Allow-listed route, admitted release materials, planned earnings lead window, conflict rejection, due releases recorded as `waiting_material` without analysis | `tests/test_workflow_triggers.py`, `tests/test_workflow_ownership.py` |
| Legacy PEAD boundary | Unknown-session BMO/AMC probing, score/retry eligibility and existing scheduler job behavior remain intact | `tests/test_earnings_union.py`, `tests/test_score_state.py`, `tests/test_scheduler_jobs.py` |
| Owner rollback | Legacy mode stops new Phase E claims while an active shadow lease completes and its run/trigger history remains queryable | `tests/test_workflow_ownership.py::test_owner_rollback_stops_new_claims_but_preserves_and_finishes_active_lease` |

## Local verification

Run from the repository root:

```text
.venv/bin/python -m pytest \
  tests/test_phase_e_planning.py \
  tests/test_phase_e_dispatcher.py \
  tests/test_memory_task_scope.py \
  tests/test_workflow_store.py \
  tests/test_workflow_triggers.py \
  tests/test_workflow_ownership.py \
  tests/test_schedule_calendar_store.py \
  tests/test_calendar_refresh.py \
  tests/test_schedule_calendar_product.py \
  tests/test_earnings_union.py \
  tests/test_score_state.py \
  tests/test_scheduler_jobs.py -q
```

Acceptance matrix: **120 passed** (2026-09-24). Including the scheduler CLI compatibility
smoke test, the combined run passed **121 tests**.

`python -m compileall` over the changed workflow/calendar/CLI modules is also expected to
pass. Full application tests remain necessary before a deployment cutover; this targeted
matrix does not exercise broker writes and does not authorize them.

## Rollback exercise

Before enabling a Phase E owner, keep the old owner available, stop the scheduler, and
pause new claims for that workflow. Allow active leases to finish or expire; inspect
`ats workflow triggers --workflow-id <id>` and `ats workflow history --workflow-id <id>
--trigger-key <key>`. Restore `mode: legacy` in `config/workflow/workflow_owners.yaml`,
restart the old scheduler, and verify the Phase E run/projection/calendar ledgers remain
queryable. Shadow mode uses a separate SQLite file; deleting it is not part of rollback.
The local rollback fixture exercises this transition with an isolated owner config and
temporary database; production cutover/rollback still requires an operator rehearsal.

No workflow owner or recurring Phase E schedule is enabled by default. No phase-E route
starts Chief, Risk, Boss approval, or Trader execution.
