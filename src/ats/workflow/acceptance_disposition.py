"""Required failures need repair; absent measurements need another run.

Optional input/coverage decisions remain governed by consumer_disposition. An
old-side defect or a difference acceptance never changes a required assertion.
"""
def dispose(result, *, legacy_diagnostics=()):
    repairs, measurements = [], []
    for row in result.results:
        if row.required and row.status != "passed":
            (repairs if row.status == "failed" else measurements).append(row.assertion_id)
    return {"eligible": result.passed, "repair_and_rerun": repairs,
            "measure_and_rerun": measurements,
            "legacy_diagnostics": list(legacy_diagnostics),
            "legacy_acceptance_can_waive": False}
