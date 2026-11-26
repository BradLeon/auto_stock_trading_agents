"""Which collected inputs are stale when the Chief decides (2026-11-26).

A failed collection used to be visible only on the dashboard: the Chief decided on whatever was last collected and
neither the model nor the report said that some of it was days old. Before each cycle this reads the same source
lights the dashboard shows (`dashboard.queries.source_rows`) and names every enabled collection point that is not
green. The list goes into the Chief's context (so the model can discount it), to the top of the decision report, and
— for a live cycle — out as one Feishu message. It never blocks the cycle and never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger("ats.runtime.input_freshness")

BLOCK_TITLE = "数据新鲜度（采集异常）"
_LIGHT_CN = {"red": "异常", "yellow": "注意"}


@dataclass(frozen=True)
class StaleSource:
    job_id: str
    label: str
    light: str
    reason: str
    data_kind: str = ""
    data_at: datetime | None = None
    data_period: str = ""

    def age_days(self, now: datetime) -> float | None:
        return None if self.data_at is None else max(0.0, (now - self.data_at).total_seconds() / 86400)


def stale_sources(*, now: datetime | None = None, rows=None) -> list[StaleSource]:
    """Enabled collection points whose light is red or yellow, worst first. Empty when unreadable."""
    now = now or datetime.now(timezone.utc)
    try:
        from .dashboard.queries import DashboardPaths, load_catalog, source_rows

        rows = source_rows(DashboardPaths.from_env(), now) if rows is None else rows
        notes = load_catalog().get("jobs", {})
    except Exception as exc:  # noqa: BLE001 - freshness is advisory; the cycle goes on without it
        log.warning("input freshness unavailable: %s", exc)
        return []
    out = []
    for r in rows:
        if not r.enabled or r.light not in _LIGHT_CN:
            continue
        base = r.job_id.split(".", 1)[0]
        note = notes.get(r.job_id) or notes.get(base)
        dt = getattr(r, "data_time", None)
        out.append(StaleSource(
            job_id=r.job_id, label=(note.label if note else ""), light=r.light,
            reason=r.light_reason or "", data_kind=getattr(dt, "kind", "") or "",
            data_at=getattr(dt, "at", None), data_period=getattr(dt, "period", "") or ""))
    out.sort(key=lambda s: (s.light != "red", s.job_id))
    return out


def _line(s: StaleSource, now: datetime) -> str:
    age = s.age_days(now)
    latest = (f"；最新数据 {s.data_kind or '入库'} {s.data_at:%m-%d}（{age:.1f} 天前）" if s.data_at and age is not None
              else "；没有可用的数据时间")
    if s.data_period:
        latest += f"，数据期 {s.data_period}"
    name = f"{s.job_id}（{s.label}）" if s.label else s.job_id
    return f"- [{_LIGHT_CN[s.light]}] {name}：{s.reason or '状态异常'}{latest}"


def block(stale: list[StaleSource], *, now: datetime | None = None) -> str:
    """The context/report block: what is stale and how old the newest data is."""
    if not stale:
        return ""
    now = now or datetime.now(timezone.utc)
    head = (f"以下 {len(stale)} 个采集点最近没有正常采集，相关信息可能滞后；"
            "引用这些来源时请按其最新数据时间折算可信度，不要当作当日信息：")
    return "\n".join([head, *(_line(s, now) for s in stale)])


def notify(cycle_id: str, stale: list[StaleSource], *, now: datetime | None = None, push=None) -> bool:
    """One Feishu message per live cycle. Never raises."""
    if not stale:
        return False
    try:
        if push is None:
            from ..workflow.consumer_health import feishu_notify as push
        red = sum(1 for s in stale if s.light == "red")
        title = f"首席决策输入滞后：{len(stale)} 个采集点" + (f"（{red} 个异常）" if red else "")
        push(title, f"周期 {cycle_id}\n" + block(stale, now=now))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("input freshness notification failed: %s", exc)
        return False


__all__ = ["BLOCK_TITLE", "StaleSource", "block", "notify", "stale_sources"]
