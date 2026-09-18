"""Governed Frontier AI raw-capability DataProduct.

The product deliberately exposes an eleven-benchmark-by-nine-Lab matrix rather than a league
table.  Scores are joined only inside an identical benchmark comparability group;
missing cells remain explicit NA states.  Heterogeneous release/model-card
evidence is retained in an event ledger and never silently substituted into the
uniform matrix. A/B evaluators are pure functions and return explanatory
evidence suitable for the L1 Observer and offline replay.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from statistics import stdev
from typing import Any, Iterable

from ..sources.frontier_ai_capability import (
    BENCHMARKS, BENCHMARK_METHODS, COHORT_VERSION, DEFAULT_FLAGSHIP_MODEL_IDS,
    DEFAULT_FLAGSHIP_MODEL_NAMES, LABS,
)

DATASET_ID = "frontier_ai_capability_benchmarks"
SOURCE_ID = "frontier_ai_capability"
METRIC_ID = "ai.frontier_capability.score"
DERIVATION_VERSION = "frontier_ai_raw_capability/v1"
CLAIM_VERSION = "v1"
THRESHOLD_PCT = 50.0
DELTA_MIN_PP = 2.0
HISTORICAL_SD_MULTIPLIER = 0.2
NA_STATES = ("not_evaluated", "pending_publication", "not_self_reported", "not_applicable",
             "non_comparable", "source_unavailable", "withdrawn")


def _dims(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("dimensions")
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(row.get("dimensions_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _raw(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("raw")
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(row.get("raw_payload") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def _chart_data(matrix: dict[str, Any], a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic tables consumed by every raw-capability chart.

    Keeping these small projections in the snapshot metadata means a reviewer
    can reproduce the exact visual inputs without re-reading a source or
    relying on a renderer's implementation details.
    """
    a_rows = list(a.get("benchmarks") or [])
    b_rows = list(b.get("benchmarks") or [])
    return {
        "coverage_heatmap": list(matrix.get("matrix") or []),
        "frontier_history": list(matrix.get("candidate_observations") or []),
        "event_ledger": list(matrix.get("event_observations") or []),
        "threshold_margin": [row for row in b_rows if row.get("status") != "not_applicable" and row.get("current")],
        "frontier_delta": [row for row in a_rows if row.get("current") and row.get("previous")],
    }


def _rows(products, *, as_of=None, include_vintages: bool = False) -> list[dict[str, Any]]:
    # Third-party AA scores and official Lab self-reports share one governed
    # dataset but retain distinct source identities for source-priority logic.
    return products.structured.observations(dataset_id=DATASET_ID, source_id=None,
                                            as_of=as_of, latest_only=not include_vintages,
                                            accepted_only=True, limit=2_000_000)


