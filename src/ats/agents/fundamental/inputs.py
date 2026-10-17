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
    snapshot = consensus.payload or {}
    meta = {"known_at": snapshot.get("known_at") or "", "target_event_date": ""}
    for row in snapshot.get("rows", []):
        try:
            meta["target_event_date"] = meta["target_event_date"] or json.loads(
                row.get("dimensions_json") or "{}").get("target_event_date") or ""
        except (TypeError, ValueError):
            pass
    for row in snapshot.get("rows", []):
        key = {"consensus.eps.mean": "eps", "consensus.revenue.mean": "revenue",
               "consensus.eps.low": "eps_low", "consensus.eps.high": "eps_high",
               "consensus.revenue.low": "revenue_low", "consensus.revenue.high": "revenue_high"}.get(row["metric_id"])
        if key:
            values[key] = row["value"]
    return {"fundamentals_text": financial_summary(financials.payload),
            "as_of": context.cutoff,
            "consensus": values, "consensus_meta": meta,
            "industry_context": json.dumps(hierarchy.payload, ensure_ascii=False, default=str),
            "input_refs": financials.input_refs + consensus.input_refs + hierarchy.input_refs,
            "source_as_of": financials.source_as_of + consensus.source_as_of}


SUMMARY_PERIODS = 8
SUMMARY_MAX_CHARS = 12_000


def financial_summary(payload, *, periods: int = SUMMARY_PERIODS, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """The financial snapshot as a compact table for prompts and the dossier (2026-10-16).

    The governed snapshot holds every reported period with raw provider payloads (TSM: 495 rows, ~568KB),
    and it used to be dumped verbatim into every PEAD prompt (~150K tokens per call). Analysis needs the
    latest periods of each metric: the newest known value per (metric, period), the most recent `periods`
    periods per metric. Lineage stays in input_refs; the full data stays in the data product.
    """
    rows = (payload or {}).get("rows") or []
    latest: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        metric = str(row.get("metric_id") or "")
        if not metric or row.get("value") in (None, ""):
            continue
        # Quarterly and annual values of one metric share period ends; keep them as separate series.
        metric = f"{metric} [{row.get('unit') or ''}, {row.get('period_basis') or ''}]"
        key = (metric, str(row.get("period_start") or ""), str(row.get("period_end") or row.get("period") or ""))
        if key not in latest or str(row.get("known_at") or "") > str(latest[key].get("known_at") or ""):
            latest[key] = row
    by_metric: dict[str, list[dict]] = {}
    for (metric, _start, _end), row in latest.items():
        by_metric.setdefault(metric, []).append(row)
    lines = [f"财务快照摘要（{(payload or {}).get('entity', '')} · as_of {(payload or {}).get('as_of', '')} · "
             f"每项最近 {periods} 期，最新已知值）"]
    for metric in sorted(by_metric):
        items = sorted(by_metric[metric], key=lambda r: (str(r.get("period_end") or r.get("period") or ""),
                                                         str(r.get("period_start") or "")))[-periods:]
        cells = []
        for row in items:
            end = str(row.get("period_end") or row.get("period") or "")
            start = str(row.get("period_start") or "")
            cells.append(f"{start + '..' if start else ''}{end}={_compact(row.get('value'))}")
        lines.append(f"- {metric}: " + "; ".join(cells))
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n…（已截断）"


def _compact(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(number) >= scale:
            return f"{number / scale:.3f}{suffix}"
    return f"{number:.4g}"
