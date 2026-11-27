"""Read the latest Artificial Analysis index snapshot known at a point in time.

Release dates and flagship status come from the model release registry only.
The date Artificial Analysis lists beside a model is reported under its own name.
This product is for the owner's internal reading; no model consumer reads it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from ..model_releases import load_model_releases, release_date_fields
from ..sources.artificial_analysis_indices import (
    ATTRIBUTION,
    COST_PER_TASK,
    DATASET_ID,
    INDEX_FIELDS,
    METHODOLOGY_REGIME,
    RUN_COST,
    TERMS,
)

SCALE_NOTE = ("Artificial Analysis 的合成指数（0–100）与跑完智能指数的成本。智能指数换主版本后不可比；"
              "六个能力指数单独定版，接口不返回其版本。不进百分比评测矩阵与 A/B 判定，也不供模型任务读取。")
_VARIANT = re.compile(r"\s*\(([^()]*)\)\s*$")


def _mapping(row: dict[str, Any], name: str, stored_as: str) -> dict[str, Any]:
    """A parsed field of one observation row, whether returned decoded or as stored text."""
    value = row.get(name)
    if not isinstance(value, dict):
        value = json.loads(row.get(stored_as) or "{}")
    return value


def model_indices(products, *, lab_id: str = "", as_of: datetime | None = None
                  ) -> dict[str, Any]:
    stored = products.structured.observations(
        dataset_id=DATASET_ID, as_of=as_of, latest_only=True, accepted_only=True,
        limit=2_000_000)
    packet: dict[str, Any] = {
        "dataset_id": DATASET_ID, "methodology_regime": METHODOLOGY_REGIME,
        "attribution": ATTRIBUTION, "terms": TERMS, "scale_note": SCALE_NOTE,
        "status": "no_data", "snapshot_date": "", "history_start": "", "index_version": "",
        "rows": [], "lab_best": {},
    }
    if not stored:
        return packet
    periods = sorted({str(row["period"]) for row in stored})
    registry = load_model_releases()
    metric_fields = {f"ai.aa.{field}": field
                     for field in (*INDEX_FIELDS, COST_PER_TASK, RUN_COST)}
    models: dict[str, dict[str, Any]] = {}
    versions: set[str] = set()
    for row in sorted((r for r in stored if str(r["period"]) == periods[-1]),
                      key=lambda r: str(r.get("known_at") or "")):
        field = metric_fields.get(str(row.get("metric_id") or ""))
        if field is None:
            continue
        dims = _mapping(row, "dimensions", "dimensions_json")
        raw = _mapping(row, "raw", "raw_payload")
        model_id = str(dims.get("model_id") or "")
        item = models.get(model_id)
        if item is None:
            item = models[model_id] = {
                "model_id": model_id, "lab_id": str(dims.get("lab_id") or ""),
                **{name: None for name in (*INDEX_FIELDS, COST_PER_TASK, RUN_COST)},
                "observation_ids": {}, "artifact_id": "", "known_at": ""}
        if dims.get("index_version"):
            versions.add(str(dims["index_version"]))
        # Rows are ordered by known_at, so the newest wording and value win.
        item.update(model_name=str(raw.get("model_name") or ""),
                    creator_name=str(raw.get("creator_name") or ""),
                    aa_listed_release_date=str(raw.get("aa_listed_release_date") or ""),
                    artifact_id=str(row.get("artifact_id") or ""),
                    known_at=str(row.get("known_at") or ""))
        item[field] = row.get("value")
        item["observation_ids"][field] = str(row.get("observation_id") or "")
    for item in models.values():
        name, lab = item["model_name"], item["lab_id"]
        match = _VARIANT.search(name)
        base = _VARIANT.sub("", name)
        item["variant"] = match.group(1) if match else ""
        release = registry.resolve(lab, (name, base), as_of=as_of) if lab else None
        flagship = registry.flagship(lab, as_of=as_of) if lab else None
        item.update(release_date_fields(release))
        item["flagship"] = bool(release and flagship and release.model_id == flagship.model_id)
    rows = sorted(models.values(), key=lambda item: (
        item["intelligence_index"] is None, -(item["intelligence_index"] or 0),
        item["model_name"]))
    best: dict[str, dict[str, Any]] = {}
    for item in rows:
        if item["lab_id"] and item["intelligence_index"] is not None:
            best.setdefault(item["lab_id"], item)
    if lab_id:
        rows = [item for item in rows if item["lab_id"] == lab_id]
    packet.update(status="ok", snapshot_date=periods[-1], history_start=periods[0],
                  index_version=",".join(sorted(versions)), rows=rows, lab_best=best)
    return packet