def _score_record(row: dict[str, Any]) -> dict[str, Any]:
    dims = _dims(row)
    lab_id = str(dims.get("lab_id") or "")
    model_release_id = str(dims.get("model_release_id") or dims.get("model_id") or "")
    configured_release_id = DEFAULT_FLAGSHIP_MODEL_IDS.get(lab_id, "")
    return {
        "observation_id": str(row.get("observation_id") or ""), "artifact_id": str(row.get("artifact_id") or ""),
        "lab_id": lab_id, "lab": str(dims.get("lab_label") or dims.get("lab_id") or ""),
        "model_id": str(dims.get("model_id") or ""), "model_name": str(dims.get("model_name") or dims.get("model_id") or ""),
        "model_release_id": model_release_id,
        "model_release_name": (DEFAULT_FLAGSHIP_MODEL_NAMES.get(lab_id, model_release_id)
                               if model_release_id == configured_release_id else
                               str(dims.get("model_release_name") or dims.get("model_name") or model_release_id)),
        "evaluation_variant_id": str(dims.get("evaluation_variant_id") or dims.get("model_id") or ""),
        "model_lineage": str(dims.get("model_lineage") or dims.get("model_id") or ""),
        "release_date": str(dims.get("release_date") or ""),
        "available": bool(dims.get("available", True)),
        "flagship": bool(dims.get("flagship", True)),
        "flagship_source": str(dims.get("flagship_source") or ""),
        "benchmark_id": str(dims.get("benchmark_id") or ""),
        "benchmark": str(dims.get("benchmark_label") or dims.get("benchmark_id") or ""),
        "score": float(row.get("value") or 0.0), "score_unit": "percent",
        "method_version": str(dims.get("method_version") or ""),
        "comparability_group": str(dims.get("comparability_group") or ""),
        "harness": str(dims.get("harness") or ""), "grader": str(dims.get("grader") or ""),
        "verified_status": dims.get("verified_status"),
        "metric_semantic": str(dims.get("metric_semantic") or dims.get("score_semantics") or ""),
        "inference_config": str(dims.get("inference_config") or ""),
        "reasoning_effort": str(dims.get("reasoning_effort") or ""),
        "source_type": str(dims.get("source_type") or "third_party_evaluation"),
        "source_priority": str(dims.get("source_priority") or ""),
        "measurement_scope": str(dims.get("measurement_scope") or "model_capability_proxy"),
        "source_transport": str(dims.get("source_transport") or "unknown"),
        "source_url": str(dims.get("source_url") or ""),
        "details_url": str(dims.get("details_url") or ""),
        "b_eligible": bool(dims.get("b_eligible", False)),
        "coverage_state": str(dims.get("coverage_state") or "observed"),
        "event_only": bool(dims.get("event_only", False)),
        "uniform_matrix": bool(dims.get("uniform_matrix", not dims.get("event_only", False))),
        "sample_size": dims.get("sample_size"), "confidence_low": dims.get("confidence_low"),
        "confidence_high": dims.get("confidence_high"), "score_as_of": str(dims.get("score_as_of") or row.get("period") or ""),
        "period": str(row.get("period") or ""), "known_at": str(row.get("known_at") or ""),
        "source_id": str(row.get("source_id") or SOURCE_ID),
        "quality_status": str(row.get("quality_status") or "accepted"),
        "confidence_unavailable": dims.get("confidence_low") is None or dims.get("confidence_high") is None,
    }


def benchmark_catalog() -> list[dict[str, Any]]:
    return [{"benchmark_id": key, **dict(value), "b_eligible": bool(value.get("b_eligible", False))}
            for key, value in BENCHMARK_METHODS.items()]


def frontier_diagnostics(matrix: dict[str, Any]) -> dict[str, Any]:
    """Compare the fixed nine-Lab panel frontier with the governed global frontier.

    The panel is a stable presentation surface; global candidates may include
    additional evaluated models. The difference is reported, never silently
    substituted or averaged.
    """
    panel_rows = [cell for cell in matrix.get("matrix") or [] if cell.get("score") is not None]
    global_rows = [row for row in matrix.get("candidate_observations") or [] if row.get("uniform_matrix", True)]
    panel, global_frontier, divergence = {}, {}, {}
    for benchmark in BENCHMARKS:
        p = _frontier(panel_rows, benchmark)
        g = _frontier(global_rows, benchmark)
        panel[benchmark] = p
        global_frontier[benchmark] = g
        if p and g:
            divergence[benchmark] = {
                "score_gap_pp": round(float(g["score"]) - float(p["score"]), 2),
                "panel_model_id": p.get("model_id"), "global_model_id": g.get("model_id"),
                "same_frontier": p.get("observation_id") == g.get("observation_id"),
            }
        else:
            divergence[benchmark] = {"score_gap_pp": None, "same_frontier": False,
                                      "reason": "panel_or_global_frontier_unavailable"}
    return {"panel": panel, "global": global_frontier, "divergence": divergence,
            "research_frontier": "global"}


