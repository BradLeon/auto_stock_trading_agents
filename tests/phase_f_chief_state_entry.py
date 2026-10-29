"""A Chief entry that reads Internal State through the read gate, for the fallback/recovery tests.

The Chief's real context now reads state through `consumer_api.read_input`, which has no
fallback. The fallback-proof and recovery rules still guard `read_internal` (the Risk role uses
it), so those tests drive it through this minimal Chief-bound entry instead of through the context.
"""
from ats.workflow.runtime_reads import scoped_read


@scoped_read("chief")
def chief_state_entry() -> str:
    from ats.memory import get_store
    from ats.workflow.consumer_reads import read_internal

    state = read_internal(get_store(), consumer="chief")
    lines = []
    if state.portfolio:
        lines.append(f"最新绩效: NetLiq ${state.portfolio['net_liquidation']:,.0f}")
    lines.append(f"内部状态: {state.completeness.status} · {state.section_as_of}")
    return "\n".join(lines)
