"""Decision audit domain (Phase B of docs/TARGET_WORKFLOW_DATAFLOW.md).

The lifecycle state machine, content hashing and legacy-migration semantics for
`decision_cycles` / `decision_revisions` / `decision_risk_reviews` /
`boss_approvals` / `cycle_events`. Storage lives in :mod:`ats.memory.store`
(Workflow memory — decisions carry opinions); this package holds the rules that
must not drift between the store, the graph and the execution gate.
"""