def _model_catalog(records: Iterable[dict[str, Any]], entities: Iterable[dict[str, Any]] = ()) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        key = (record["lab_id"], record["model_id"])
        out["%s:%s" % key] = {"lab_id": record["lab_id"], "lab": record["lab"],
                               "model_id": record["model_id"], "model_name": record["model_name"],
                               "model_release_id": record.get("model_release_id") or record["model_id"],
                               "model_release_name": record.get("model_release_name") or record["model_name"],
                               "model_lineage": record["model_lineage"],
                               "release_date": record.get("release_date", ""),
                               "available": bool(record.get("available", True)),
                               "flagship": bool(record.get("flagship", True)),
                               "flagship_source": record.get("flagship_source", "")}
    for entity in entities:
        if str(entity.get("kind") or "") != "frontier_model":
            continue
        try:
            meta = json.loads(entity.get("metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            meta = {}
        lab = str(meta.get("lab_id") or "")
        model_id = str(meta.get("model_id") or "")
        if lab and model_id:
            out.setdefault(f"{lab}:{model_id}", {"lab_id": lab, "lab": dict(LABS).get(lab, lab),
                                                   "model_id": model_id,
                                                   "model_release_id": str(meta.get("model_release_id") or model_id),
                                                   "model_name": str(meta.get("display_name") or entity.get("canonical_name") or model_id),
                                                   "model_lineage": str(meta.get("lineage") or model_id),
                                                   "available": bool(meta.get("available", True)),
                                                   "flagship": bool(meta.get("flagship", True)),
                                                   "flagship_source": str(meta.get("flagship_source") or "")})
    return out


def capability_matrix(products, *, as_of=None, include_vintages: bool = False) -> dict[str, Any]:
    rows = [_score_record(row) for row in _rows(products, as_of=as_of, include_vintages=include_vintages)]
    # Keep only the latest observation per model/benchmark/method when callers
    # requested all vintages; lineage remains available in candidate_observations.
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    priority = {"third_party_evaluation": 3, "competitor_reported": 2, "lab_self_reported": 1}
    for record in sorted(rows, key=lambda item: (item["score_as_of"], item["known_at"],
                                                 priority.get(item.get("source_type", ""), 0))):
        if not record.get("uniform_matrix", True):
            continue
        key = (record["lab_id"], record["model_id"], record["benchmark_id"])
        incumbent = latest.get(key)
        if incumbent is None or (priority.get(record.get("source_type", ""), 0), record["known_at"]) >= (
                priority.get(incumbent.get("source_type", ""), 0), incumbent["known_at"]):
            latest[key] = record
    records = list(latest.values())
    try:
        entities = products.structured.entities()
    except AttributeError:
        entities = []
    models = _model_catalog(records, entities)
    coverage_meta: dict[tuple[str, str, str], dict[str, Any]] = {}
    try:
        for item in products.structured.frontier_capability_coverage():
            try:
                payload = json.loads(item.get("metadata_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            coverage_meta[(str(item.get("lab_id")), str(item.get("model_id")),
                           str(item.get("benchmark_id")))] = {**item, **payload}
    except AttributeError:
        pass
    # A configured nine-lab panel is always rendered even if one source is empty.
    by_lab: dict[str, dict[str, Any]] = {}
    for lab_id, lab_label in LABS:
        lab_models = [item for item in models.values() if item["lab_id"] == lab_id
                      and item.get("available", True)]
        # An official release discovery or explicit override may advance the
        # active release without a code change. The configured registry is a
        # truthful fallback when the official-release source has no coverage;
        # benchmark publication dates never select the flagship by themselves.
        configured_id = DEFAULT_FLAGSHIP_MODEL_IDS.get(lab_id, "")
        current = {"lab_id": lab_id, "lab": lab_label,
                   "model_id": configured_id, "model_release_id": configured_id,
                   "model_name": DEFAULT_FLAGSHIP_MODEL_NAMES.get(lab_id, configured_id),
                   "model_lineage": configured_id, "available": True,
                   "flagship": True, "flagship_source": "configured_cohort"}
        source_rank = {"official_lab_release": 3, "operator_override": 3,
                       "official_release": 3, "configured_registry": 2,
                       "configured_cohort": 1}
        active_models = [item for item in lab_models if item.get("flagship", False)
                         and source_rank.get(str(item.get("flagship_source") or ""), 0) > 0]
        if active_models:
            representative = max(active_models, key=lambda item: (
                source_rank.get(str(item.get("flagship_source") or ""), 0),
                item.get("release_date") or "", item.get("model_release_id") or item["model_id"]))
            release_id = str(representative.get("model_release_id") or representative["model_id"])
            current.update(representative)
            current["model_release_id"] = release_id
            current["model_id"] = release_id
            current["model_name"] = (DEFAULT_FLAGSHIP_MODEL_NAMES.get(lab_id, release_id)
                                     if release_id == configured_id else
                                     str(representative.get("model_release_name")
                                         or representative.get("model_name") or release_id))
        active_release_id = str(current.get("model_release_id") or configured_id)
        cells = []
        for benchmark in BENCHMARKS:
            candidates = [item for item in records
                          if item["lab_id"] == lab_id
                          and item["benchmark_id"] == benchmark
                          and item.get("model_release_id") == active_release_id]
            # Source class is the first-order selector. Within the same source
            # class, raw capability uses the highest published configuration;
            # its exact effort and harness remain attached to the selected cell.
            record = max(
                candidates,
                key=lambda item: (priority.get(item.get("source_type", ""), 0),
                                  item["score"], item["score_as_of"], item["known_at"], item["model_id"]),
                default=None,
            )
            if record is None:
                metadata = coverage_meta.get((lab_id, current["model_id"], benchmark), {}) if current else {}
                state = str(metadata.get("coverage_state") or "not_evaluated")
                cells.append({"lab_id": lab_id, "lab": lab_label, "model_id": current["model_id"] if current else "",
                              "model_name": current["model_name"] if current else "", "benchmark_id": benchmark,
                              "benchmark": BENCHMARK_METHODS[benchmark]["label"], "score": None,
                              "display": "NA", "coverage_state": state if state in NA_STATES else "not_evaluated",
                              "source_type": str(metadata.get("source_type") or ""),
                              "method_version": str(metadata.get("method_version") or ""),
                              "comparability_group": str(metadata.get("comparability_group") or "")})
            else:
                cells.append({**record, "display": round(record["score"], 2),
                              "selection_reason": "highest_observed_configuration_for_exact_release;" + {
                                  "third_party_evaluation": "maintainer_or_independent_third_party_preferred",
                                  "competitor_reported": "competitor_reported_fallback",
                                  "lab_self_reported": "lab_self_reported_fallback",
                              }.get(record.get("source_type"), "source_priority_fallback")})
        by_lab[lab_id] = {"lab_id": lab_id, "lab": lab_label,
                          "model": current, "cells": cells,
                          "covered": sum(cell["score"] is not None for cell in cells),
                          "coverage_ratio": sum(cell["score"] is not None for cell in cells) / len(BENCHMARKS)}
    matrix_rows = [cell for lab in by_lab.values() for cell in lab["cells"]]
    numeric_count = sum(cell.get("score") is not None for cell in matrix_rows)
    coverage_counts = {state: sum(cell.get("coverage_state") == state for cell in matrix_rows)
                       for state in NA_STATES}
    result = {"status": "ok" if records else "no_coverage", "dataset_id": DATASET_ID,
            "source_id": SOURCE_ID, "cohort_version": COHORT_VERSION,
            "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
            "labs": [{"lab_id": key, "label": label} for key, label in LABS],
            "benchmarks": benchmark_catalog(), "by_lab": by_lab,
            "matrix": matrix_rows, "coverage": {"numeric": numeric_count, "total": len(LABS) * len(BENCHMARKS),
                                                  "ratio": numeric_count / (len(LABS) * len(BENCHMARKS)),
                                                  "states": coverage_counts},
            "candidate_observations": rows,
            "event_observations": [row for row in rows if not row.get("uniform_matrix", True)],
            "rows_hash": _hash(matrix_rows),
            "lineage": {"derivation_version": DERIVATION_VERSION,
                        "observation_ids": sorted({r["observation_id"] for r in rows if r["observation_id"]}),
                        "artifact_ids": sorted({r["artifact_id"] for r in rows if r["artifact_id"]})}}
    result["frontier_diagnostics"] = frontier_diagnostics(result)
    return result


def _frontier(records: list[dict[str, Any]], benchmark: str) -> dict[str, Any] | None:
    candidates = [r for r in records if r["benchmark_id"] == benchmark and r["score"] is not None
                  and r.get("uniform_matrix", True)]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r["score"], r["score_as_of"], r["model_id"]))


