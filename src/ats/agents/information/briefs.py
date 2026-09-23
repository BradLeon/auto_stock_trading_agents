"""Information brief publication contract (Phase D, agent/information-analyst).

Everything a brief must pass before it becomes a projection lives here, so the
rule set is checkable in one place:

* six evidence elements REQUIRED (missing any = failed run, not a degraded write);
* buy/sell/position advice intercepted at the payload boundary;
* three clocks per fact change (event / publish / extract) with freshness judged
  by publish time, never by ingestion time;
* same-event clustering with confidence explicitly NOT raised by cluster size.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from ...agent.task_projection import (EnvelopeValidationError, ProjectionScope,
                                      build_envelope)

log = __import__("logging").getLogger("ats.agents.information.briefs")

# 方向性交易建议词表（4.7）。英文走词边界匹配，中文子串匹配——
# "additionally" 这类词不能被 "add" 误伤。
_ADVICE_EN = ("buy", "sell", "trim", "overweight", "underweight",
              "target position", "target weight")
_ADVICE_ZH = ("买入", "卖出", "增持", "减持", "加仓", "减仓", "清仓",
              "目标仓位", "建议仓位")
_ADVICE_EN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _ADVICE_EN) + r")\b",
    re.IGNORECASE)


def assert_no_advice(texts) -> None:
    """Reject any directional trading advice inside a brief's text fields (4.7)."""
    for text in texts:
        if not text:
            continue
        hit = _ADVICE_EN_RE.search(text)
        if hit:
            raise EnvelopeValidationError(
                f"信息简报不得包含交易建议：命中英文建议词 {hit.group(0)!r}")
        for zh in _ADVICE_ZH:
            if zh in text:
                raise EnvelopeValidationError(
                    f"信息简报不得包含交易建议：命中中文建议词 {zh!r}")


def require_six_elements(payload: dict) -> None:
    """Six evidence elements are the publication minimum (4.6).

    Missing fact changes OR missing to-verify items OR missing confidence =
    the run failed. Callers must NOT fall back to a free-text degraded write.
    """
    missing = []
    if not (payload.get("fact_changes") or []):
        missing.append("fact_changes")
    if not (payload.get("unverified") or []):
        missing.append("unverified")
    if payload.get("confidence") is None:
        missing.append("confidence")
    if missing:
        raise EnvelopeValidationError(
            "信息简报六要素不完整，产出判为失败：" + "、".join(missing))


def publish_information_brief(store, payload: dict, *, scope: ProjectionScope,
                              as_of: str, input_refs=None, valid_until: str = "",
                              supersedes_projection_id: str = "") -> str:
    """Validate, intercept, wrap and persist one InformationBrief projection.

    Returns the projection id. Raises `EnvelopeValidationError` on contract
    violations — no partial or degraded row is ever written.
    """
    require_six_elements(payload)
    assert_no_advice([
        payload.get("headline", ""), payload.get("summary", ""),
        *(payload.get("fact_changes") or []),
        *(payload.get("impact_candidates") or []),
    ])
    envelope = build_envelope(
        role="information_brief", payload=payload, scope=scope, as_of=as_of,
        input_refs=input_refs, valid_until=valid_until,
        supersedes_projection_id=supersedes_projection_id)
    store.save_task_projection_envelope(envelope)
    return envelope.projection_id


# --------------------------------------------------------------------------- #
# Three clocks (4.10)
# --------------------------------------------------------------------------- #

def _parse(ts) -> datetime | None:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def clocks_for(record: dict, *, extracted_at: datetime | None = None) -> dict:
    """Event time / publish time / extract time for one source record.

    * 事件时间 — `event_time` if the record carries one, else the publish time
      (for news, the thing reported and the thing published usually coincide);
    * 发布时间 — `published_at`（对外可见的时刻，时效与 cutoff 的判据）;
    * 抽取时间 — 系统入库抽取的时刻（只做记录，绝不参与时效判定）。
    """
    published = _parse(record.get("published_at"))
    event = _parse(record.get("event_time")) or published
    return {
        "event_time": (event or extracted_at or datetime.now(timezone.utc)
                       ).isoformat(timespec="seconds"),
        "published_at": (published or extracted_at or datetime.now(timezone.utc)
                         ).isoformat(timespec="seconds"),
        "extracted_at": (extracted_at or datetime.now(timezone.utc)
                         ).isoformat(timespec="seconds"),
    }


def is_new_by_publish(record: dict, cutoff: datetime) -> bool:
    """Freshness/cutoff is judged by PUBLISH time, never ingestion time (4.10).

    A document published before the cutoff but ingested after it (早发布、晚
    入库) is NOT new information for this window — it is late material.
    """
    published = _parse(record.get("published_at"))
    if published is None:
        # 无发布时间的记录无法判定期间正确性：保守按"非本期"处理。
        return False
    return published >= cutoff


# --------------------------------------------------------------------------- #
# Same-event clustering (4.11)
# --------------------------------------------------------------------------- #

def cluster_key(headline: str) -> str:
    """Deterministic same-event key: normalized headline digest.

    Same event reported by N outlets shares the normalized headline core, so
    they collapse into one cluster; a different event cannot collide unless it
    word-for-word repeats another headline (in which case dedup is correct).
    """
    normalized = re.sub(r"[^\w\s]", "", (headline or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""
    return "evt-" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def cluster_summary(records: list[dict]) -> dict:
    """Cluster stats for a set of same-event records.

    `independent_sources` counts DISTINCT sources — 4 篇同源报道 ≠ 4 个独立
    信源。`confidence` 是簇内最大单文档置信度：文档数量本身不得上调置信度
    （同源转载只是放大，不是佐证）。
    """
    sources = {str(r.get("source") or "").strip() for r in records} - {""}
    confidences = [float(r["confidence"]) for r in records
                   if r.get("confidence") is not None]
    return {
        "document_count": len(records),
        "independent_sources": len(sources),
        "sources": sorted(sources),
        "confidence": max(confidences) if confidences else None,
    }
