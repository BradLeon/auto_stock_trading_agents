"""Charts for the raw-capability Observer.

Every chart is generated from the matrix packet and carries the same rows hash;
no visual is allowed to fetch or transform a second source silently.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .chart_font import configure_font, glyph_report

RENDERER_VERSION = "raw_capability_renderer/v1"


def _hash(rows: Any) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def _write_table(output: Path, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    jp, cp = output / f"{name}.json", output / f"{name}.csv"
    jp.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2, default=str), encoding="utf-8")
    keys = sorted({k for row in rows for k in row})
    with cp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys); writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v
                             for k, v in row.items()})
    return {"table_name": name, "json_path": str(jp), "csv_path": str(cp),
            "rows_hash": _hash(rows), "row_count": len(rows)}


def _sidecar(path: Path, *, title: str, definition: str, rows: list[dict[str, Any]], packet: dict[str, Any], font_path: str = "") -> dict[str, Any]:
    body = {"title": title, "metric_definition": definition, "chart_slug": path.stem,
            # ``rows_hash`` is the accepted matrix hash shared by the report;
            # retain the chart-table hash separately for audit of the render
            # input (A/B tables are intentional projections of the same packet).
            "rows_hash": packet.get("rows_hash") or _hash(rows),
            "input_rows_hash": _hash(rows),
            "observation_ids": sorted({str(r.get("observation_id")) for r in rows if r.get("observation_id")}),
            "artifact_ids": sorted({str(r.get("artifact_id")) for r in rows if r.get("artifact_id")}),
            "claim_id": packet.get("claim_id"), "claim_definition_version": packet.get("claim_definition_version"),
            "cohort_version": (packet.get("matrix") or {}).get("cohort_version"),
            "renderer_version": RENDERER_VERSION, "font_path": font_path,
            "glyph_check": glyph_report(title, font_path=font_path)}
    sp = path.with_name(path.stem + ".sidecar.json")
    sp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2, default=str), encoding="utf-8")
    return {"png_path": str(path), "sidecar_path": str(sp), **body}


def render_raw_capability_charts(*, packet: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    matrix = packet.get("matrix") or {}
    cells = list(matrix.get("matrix") or [])
    a_rows = list((packet.get("a") or {}).get("benchmarks") or [])
    b_rows = list((packet.get("b") or {}).get("benchmarks") or [])
    tables = [_write_table(output, "frontier_ai_capability_matrix", cells),
              _write_table(output, "frontier_ai_capability_a", a_rows),
              _write_table(output, "frontier_ai_capability_b", b_rows)]
    descriptors: list[dict[str, Any]] = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        font_path = configure_font(plt) or ""
        labs = [str(item["lab_id"]) for item in matrix.get("labs") or []]
        labels = [str(item["label"]) for item in matrix.get("benchmarks") or []]
        values = np.full((len(labs), len(labels)), np.nan)
        lookup = {(str(c.get("lab_id")), str(c.get("benchmark_id"))): c for c in cells}
        for i, lab in enumerate(labs):
            for j, benchmark in enumerate([str(item["benchmark_id"]) for item in matrix.get("benchmarks") or []]):
                cell = lookup.get((lab, benchmark)) or {}
                if cell.get("score") is not None:
                    values[i, j] = float(cell["score"])
        fig, ax = plt.subplots(figsize=(14, 6), dpi=150)
        cmap = plt.cm.viridis.copy(); cmap.set_bad(color="#e9e9e9")
        im = ax.imshow(values, aspect="auto", cmap=cmap, vmin=0, vmax=100)
        ax.set_xticks(range(len(labels)), labels, rotation=35, ha="right", fontsize=8)
        ax.set_yticks(range(len(labs)), [str(item["label"]) for item in matrix.get("labs") or []], fontsize=9)
        for i in range(len(labs)):
            for j in range(len(labels)):
                cell = lookup.get((labs[i], str((matrix.get("benchmarks") or [])[j]["benchmark_id"]))) or {}
                marker = "*" if cell.get("source_type") == "lab_self_reported" else "†" if cell.get("coverage_state") == "non_comparable" else ""
                txt = "NA" if cell.get("score") is None else f"{float(cell['score']):.1f}{marker}"
                ax.text(j, i, txt, ha="center", va="center", fontsize=7,
                        color="black" if cell.get("score") is None or float(cell.get("score", 50)) < 65 else "white")
                if cell.get("source_type") == "lab_self_reported":
                    ax.add_patch(plt.Rectangle((j - .48, i - .48), .96, .96,
                                               fill=False, edgecolor="#f28e2b", linewidth=1.4))
                elif cell.get("coverage_state") == "non_comparable":
                    ax.add_patch(plt.Rectangle((j - .48, i - .48), .96, .96,
                                               fill=False, edgecolor="#d62728", linewidth=1.4, linestyle="--"))
        fig.colorbar(im, ax=ax, label="分数（0–100%）")
        ax.set_title("Frontier AI 原始能力：九家 Labs × 十一项 benchmark（NA 不代表零分）")
        fig.tight_layout(); p = output / "frontier_ai_capability_coverage_heatmap.png"; fig.savefig(p); plt.close(fig)
        descriptors.append(_sidecar(p, title="九家 Labs × 十一项 benchmark 能力矩阵",
                                    definition=("当前旗舰模型精确 release 在各 benchmark 的分数；同一 release 的多个公开配置只展示最高分，"
                                                "原始 effort/harness 保留在 sidecar 数据中；灰色 NA 为没有可用观测。"),
                                    rows=cells, packet=packet, font_path=font_path))

        event_rows = list(matrix.get("event_observations") or [])
        if event_rows:
            # Event evidence is intentionally a separate compact view. It is
            # not a ranking and is never mixed into the uniform heatmap.
            fig, ax = plt.subplots(figsize=(13, 5), dpi=150)
            labels_event = [f"{row.get('model_name', row.get('model_id', 'NA'))}\n{row.get('benchmark', row.get('benchmark_id', 'NA'))}" for row in event_rows]
            vals_event = [float(row.get("score")) if row.get("score") is not None else float("nan") for row in event_rows]
            ax.bar(range(len(vals_event)), vals_event, color="#7b61a8")
            ax.set_ylim(0, 100); ax.set_ylabel("分数（0–100%）"); ax.set_xlabel("事件证据（不作统一横向排名）")
            ax.set_xticks(range(len(labels_event)), labels_event, rotation=45, ha="right", fontsize=7)
            ax.set_title("事件型评测证据（与统一矩阵分离）"); ax.grid(axis="y", alpha=.25)
            fig.tight_layout(); p = output / "frontier_ai_capability_event_ledger.png"; fig.savefig(p); plt.close(fig)
            descriptors.append(_sidecar(p, title="事件型评测证据账本",
                                        definition="不同 task set、harness、grader 或评分语义的真实结果，仅作为事件证据，不混入统一矩阵。",
                                        rows=event_rows, packet=packet, font_path=font_path))

        # Comparable frontier history.  Each benchmark is a separate line and
        # only points sharing a comparability group are connected.  A one-point
        # slice is still useful as a baseline and is never extended by
        # interpolation.
        history = list(matrix.get("candidate_observations") or [])
        if history:
            fig, ax = plt.subplots(figsize=(13, 6), dpi=150)
            for benchmark in [str(item["benchmark_id"]) for item in matrix.get("benchmarks") or []]:
                series = [row for row in history if row.get("benchmark_id") == benchmark]
                groups: dict[str, list[dict[str, Any]]] = {}
                for row in series:
                    groups.setdefault(str(row.get("comparability_group") or "unknown"), []).append(row)
                label = next((str(item["label"]) for item in matrix.get("benchmarks") or [] if item["benchmark_id"] == benchmark), benchmark)
                for group, points in groups.items():
                    points = sorted(points, key=lambda item: (str(item.get("score_as_of") or ""), str(item.get("model_id") or "")))
                    # Reduce each period/group to the global frontier; model
                    # labels identify the winning point without ranking vendors.
                    by_period: dict[str, dict[str, Any]] = {}
                    for point in points:
                        period = str(point.get("score_as_of") or point.get("period") or "")
                        if period not in by_period or float(point["score"]) > float(by_period[period]["score"]):
                            by_period[period] = point
                    chosen = list(by_period.values())
                    ax.plot([str(p.get("score_as_of") or p.get("period")) for p in chosen], [float(p["score"]) for p in chosen], marker="o", linewidth=1.8, label=f"{label} · {group}")
                    for point in chosen:
                        ax.annotate(str(point.get("model_name") or point.get("model_id") or ""), (str(point.get("score_as_of") or point.get("period")), float(point["score"])), fontsize=6, xytext=(0, 4), textcoords="offset points", ha="center")
            ax.set_ylim(0, 100); ax.set_ylabel("global frontier score（0–100%）"); ax.set_xlabel("score-as-of（方法换版时断线）"); ax.set_title("Comparable global frontier history")
            # Sparse ticks keep the report readable when the source publishes
            # weekly or irregular score updates.
            tick_positions = list(range(0, len(sorted({str(p.get("score_as_of") or p.get("period")) for p in history})), max(1, len(history) // 8)))
            if tick_positions:
                ax.set_xticks(tick_positions)
            ax.tick_params(axis="x", rotation=35, labelsize=8); ax.grid(alpha=.25, linestyle=":"); ax.legend(fontsize=6, ncol=2)
            fig.tight_layout(); p = output / "frontier_ai_capability_frontier_history.png"; fig.savefig(p); plt.close(fig)
            descriptors.append(_sidecar(p, title="可比 global frontier 历史", definition="按 benchmark 与 comparability group 分面选择各时点最高分；方法换版分组，不跨组连线。", rows=history, packet=packet, font_path=font_path))

        # B threshold-level chart. Non-applicable benchmarks are omitted. The
        # chart shows observed scores against predefined levels rather than a
        # decontextualized "score minus 50" margin.
        eligible = [r for r in b_rows if r.get("status") != "not_applicable" and r.get("current")]
        if eligible:
            names = [next((str(m["label"]) for m in matrix.get("benchmarks") or [] if m["benchmark_id"] == r["benchmark_id"]), r["benchmark_id"]) for r in eligible]
            scores = [float(r["current"]["score"]) for r in eligible]
            thresholds = [float((r.get("levels") or [{}])[-1].get("threshold_pct") or 50.0) for r in eligible]
            colors = ["#1b9e77" if "confirmed" in str(r.get("status")) else "#d95f02" if "provisional" in str(r.get("status")) else "#7570b3" for r in eligible]
            fig, ax = plt.subplots(figsize=(12, 5), dpi=150)
            positions = np.arange(len(names))
            ax.barh(positions, scores, color=colors, alpha=.88)
            ax.scatter(thresholds, positions, marker="|", s=300, color="black", label="该项已定义门槛")
            ax.set_yticks(positions, names); ax.set_xlim(0, 100)
            ax.set_xlabel("分数（0–100%）"); ax.set_title("B：已定义能力门槛与当前 global frontier")
            ax.legend(loc="lower right", fontsize=8)
            ax.grid(axis="x", alpha=.25); fig.tight_layout(); p = output / "frontier_ai_capability_threshold_margin.png"; fig.savefig(p); plt.close(fig)
            descriptors.append(_sidecar(p, title="B 能力门槛", definition="展示适用 benchmark 的当前 global frontier 分数与该项已预定义的最高可观测门槛；95% 置信下界超过门槛才确认。", rows=eligible, packet=packet, font_path=font_path))
        # A summary chart: current and previous frontier when available.
        a_eligible = [r for r in a_rows if r.get("current") and r.get("previous")]
        if a_eligible:
            names = [next((str(m["label"]) for m in matrix.get("benchmarks") or [] if m["benchmark_id"] == r["benchmark_id"]), r["benchmark_id"]) for r in a_eligible]
            cur = [float(r["current"]["score"]) for r in a_eligible]; prev = [float(r["previous"]["score"]) for r in a_eligible]
            x = np.arange(len(names)); width=.38
            fig, ax = plt.subplots(figsize=(13, 5), dpi=150); ax.bar(x-width/2, prev, width, label="previous frontier"); ax.bar(x+width/2, cur, width, label="current frontier")
            ax.set_xticks(x, names, rotation=35, ha="right", fontsize=8); ax.set_ylabel("分数（0–100%）"); ax.set_title("A：同一 comparability group 的 global frontier 对比"); ax.legend(); fig.tight_layout()
            p = output / "frontier_ai_capability_frontier_delta.png"; fig.savefig(p); plt.close(fig)
            descriptors.append(_sidecar(p, title="Global frontier A 对比", definition="按 benchmark 在同一可比组选择当前/先前最高分模型；不同方法版本断开。", rows=a_eligible, packet=packet, font_path=font_path))
        return {"descriptors": descriptors, "tables": tables, "font_path": font_path,
                "glyph_status": glyph_report("Frontier AI 原始能力", font_path=font_path)}
    except Exception as exc:
        return {"descriptors": descriptors, "tables": tables, "visualization_warning": f"raw capability renderer failed: {type(exc).__name__}: {exc}"}