def _historical_previous_frontier(records: list[dict[str, Any]], benchmark: str,
                                  current: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build the preceding cumulative frontier from published score dates.

    A single current scrape often contains several model generations with their
    original score dates.  That is valid history and should not be discarded
    merely because the Observer has only one local ingestion vintage.
    """
    if current is None:
        return None
    group = current.get("comparability_group")
    candidates = [row for row in records
                  if row.get("benchmark_id") == benchmark and row.get("score") is not None
                  and row.get("uniform_matrix", True)
                  and row.get("comparability_group") == group and row.get("score_as_of")]
    current_date = str(current.get("score_as_of") or "")
    if not current_date:
        return None
    previous_candidates = [row for row in candidates if str(row["score_as_of"]) < current_date]
    if not previous_candidates:
        return None
    return max(previous_candidates, key=lambda row: (row["score"], row["score_as_of"], row["model_id"]))


def evaluate_a(matrix: dict[str, Any], *, previous_matrix: dict[str, Any] | None = None,
               historical_frontier_scores: dict[str, list[float]] | None = None) -> dict[str, Any]:
    """Evaluate whether each benchmark's comparable global frontier expanded."""
    current_records = list(matrix.get("candidate_observations") or [])
    previous_records = list((previous_matrix or {}).get("candidate_observations") or [])
    output = []
    for benchmark in BENCHMARKS:
        current = _frontier(current_records, benchmark)
        previous = (_frontier(previous_records, benchmark) if previous_records
                    else _historical_previous_frontier(current_records, benchmark, current))
        if current is None or previous is None:
            status = "insufficient_history"
            reason = "缺少同一可比组的 current/previous global frontier 基线。"
            output.append({"benchmark_id": benchmark, "status": status, "current": current,
                           "previous": previous, "reason": reason})
            continue
        if current["comparability_group"] != previous["comparability_group"]:
            output.append({"benchmark_id": benchmark, "status": "non_comparable_version_change",
                           "current": current, "previous": previous,
                           "reason": "methodology comparability group changed; no bridge registered."})
            continue
        delta = current["score"] - previous["score"]
        history = list((historical_frontier_scores or {}).get(benchmark) or [])
        sigma = stdev(history) if len(history) >= 2 else 0.0
        delta_min = max(DELTA_MIN_PP, HISTORICAL_SD_MULTIPLIER * sigma)
        low = current.get("confidence_low")
        previous_high = previous.get("confidence_high")
        lcb_delta = (float(low) - float(previous_high)) if low is not None and previous_high is not None else None
        if lcb_delta is not None and lcb_delta > delta_min:
            status = "confirmed_expansion"
        elif delta > delta_min:
            status = "provisional_expansion"
        elif delta < -delta_min:
            status = "contracting"
        else:
            status = "stable"
        output.append({"benchmark_id": benchmark, "status": status, "current": current, "previous": previous,
                       "delta_pp": round(delta, 2), "delta_min_pp": round(delta_min, 2),
                       "lcb_delta_pp": lcb_delta, "reason": "同一 comparability group 内的 global frontier 比较。"})
    statuses = {item["status"] for item in output}
    overall = "expanding" if any(item["status"] in {"confirmed_expansion", "provisional_expansion"} for item in output) else (
        "contracting" if "contracting" in statuses else (
            "stable" if "stable" in statuses else "insufficient_history"))
    return {"rule_version": "raw_capability_a/v1", "status": overall, "benchmarks": output}


def _threshold_level(current: dict[str, Any], *, level_id: str, label: str,
                     threshold_pct: float) -> dict[str, Any]:
    low = current.get("confidence_low")
    point_crossed = float(current["score"]) > threshold_pct
    confirmed = low is not None and float(low) > threshold_pct
    return {"level_id": level_id, "label": label, "available": True,
            "threshold_pct": threshold_pct, "score": current["score"],
            "confidence_low": low,
            "status": "confirmed_crossing" if confirmed else (
                "provisional_crossing" if point_crossed else "not_crossed")}


def evaluate_b(matrix: dict[str, Any], *, threshold_pct: float = THRESHOLD_PCT) -> dict[str, Any]:
    current_records = list(matrix.get("candidate_observations") or [])
    output = []
    for benchmark in BENCHMARKS:
        current = _frontier(current_records, benchmark)
        method = BENCHMARK_METHODS[benchmark]
        eligible = bool(method.get("b_eligible")) or bool((current or {}).get("b_eligible"))
        if not eligible:
            output.append({"benchmark_id": benchmark, "status": "not_applicable", "current": current,
                           "levels": [], "reason": "该 benchmark 的评分语义不支持完整任务解锁判断。"})
            continue
        if current is None:
            output.append({"benchmark_id": benchmark, "status": "not_evaluated", "current": None,
                           "levels": []})
            continue
        levels = [_threshold_level(
            current, level_id="majority_task_unlock",
            label="多数任务解锁（95% 置信下界 > 50%）", threshold_pct=threshold_pct)]
        human_threshold = method.get("human_baseline_threshold_pct")
        if human_threshold is not None:
            levels.append(_threshold_level(
                current, level_id="human_baseline",
                label=str(method.get("human_baseline_label") or "人类基准门槛"),
                threshold_pct=float(human_threshold)))
        economic_threshold = method.get("economic_usability_threshold_pct")
        if economic_threshold is not None:
            levels.append(_threshold_level(
                current, level_id="economic_usability",
                label=str(method.get("economic_usability_label") or "经济可用门槛"),
                threshold_pct=float(economic_threshold)))
        status = levels[-1]["status"]
        output.append({"benchmark_id": benchmark, "status": status, "current": current,
                       "levels": levels, "highest_defined_level": levels[-1]["level_id"],
                       "reason": "只判断预先定义且可观测的门槛；95% 置信下界超过门槛才确认，点估计超过仅暂定。"})
    crossed = [x for x in output if x["status"] in {"confirmed_crossing", "provisional_crossing"}]
    return {"rule_version": "raw_capability_b/v2",
            "framework": ["majority_task_unlock", "human_baseline", "economic_usability"],
            "status": "crossed" if crossed else "not_crossed", "benchmarks": output}


def evidence_bundle(products, *, as_of=None, previous_matrix: dict[str, Any] | None = None,
                    snapshot_consumer: str = "", snapshot_purpose: str = "") -> dict[str, Any]:
    matrix = capability_matrix(products, as_of=as_of)
    a = evaluate_a(matrix, previous_matrix=previous_matrix)
    b = evaluate_b(matrix)
    manifest = None
    if hasattr(products, "snapshot_manifest"):
        effective_as_of = as_of
        if effective_as_of is None:
            effective_as_of = datetime.now(timezone.utc)
        # The manifest references the exact accepted observations; metadata
        # carries the derived packet hashes and rule versions for offline audit.
        manifest = products.snapshot_manifest(
            consumer=snapshot_consumer or "evidence_observer",
            purpose=snapshot_purpose or "ai_frontier_raw_capability:v1",
            as_of=effective_as_of,
            rows=[dict(row, selected_source=row.get("source_id"),
                       selection_reason="frontier_candidate")
                  for row in matrix.get("candidate_observations") or []],
            metadata={"dataset_id": DATASET_ID, "rows_hash": matrix["rows_hash"],
                      "claim_id": "ai_frontier_raw_capability",
                      "derivation_version": DERIVATION_VERSION,
                      "a_rule_version": a.get("rule_version"),
                      "b_rule_version": b.get("rule_version"),
                      "a_status": a.get("status"), "b_status": b.get("status"),
                      "matrix_coverage": matrix.get("coverage"),
                      # Persist the deterministic derived payload as part of
                      # the immutable manifest.  This is intentionally
                      # separate from the observation references: a later
                      # code change must not silently alter an old report's
                      # matrix or A/B decision during replay.
                      "derived": {
                          "matrix": matrix,
                          "a": a,
                          "b": b,
                          "chart_data": _chart_data(matrix, a, b),
                      }})
    return {"status": "ok" if matrix["status"] == "ok" else "unavailable", "claim_id": "ai_frontier_raw_capability",
            "claim_definition_version": CLAIM_VERSION,
            "claim_text": "前沿生成式 AI 的原始能力边界是否持续外扩，并出现过去模型无法跨越的新任务门槛？",
            "matrix": matrix, "a": a, "b": b, "coverage": matrix["coverage"],
            "frontier_diagnostics": matrix.get("frontier_diagnostics"),
            "manifest": manifest, "snapshot_manifest": manifest,
            "freshness": {"latest_score_as_of": max((r["score_as_of"] for r in matrix["candidate_observations"]), default=""),
                          "source_id": SOURCE_ID},
            "rows_hash": matrix["rows_hash"], "lineage": matrix["lineage"],
            "snapshot_consumer": snapshot_consumer, "snapshot_purpose": snapshot_purpose,
            "limitations": ["benchmark 是 raw capability 的量化 proxy，不是综合智能排行榜。",
                            "NA 表示没有可用观测，不表示零分；第三方、自报告和不可比结果分开。",
                            "未预定义人类或经济门槛的 benchmark 不生成相应 B 栏目。"]}


def frontier_ai_capability_matrix(products, **kwargs):
    return capability_matrix(products, **kwargs)


def frontier_ai_capability_evidence_bundle(products, **kwargs):
    return evidence_bundle(products, **kwargs)


def replay_frontier_ai_capability_snapshot(products, snapshot_id: str) -> dict[str, Any] | None:
    """Return an immutable raw-capability snapshot for offline verification.

    The manifest contains both observation references and the exact derived
    packet used at publication time.  Returning the latter makes replay stable
    across future selector/renderer code changes while still exposing the raw
    rows for independent audit.
    """
    if not hasattr(products, "replay_snapshot"):
        return None
    replayed = products.replay_snapshot(snapshot_id)
    if replayed is None:
        return None
    metadata = replayed.get("metadata") or {}
    derived = metadata.get("derived") or {}
    matrix = derived.get("matrix") if isinstance(derived, dict) else None
    a = derived.get("a") if isinstance(derived, dict) else None
    b = derived.get("b") if isinstance(derived, dict) else None
    chart_data = derived.get("chart_data") if isinstance(derived, dict) else None
    return {"snapshot_id": snapshot_id, "manifest": replayed,
            "rows": replayed.get("rows") or [],
            "rows_hash": metadata.get("rows_hash", ""),
            "rule_versions": {"a": metadata.get("a_rule_version", ""),
                              "b": metadata.get("b_rule_version", "")},
            "matrix": matrix,
            "a": a,
            "b": b,
            "chart_data": chart_data,
            "derived_payload_present": bool(matrix is not None and a is not None and b is not None),
            "replayable_without_network": True}


def query_scores(products, *, lab_id: str = "", model_id: str = "", benchmark_id: str = "",
                 method_version: str = "", source_type: str = "", as_of=None,
                 comparability_group: str = "", harness: str = "", grader: str = "",
                 inference_config: str = "", reasoning_effort: str = "", measurement_scope: str = "",
                 include_vintages: bool = True) -> list[dict[str, Any]]:
    """Return immutable score vintages with explicit comparability filters."""
    rows = [_score_record(row) for row in _rows(products, as_of=as_of, include_vintages=include_vintages)]
    predicates = (("lab_id", lab_id), ("model_id", model_id), ("benchmark_id", benchmark_id),
                  ("method_version", method_version), ("source_type", source_type),
                  ("comparability_group", comparability_group), ("harness", harness),
                  ("grader", grader), ("inference_config", inference_config),
                  ("reasoning_effort", reasoning_effort), ("measurement_scope", measurement_scope))
    return [row for row in rows if all(not value or str(row.get(key)) == str(value)
                                       for key, value in predicates)]


def frontier_ai_capability_scores(products, **kwargs):
    return query_scores(products, **kwargs)


def event_ledger(products, *, benchmark_id: str = "", model_id: str = "", lab_id: str = "",
                 as_of=None, include_vintages: bool = True) -> list[dict[str, Any]]:
    """Return heterogeneous event evidence without mixing it into the matrix.

    An event keeps its task-set, harness, grader, metric semantic and source
    identity.  Consumers may filter a single benchmark/model, but there is no
    aggregate or ranking operation here by design.
    """
    rows = [_score_record(row) for row in _rows(products, as_of=as_of, include_vintages=include_vintages)]
    return [row for row in rows
            if row.get("event_only")
            and (not benchmark_id or row.get("benchmark_id") == benchmark_id)
            and (not model_id or row.get("model_id") == model_id)
            and (not lab_id or row.get("lab_id") == lab_id)]


def affected_benchmarks_from_events(events: Iterable[dict[str, Any] | str]) -> list[str]:
    """Map typed discovery events to the smallest deterministic recompute scope."""
    affected: set[str] = set()
    all_events = {"model_added", "model_withdrawn", "score_added", "score_revised",
                  "method_changed", "flagship_changed", "a_state_changed", "b_state_changed",
                  "score_added_or_revised", "model_directory_checked"}
    for event in events:
        if isinstance(event, dict):
            name = str(event.get("event_type") or event.get("type") or "")
            benchmark = str(event.get("benchmark_id") or "")
        else:
            name, benchmark = str(event), ""
        if benchmark in BENCHMARKS:
            affected.add(benchmark)
        elif name in all_events:
            affected.update(BENCHMARKS)
    return [benchmark for benchmark in BENCHMARKS if benchmark in affected]


def recompute_frontier_ai_capability_for_events(products, events: Iterable[dict[str, Any] | str], **kwargs) -> dict[str, Any]:
    """Recompute the packet once for an event batch and disclose its scope."""
    impacted = affected_benchmarks_from_events(events)
    packet = evidence_bundle(products, **kwargs)
    packet["recomputed_for"] = impacted
    packet["unaffected_benchmarks_stable"] = [benchmark for benchmark in BENCHMARKS if benchmark not in impacted]
    return packet
