"""Governed L1 Evidence Observer consumer for Claude work-adoption data.

This module deliberately consumes only DataProducts. It does not expose repository
handles, provider URLs, or physical-table queries to the Observer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


CONSUMER = "evidence_observer"
SEMANTIC_GUARDRAILS = {
    "usage_share": "职业或任务占对应 Claude 产品总使用量的份额，不是从业者采用率。",
    "industry": "官网 Industry 是 SOC occupational major group，不是企业所属行业。",
    "observed_exposure": "Observed Exposure 是研究快照，不是月度使用序列。",
    "allowed_claims": (
        "Claude 使用量在职业间的分布",
        "同口径月份间自动化占比的变化",
        "公开 task-cell 的可见覆盖",
    ),
    "prohibited_claims": (
        "员工采用率",
        "企业席位渗透率",
        "岗位替代数量",
        "交易信号或证据权重",
    ),
}


def _usage_fact(job: dict[str, Any], *, source_product: str, period: str) -> dict[str, Any] | None:
    value = (job.get("metrics") or {}).get("ai.work_adoption.usage_share")
    if value is None:
        return None
    name = job.get("name") or job.get("entity_id", "")
    return {
        "kind": "claude_usage_share",
        "entity_id": job.get("entity_id", ""),
        "period": period,
        "source_product": source_product,
        "value": value,
        "unit": "percent",
        "statement": f"{period}，{name} 占 {source_product} 总使用量的 {value:g}%（不是该职业从业者采用率）。",
        "lineage": job.get("lineage", {}),
    }


def observe_work_adoption(*, source_product: str, period: str = "", occupation: str = "",
                          as_of: datetime | None = None, top_n: int = 10,
                          products=None) -> dict[str, Any]:
    """Return a bounded, replayable L1 observation packet.

    The generated statements use closed deterministic templates so provider metrics
    cannot be relabelled as worker adoption or employer-industry statistics.
    """
    if top_n < 0:
        raise ValueError("top_n must be non-negative")
    if products is None:
        from ...data.products import get_platform_data_products

        products = get_platform_data_products()

    snapshot = products.ai_work_adoption_snapshot(
        source_product=source_product,
        period=period,
        as_of=as_of,
        snapshot_consumer=CONSUMER,
        snapshot_purpose=f"l1_work_adoption:{source_product}:{period or 'latest'}",
    )
    selected_period = snapshot.get("period", period)
    facts = [
        fact for job in snapshot.get("jobs", [])[:top_n]
        if (fact := _usage_fact(job, source_product=source_product, period=selected_period)) is not None
    ]
    profile = None
    if occupation and snapshot.get("status") == "ok":
        profile = products.ai_job_profile(
            occupation, source_product=source_product, period=selected_period, as_of=as_of)
    return {
        "status": snapshot.get("status", "no_coverage"),
        "consumer": CONSUMER,
        "source_access": "data_products_only",
        "source_product": source_product,
        "period": selected_period,
        "as_of": as_of.isoformat() if as_of else None,
        "facts": facts,
        "snapshot": snapshot,
        "job_profile": profile,
        "manifest": snapshot.get("manifest"),
        "semantic_guardrails": SEMANTIC_GUARDRAILS,
    }
