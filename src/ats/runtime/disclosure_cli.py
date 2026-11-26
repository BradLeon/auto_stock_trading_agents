"""`ats data disclosure-*`: extract company disclosure metrics from stored filings and release them by hand."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

ACTIONS = ("disclosure-extract", "disclosure-candidates", "disclosure-confirm", "disclosure-reject",
           "disclosure-correct", "disclosure-series")
WRITES = ("disclosure-extract", "disclosure-confirm", "disclosure-reject", "disclosure-correct")
CORRECTABLE = ("value_text", "scale", "scale_quote", "period_end", "period_basis", "quote", "period_quote",
               "header_quote", "column_index", "bucket", "qualifier")
_STATES = {"needs_evidence": "待确认", "accepted": "已发布", "rejected": "已驳回", "superseded": "已撤回"}


def _readonly():
    from ..data.runtime.repository import platform_artifact_root, platform_data_db_path
    from ..data.stores.structured.repository import SQLiteStructuredRepository

    return SQLiteStructuredRepository(platform_data_db_path(), artifact_root=platform_artifact_root(), readonly=True)


def candidates(repository: Any, entity: str, *, form: str = "", metric: str = "", status: str = "") -> list[dict]:
    """Candidates a person reviews, from the latest extraction of each filing and metric (plus every reviewed one)."""
    from ..config import ledger_entity
    from ..data.pipelines.structured import disclosure_metrics as pipeline

    _, metrics = pipeline.issuer_metrics(entity, [metric] if metric else None)
    names = {item.metric_id: item for item in metrics}
    subject = ledger_entity(entity.upper())
    current = {(row["metric_id"], row["document_id"]): row["extraction_id"]
               for row in repository.document_extractions(entity_id=subject)}
    rows = []
    for row in repository.evidence_candidates(entity_id=subject, limit=100_000):
        if row["dataset_id"] != pipeline.DATASET_ID or row["metric_id"] not in names:
            continue
        raw = json.loads(row["raw_payload"])
        if form and raw["form_type"] != form.upper():
            continue
        if row["status"] == "needs_evidence" and raw["extraction_id"] != current.get(
                (row["metric_id"], row["document_id"])):
            continue                      # left over from an earlier request for the same filing
        reasons = json.loads(row["reason_codes_json"] or "[]")
        state = "evidence_incomplete" if row["status"] == "needs_evidence" and reasons else row["status"]
        if status and status not in {state, row["status"]}:
            continue
        item = names[row["metric_id"]]
        rows.append({
            "candidate_id": row["candidate_id"], "metric": item.key, "name": item.name, "value": row["value"],
            "unit": row["unit"], "period": row["period"], "period_basis": raw["period_basis"],
            "bucket": raw["bucket"], "form_type": raw["form_type"], "state": state, "reasons": reasons,
            "qualifier": raw["qualifier"], "value_text": raw["value_text"], "scale": raw["scale"],
            "quote": raw["quote"], "period_quote": raw["period_quote"], "header_quote": raw["header_quote"],
            "scale_quote": raw["scale_quote"], "document_id": row["document_id"],
            "char_start": row["char_start"], "char_end": row["char_end"], "corrects": raw.get("corrects", "")})
    order = [item.key for item in metrics]
    return sorted(rows, key=lambda r: (order.index(r["metric"]), r["form_type"], r["period"], r["period_basis"],
                                       r["bucket"], r["candidate_id"]))


def markdown(rows: list[dict]) -> str:
    lines, last = [], ""
    for row in rows:
        if row["metric"] != last:
            lines += ["", f"## {row['name']}（{row['metric']}）"]
            last = row["metric"]
        value = f"{row['value']:,.0f}" if row["unit"] != "percent" else f"{row['value']:g}%"
        state = "证据不完整：" + ",".join(row["reasons"]) if row["state"] == "evidence_incomplete" \
            else _STATES.get(row["state"], row["state"])
        bucket = f" / {row['bucket']}" if row["bucket"] else ""
        lines.append(f"\n- `{row['candidate_id']}` **{value}** {'' if row['unit'] == 'percent' else row['unit']} · "
                     f"{row['period']}（{row['period_basis']}{bucket}） · {row['form_type']} · {state}")
        lines.append(f"  - 原文：{row['quote']}")
        for label, key in (("期间依据", "period_quote"), ("表头", "header_quote"), ("单位依据", "scale_quote")):
            if row[key]:
                lines.append(f"  - {label}：{row[key]}")
    return "\n".join(lines).strip() or "（没有候选）"


def run(action: str, *, entity: str, form: str = "", metric: str = "", status: str = "", as_of: str = "",
        dry_run: bool = False, output_format: str = "json", document_id: str = "",
        candidate_ids: list[str] | None = None, reviewer: str = "", note: str = "", basis_note: str = "",
        settings_changes: list[str] | None = None, isolated: bool = False) -> int:
    from ..data.pipelines.structured import disclosure_metrics as pipeline
    from ..data.products import company_disclosure_metrics as product
    from ..data.runtime.repository import get_platform_structured_repository
    from ..data.stores.structured import disclosure_review
    from ..data.stores.unstructured import get_platform_unstructured_repository

    ids = [item for item in candidate_ids or [] if item]
    try:
        if isolated:
            # These writes go through the queue only; an isolated rehearsal points ATS_DATA_DB_PATH elsewhere.
            raise ValueError("disclosure_commands_do_not_take_db_or_force:set ATS_DATA_DB_PATH instead")
        if not entity:
            raise ValueError(f"{action} 需要 --entity")
        keys = [item.strip() for item in metric.split(",") if item.strip()] or None
        if action == "disclosure-extract":
            if not form and not document_id:
                raise ValueError("disclosure-extract 需要 --form 或 --document")
            settings, metrics = pipeline.issuer_metrics(entity, keys)
            documents = get_platform_unstructured_repository()
            try:
                document = pipeline.load_document(documents, entity, form=form, document_id=document_id)
            finally:
                documents.close()
            repository = _readonly() if dry_run else get_platform_structured_repository()
            try:
                if dry_run:
                    rows = pipeline.plan(repository, document, settings, metrics)
                    result = {"status": "succeeded", "dry_run": True, "form_type": document["form_type"],
                              "filing_date": document["filing_date"], "document_id": document["document_id"],
                              "model_requests_needed": sum(row["state"] == "needs_request" for row in rows),
                              "metrics": rows}
                else:
                    result = pipeline.extract_document(repository, document, settings, metrics)
            finally:
                repository.close()
        elif action in {"disclosure-candidates", "disclosure-series"}:
            repository = _readonly()
            try:
                if action == "disclosure-candidates":
                    rows = candidates(repository, entity, form=form, metric=metric, status=status)
                    if output_format == "markdown":
                        print(markdown(rows))
                        return 0
                    result = {"status": "succeeded", "count": len(rows), "candidates": rows}
                else:
                    cutoff = datetime.fromisoformat(as_of) if as_of else None
                    result = {"status": "succeeded",
                              "series": product.series(repository, entity, metric=metric, as_of=cutoff),
                              "coverage": product.coverage(repository, entity)}
            finally:
                repository.close()
        else:
            if not ids:
                raise ValueError(f"{action} 需要 --candidate")
            repository = get_platform_structured_repository()
            try:
                if action == "disclosure-correct":
                    if len(ids) != 1:
                        raise ValueError("disclosure-correct 一次只更正一个候选")
                    changes = dict(item.split("=", 1) for item in settings_changes or [] if "=" in item)
                    unknown = sorted(set(changes) - set(CORRECTABLE))
                    if unknown or len(changes) != len(settings_changes or []):
                        raise ValueError("--set 只接受 字段=值，字段限于：" + ", ".join(CORRECTABLE))
                    documents = get_platform_unstructured_repository()
                    try:
                        row = disclosure_review.correct(repository, documents, ids[0], reviewer=reviewer,
                                                        note=note, changes=changes)
                    finally:
                        documents.close()
                    result = {"status": "succeeded", "candidate_id": row["candidate_id"], "value": row["value"],
                              "period": row["period"], "reasons": json.loads(row["reason_codes_json"])}
                else:
                    done = []
                    for candidate_id in ids:
                        try:
                            if action == "disclosure-confirm":
                                row = disclosure_review.confirm(repository, candidate_id, reviewer=reviewer,
                                                                note=note, basis_note=basis_note)
                            else:
                                row = disclosure_review.reject(repository, candidate_id, reviewer=reviewer,
                                                               note=note)
                            done.append({"candidate_id": candidate_id, "status": row["status"],
                                         "observation_id": row["observation_id"]})
                        except ValueError as exc:
                            done.append({"candidate_id": candidate_id, "status": "failed", "reason_code": str(exc)})
                    failed = sum(item["status"] == "failed" for item in done)
                    result = {"status": "succeeded" if not failed else
                              "partial" if failed < len(done) else "failed", "results": done}
            finally:
                repository.close()
    except (OSError, ValueError) as exc:
        result = {"status": "failed", "reason_code": str(exc) or type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] in {"succeeded", "partial"} else 1
