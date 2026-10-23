"""Readable reports published after a Dispatcher run.

A report is a human-readable copy of what a run published; it is not an analysis result. So publishing is a
separate step AFTER the run: a report that cannot be written (no output directory, unwritable, a rendering
error) is recorded in the run's return value and never changes the run's status or blocks anything.

Which reports exist (see openspec change retire-legacy-and-migration-tooling, design D9):

* technical-review: one summary report over every symbol the run read. The task itself is per symbol, so
  only the run as a whole has all the readings.
* sector-review: the weekly chain-evidence report, after the sector review. The sector report, the per-layer
  reports and the html dashboard are written by the sector task itself (`agents.sector.review.run`), which is
  the only place that has their data.
* macro-review: written by the macro task (`agents.macro.report.write_new_version`).

A run that reused earlier projections and published nothing new writes no report. Every writer creates a new
file (`-v2`, `-v3`, ... on the same day) and never replaces an existing one.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

log = logging.getLogger("ats.report_publishers")


def _new_refs(outcomes: list[dict], task_id: str) -> list[str]:
    """Projection ids this run published for `task_id` (reused earlier projections do not count)."""
    refs: list[str] = []
    for item in outcomes:
        if item.get("task_id") == task_id and item.get("status") == "succeeded" and not item.get("reused"):
            refs.extend(item.get("projection_refs") or [])
    return refs


def technical_report(outcomes: list[dict], *, store_path: str | None) -> dict[str, Any]:
    from ..agents.technical import report as tech_report
    from ..config import load_technical_config
    from ..memory import task_store_scope
    from ..schemas.technical import TechnicalReading, TechnicalReview

    refs = set(_new_refs(outcomes, "technical-review"))
    if not refs:
        return {"report": "technical", "status": "skipped", "reason": "no_new_projection"}
    with task_store_scope(store_path) as memory:
        rows = [row for row in memory.task_projection_envelopes(
            agent_role="technical_review", status="published", limit=500) if row["projection_id"] in refs]
    if not rows:
        return {"report": "technical", "status": "skipped", "reason": "projections_not_found"}
    cfg = load_technical_config("technical")
    readings: list[TechnicalReading] = []
    for row in rows:
        payload = row["payload"]
        levels = payload.get("levels") or {}
        if payload.get("signal") == "unavailable":
            readings.append(TechnicalReading(symbol=str(payload.get("entity") or row["scope_id"]), stale=True,
                                             note=str(payload.get("summary") or "数据缺失")))
            continue
        target = float(payload.get("suggested_exposure") or 0.0)
        previous = payload.get("previous_exposure")
        readings.append(TechnicalReading(
            symbol=str(payload.get("entity") or row["scope_id"]), score=int(payload.get("score") or 0),
            score_detail=payload.get("score_detail") or {}, raw_ladder=target, vol_scalar=1.0,
            target_exposure=target, prev_exposure=previous,
            panic_fired=bool(payload.get("panic_fired")), bear_fired=bool(payload.get("bear_fired")),
            close=levels.get("close"), sma20=levels.get("sma20"), sma50=levels.get("sma50"),
            sma200=levels.get("sma200"), bars=0, stale=False, note=""))
    as_of = max(datetime.fromisoformat(str(row["as_of"])) for row in rows)
    review = TechnicalReview(
        name=cfg.name, as_of=as_of, strategy=cfg.strategy,
        fingerprint=str(rows[0].get("model_version", "")).rsplit("@", 1)[-1],
        market_panic=any(r.panic_fired for r in readings), readings=readings,
        notes=["由本次运行已发布的技术面投影汇总；VIX 与 VIX3M 数值不在投影内，市场状态只显示是否触发恐慌层"])
    path = tech_report.write(review, cfg)
    if path is None:
        return {"report": "technical", "status": "skipped", "reason": "output_dir_unset_or_missing"}
    return {"report": "technical", "status": "written", "path": str(path), "symbols": len(readings)}


def chain_evidence_report(outcomes: list[dict], *, store_path: str | None) -> dict[str, Any]:
    from ..chain import report as chain_report
    from ..config import load_pead_global, load_sector_config
    from ..memory import task_store_scope

    sectors = sorted({(item.get("scope") or {}).get("id") for item in outcomes
                      if item.get("task_id") == "sector-review" and item.get("status") == "succeeded"
                      and not item.get("reused") and (item.get("scope") or {}).get("kind") == "sector"})
    if not sectors:
        return {"report": "chain_evidence", "status": "skipped", "reason": "no_new_sector_review"}
    ind_cfg = load_pead_global().get("induction", {})
    written, skipped = [], []
    with task_store_scope(store_path) as memory:
        for name in sectors:
            path = chain_report.write(load_sector_config(name), memory,
                                      as_of=datetime.now(timezone.utc), ind_cfg=ind_cfg)
            (written if path else skipped).append(str(path) if path else name)
    if not written:
        return {"report": "chain_evidence", "status": "skipped", "reason": "output_dir_unset_or_missing"}
    return {"report": "chain_evidence", "status": "written", "paths": written}


PUBLISHERS: Mapping[str, tuple[Callable[..., dict[str, Any]], ...]] = {
    "technical-review": (technical_report,),
    "sector-review": (chain_evidence_report,),
}


def publish(workflow_id: str, dispatched: Mapping[str, Any], *, store_path: str | None) -> list[dict[str, Any]]:
    """Run the workflow's report publishers over a finished dispatch; never raises."""
    if dispatched.get("duplicate") or not isinstance(dispatched.get("result"), Mapping):
        return []
    outcomes = list(dispatched["result"].get("outcomes") or [])
    results: list[dict[str, Any]] = []
    for publisher in PUBLISHERS.get(workflow_id, ()):
        name = publisher.__name__
        try:
            results.append(publisher(outcomes, store_path=store_path))
        except Exception as exc:  # noqa: BLE001 - a report must never change the run
            log.warning("report %s for %s failed: %s", name, workflow_id, exc)
            results.append({"report": name, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return results
