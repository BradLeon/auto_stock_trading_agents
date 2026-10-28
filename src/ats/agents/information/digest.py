"""Intel digest, migrated from runtime/digest.py (Phase D task 4.5).

The Boss-facing markdown + Feishu card keep rendering exactly as before; what
is new is that the digest's per-target synthesis is ALSO published as
InformationBrief projections — the digest output becomes queryable workflow
state instead of an unreadable-into-history side effect. The takeaways stay in
the rendered report; the projected payload carries only neutral six-element
content (the advice interceptor would reject anything else).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .briefs import clocks_for, publish_information_brief

log = logging.getLogger("ats.agents.information.digest")


def publish_digest_briefs(store, per: dict[str, dict], *, cutoff: datetime,
                          min_triage: float) -> int:
    """Publish one brief per target with material events in the window.

    `per` is the digest's {symbol: {events, insights, delta}} structure, already
    filtered by the runtime. Returns the number of briefs published. Publication
    is best-effort per target: one contract failure must not cost the others.
    """
    published = 0
    for sym, d in per.items():
        events = d.get("events") or []
        insights = d.get("insights") or []
        if not events and not insights:
            continue
        current = [e for e in events if _published_within(e, cutoff)]
        fact_changes = [
            f"[{str(e.get('published_at') or '')[:10]} · 材料度 {e.get('triage_score') or 0:.2f}] "
            f"{e.get('headline') or ''}" for e in current]
        fact_changes += [
            f"📰 [{i.get('direction', '')}/{i.get('impact_path', '')} · 置信 {i.get('confidence') or 0:.2f}] "
            f"{i.get('summary') or ''}" for i in insights]
        if d.get("delta"):
            fact_changes.append(f"Δthesis 摘录：{str(d['delta'])[:300]}")
        sources = sorted({str(e.get("source") or "") for e in current} - {""})
        now = datetime.now(timezone.utc)
        clocks = clocks_for({"published_at": (current[0].get("published_at") if current else "")},
                            extracted_at=now)
        confidences = [float(i.get("confidence") or 0) for i in insights if i.get("confidence")]
        payload = {
            "entity": sym,
            "headline": f"{sym} 每日情报（{len(current)} 事件 · {len(insights)} insight）",
            "summary": f"近窗材料性事件与研报要点汇总，详见每日情报报告。",
            "relevance": "high" if any((e.get("triage_score") or 0) >= min_triage + 0.3
                                       for e in current) else "medium",
            "sources": sources or ["aggregated"],
            "fact_changes": fact_changes or ["窗口内无新事实变化"],
            "impact_candidates": sorted({i.get("impact_path", "") for i in insights} - {""}),
            "entities": [sym],
            "confidence": max(confidences) if confidences else 0.5,
            "freshness": f"window since {cutoff:%Y-%m-%dT%H:%M}",
            "unverified": ["各条目影响幅度待核验，置信度沿用单条评估未按来源数上调"],
            "event_time": clocks["event_time"],
            "published_at": clocks["published_at"],
            "extracted_at": clocks["extracted_at"],
            "cluster_key": f"digest-{sym}",
            "source_count": len(current),
            "independent_sources": len(sources),
        }
        try:
            from ...agent.task_projection import ProjectionScope

            publish_information_brief(
                store, payload,
                scope=ProjectionScope(kind="entity", id=sym),
                as_of=clocks["extracted_at"], input_refs=[])
            published += 1
        except Exception as exc:  # noqa: BLE001 - per-target isolation
            log.warning("digest brief publication failed for %s: %s", sym, exc)
    return published


def _published_within(record: dict, cutoff: datetime) -> bool:
    from datetime import datetime as _dt

    raw = str(record.get("published_at") or "")
    if not raw:
        return False
    try:
        published = _dt.fromisoformat(raw)
    except ValueError:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published >= cutoff
