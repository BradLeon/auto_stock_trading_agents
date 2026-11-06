"""Chain-layer ceilings that follow the sector analyst's latest allocation call.

`risk.yaml` `sector_layer_caps.<sector>.<layer>.weight_cap` is the STATIC ceiling and is never
exceeded. The sector analyst's latest SectorAllocation says, per layer, 超配/标配/低配/清仓, and
`layer_utilization` (also in risk.yaml) says how much of the ceiling that call may use. The
effective ceiling is `static × utilization` — it can only move DOWN from the static one.

Why this is safe to feed the breach check (2026-11-06; it used to read only the static cap):
a layer breach never forces a sale. Its action is "block / shrink NEW buys into that layer";
forced trims come only from the stop-loss rule. So a 低配 call stops new money going into a
layer — what the call means — without turning a full-but-compliant layer into a sell order.

An allocation older than `MAX_ALLOCATION_AGE_DAYS`, or none at all, falls back to the static
ceiling and says so: "we could not read the call" must not look like a 标配 verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MAX_ALLOCATION_AGE_DAYS = 10      # the sector review is weekly; two missed runs = stale


@dataclass(frozen=True)
class LayerCap:
    static: float | None
    effective: float | None
    allocation: str = ""          # 超配/标配/低配/清仓, "" when the static cap applies
    source: str = "static"        # allocation | static_no_allocation | static_stale
    as_of: str = ""


def latest_allocations(store, sector: str, *, now: datetime | None = None) -> tuple[dict[str, str], str, str]:
    """`({layer key: allocation}, as_of iso, why-not)` from the newest SectorAllocation.

    `why-not` is "" when usable, else "no_allocation" / "stale". Never raises.
    """
    from ..agent.task_projection import ProjectionScope

    now = now or datetime.now(timezone.utc)
    try:
        env = store.reusable_task_projection(
            agent_role="sector_allocation", scope=ProjectionScope(kind="sector", id=sector),
            schema_name="SectorAllocation")
    except Exception as exc:  # noqa: BLE001 - a missing call must not stop the risk review
        log.warning("sector allocation unreadable for %s: %s", sector, exc)
        return {}, "", "no_allocation"
    if env is None:
        return {}, "", "no_allocation"
    payload = env.payload if isinstance(env.payload, dict) else env.payload.model_dump()
    as_of = str(getattr(env, "as_of", "") or getattr(env, "created_at", "") or "")
    try:
        stamp = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age_days = (now - stamp).total_seconds() / 86400
    except ValueError:
        return {}, as_of, "stale"
    if age_days > MAX_ALLOCATION_AGE_DAYS:
        return {}, as_of, "stale"
    calls = {str(item.get("layer")): str(item.get("allocation") or "")
             for item in (payload.get("layers") or []) if item.get("layer")}
    return {k: v for k, v in calls.items() if v}, as_of, ""


def effective_caps(scfg, store, *, now: datetime | None = None) -> dict[str, LayerCap]:
    """{layer key: LayerCap} for every layer of the sector config."""
    from ..agents.sector.cross_section import utilization_for

    calls, as_of, why = latest_allocations(store, scfg.name, now=now) if store is not None \
        else ({}, "", "no_allocation")
    out: dict[str, LayerCap] = {}
    for layer in scfg.layers:
        static = layer.weight_cap
        call = calls.get(layer.key, "")
        if static is None or not call:
            source = "static_stale" if why == "stale" else "static_no_allocation"
            out[layer.key] = LayerCap(static, static, "", source, as_of)
            continue
        out[layer.key] = LayerCap(static, static * utilization_for(call), call, "allocation", as_of)
    return out
