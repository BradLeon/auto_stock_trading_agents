"""Persistent, point-in-time company inputs for the Fundamental business graph."""
from __future__ import annotations

import json

from ...data.consumer_api import read_input
from ...workflow.isolation import verified_isolation_root
from ...workflow.runtime_reads import current_read_context


def governed_company_inputs(symbol):
    context = current_read_context()
    if context is None or (context.route != "target" and verified_isolation_root() is None):
        return None  # Existing legacy entry; never used as a target fallback.
    financials = read_input("fundamental", "COMPANY_DATA", scope={"kind": "financials", "entity": symbol})
    consensus = read_input("fundamental", "COMPANY_DATA", scope={"kind": "consensus", "entity": symbol})
    hierarchy = read_input("fundamental", "HIER_DATA", scope={"kind": "evidence", "entity": symbol})
    if financials.status != "complete":
        from ...workflow.cutover_routing import RouteUnavailable

        raise RouteUnavailable("fundamental financial input unavailable: " + ";".join(financials.gaps))
    values = {}
    for row in (consensus.payload or {}).get("rows", []):
        key = {"consensus.eps.mean": "eps", "consensus.revenue.mean": "revenue",
               "consensus.eps.low": "eps_low", "consensus.eps.high": "eps_high",
               "consensus.revenue.low": "revenue_low", "consensus.revenue.high": "revenue_high"}.get(row["metric_id"])
        if key:
            values[key] = row["value"]
    return {"fundamentals_text": json.dumps(financials.payload, ensure_ascii=False, default=str),
            "as_of": context.cutoff,
            "consensus": values,
            "industry_context": json.dumps(hierarchy.payload, ensure_ascii=False, default=str),
            "input_refs": financials.input_refs + consensus.input_refs + hierarchy.input_refs,
            "source_as_of": financials.source_as_of + consensus.source_as_of}
