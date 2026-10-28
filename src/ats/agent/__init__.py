"""Analysis-role contracts.

This package owns what an *analysis role* is allowed to produce — never how the
result is scheduled, executed or booked (those live in `workflow/` and
`execution/`). The single most important object here is the projection envelope in
`ats.agent.task_projection`: every agent output must be published through it so the
rest of the system can tell what a conclusion depends on and when it stops being
true.
"""
