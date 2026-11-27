"""Read the latest Epoch Capabilities Index snapshot known at a point in time.

Release dates and flagship status come from the model release registry only.
The date Epoch lists beside a model is reported under its own name.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..model_releases import load_model_releases, release_date_fields
from ..sources.epoch_capabilities_index import (
    ATTRIBUTION,
    DATASET_ID,
    LICENSE,
    METHODOLOGY_REGIME,
)

SCALE_NOTE = ("指数分，标度任意：固定点为 Claude 3.5 Sonnet = 130、GPT-5 = 150；只比较同一快照内的差距，"
              "跨快照的变化可能来自 Epoch 重新拟合。不进百分比评测矩阵与 A/B 判定。")


def _mapping(row: dict[str, Any], name: str, stored_as: str) -> dict[str, Any]:
    """A parsed field of one observation row, whether returned decoded or as stored text."""
    value = row.get(name)
    if not isinstance(value, dict):
        value = json.loads(row.get(stored_as) or "{}")
    return value


def capability_index(products, *, lab_id: str = "", as_of: datetime | None = None
                     ) -> dict[str, Any]:
    stored = products.structured.observations(
        dataset_id=DATASET_ID, as_of=as_of, latest_only=True, accepted_only=True,
        limit=2_000_000)
    packet: dict[str, Any] = {
        "dataset_id": DATASET_ID, "methodology_regime": METHODOLOGY_REGIME,
        "attribution": ATTRIBUTION, "license": LICENSE, "scale_note": SCALE_NOTE,
        "status": "no_data", "snapshot_date": "", "history_start": "", "rows": [],
        "lab_best": {},
    }
    if not stored:
        return packet
    periods = sorted({str(row["period"]) for row in stored})
    registry = load_model_releases()
    latest: dict[str, dict[str, Any]] = {}
    for row in sorted((r for r in stored if str(r["period"]) == periods[-1]),
                      key=lambda r: str(r.get("known_at") or "")):
        dims = _mapping(row, "dimensions", "dimensions_json")
        raw = _mapping(row, "raw", "raw_payload")
        name, lab = str(dims.get("model_name") or ""), str(dims.get("lab_id") or "")
        release = (registry.resolve(lab, (name, raw.get("display_name")), as_of=as_of)
                   if lab else None)
        flagship = registry.flagship(lab, as_of=as_of) if lab else None
        latest[name] = {
            "model_name": name, "organization": str(dims.get("organization") or ""),
            "lab_id": lab, "eci": row.get("value"),
            "eci_ci_low": raw.get("eci_ci_low"), "eci_ci_high": raw.get("eci_ci_high"),
            "epoch_listed_date": str(dims.get("epoch_listed_date") or ""),
            **release_date_fields(release),
            "flagship": bool(release and flagship and release.model_id == flagship.model_id),
            "observation_id": str(row.get("observation_id") or ""),
            "artifact_id": str(row.get("artifact_id") or ""),
            "known_at": str(row.get("known_at") or ""),
        }
    rows = sorted(latest.values(), key=lambda item: (-item["eci"], item["model_name"]))
    best: dict[str, dict[str, Any]] = {}
    for item in rows:
        if item["lab_id"]:
            best.setdefault(item["lab_id"], item)
    if lab_id:
        rows = [item for item in rows if item["lab_id"] == lab_id]
    packet.update(status="ok", snapshot_date=periods[-1], history_start=periods[0],
                  rows=rows, lab_best=best)
    return packet
