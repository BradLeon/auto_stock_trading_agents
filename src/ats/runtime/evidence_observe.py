"""Chain-evidence extraction for one company, run by the data refresh controller.

Why this lives here (2026-10-16): extraction used to be a side effect of the PEAD score
window in the legacy scheduler (`scheduler._observe_window`). When the user turned that
window off, nothing else collected evidence and 9–10 月 left no facts at all. ARCHITECTURE §3 has
the data platform collect on its own cadence, so the controller now schedules this per
entity (`config/data/schedules.yaml` -> `derived.jobs.chain_evidence_daily`) and the
legacy window calls the same function.

The guards are the ones the window had, unchanged:
  * double confirmation that the print is released (actual EPS, or an 8-K filed on/after
    the print date) — otherwise last quarter's filing is read as new;
  * a document already extracted is never read again (idempotent per document);
  * the period-guarded fetch (`observer.fetch_document`), so a wrong company or quarter
    is a gap, not an observation.

Hard boundary: this writes to the evidence tables only — no scoring, no dossier, no
Chief, no orders.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("ats.runtime.evidence_observe")

DAILY_LOOKBACK_DAYS = 7


def observe_entity(symbol: str, *, today: date | None = None,
                   lookback_days: int = DAILY_LOOKBACK_DAYS, dry_run: bool = False,
                   store=None) -> dict:
    """Extract the latest confirmed print of `symbol` into chain evidence.

    Returns a packet whose `status` is what the controller classifies:
    `succeeded` (new facts written), `no_change` (nothing to do; `reason` says why) or
    `failed` (extraction or fetch failed — retried by the next window, never recorded as
    zero observations).
    """
    from ..config import canonical_entity
    from ..data import earnings_calendar
    from ..data.products import earnings

    sym = canonical_entity(symbol) if symbol else ""
    if not sym:
        return {"status": "failed", "entity": symbol, "reason": "entity_required"}
    packet: dict = {"entity": sym, "lookback_days": lookback_days, "dry_run": dry_run}
    try:
        pr = earnings_calendar.last_print(sym, as_of=today, back_days=lookback_days)
    except Exception as exc:  # noqa: BLE001 - calendar outage is a retryable failure
        return {**packet, "status": "failed", "reason": f"calendar_unavailable:{type(exc).__name__}"}
    if pr is None or not pr.date:
        return {**packet, "status": "no_change", "reason": "no_print_in_window"}
    packet["print_date"] = pr.date.isoformat()
    ok, evidence = earnings.confirm_reported(sym, pr)
    if not ok:
        return {**packet, "status": "no_change", "reason": "release_not_confirmed",
                "detail": str(evidence)}
    packet["confirmation"] = str(evidence)
    doc_id = f"{sym}:{pr.date:%Y%m%d}"

    if store is None:
        from ..memory import get_store

        store = get_store()
    if store.has_observations_for_document(doc_id):
        return {**packet, "status": "no_change", "reason": "already_extracted", "document_id": doc_id}
    if dry_run:
        return {**packet, "status": "succeeded", "reason": "would_extract", "document_id": doc_id}

    from ..agents.evidence import observer
    from ..data.pipelines.unstructured import documents as document_pipeline

    try:
        text, src, note = observer.fetch_document(sym, print_=pr, store=store)
    except Exception as exc:  # noqa: BLE001 - a fetch outage must not look like "no evidence"
        return {**packet, "status": "failed", "reason": f"fetch_failed:{type(exc).__name__}"}
    if note:
        packet["note"] = note
    if not (text or "").strip():
        # No current, verified document: a gap. Next window retries (a transcript that did
        # not exist yesterday may exist today).
        return {**packet, "status": "no_change", "reason": "no_verified_document", "document_id": doc_id}
    asset = document_pipeline.identify(text, entity=sym, store=store)
    if asset and store.has_observations_for_document(asset["document_id"]):
        return {**packet, "status": "no_change", "reason": "already_extracted",
                "document_id": asset["document_id"]}
    result = observer.observe_document(sym, doc_id, text, source_url=src, period=str(pr.date), store=store)
    packet.update(document_id=(asset or {}).get("document_id") or doc_id, source=src,
                  new=result["new"], saved=result["saved"])
    if result["failure"]:
        return {**packet, "status": "failed", "reason": "extraction_failed",
                "detail": str(result["failure"])}
    return {**packet, "status": "succeeded" if result["new"] else "no_change",
            "reason": "" if result["new"] else "no_new_facts"}
