"""Reader-facing tables and charts for the L1 AI adoption v2 packet."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any

RENDERER_VERSION = "ai_production_diffusion_renderer/v2"


def _period_key(value: Any) -> tuple:
    text = str(value or "")
    if text.isdigit(): return (0, int(text))
    wave = re.fullmatch(r"wave[-_ ]?(\d+)", text, re.IGNORECASE)
    if wave: return (1, int(wave.group(1)))
    quarter = re.fullmatch(r"(\d{4})-Q([1-4])", text, re.IGNORECASE)
    if quarter: return (2, int(quarter.group(1)), int(quarter.group(2)))
    month = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", text)
    if month: return (3, *(int(part) if part else 0 for part in month.groups()))
    return (9, text)


def _period_label(row: dict) -> str:
    period = str(row.get("period", ""))
    start, end = row.get("period_start"), row.get("period_end")
    if end:
        return str(end)[:10]
    if start:
        return str(start)[:10]
    return f"波次 {period}" if period.isdigit() else period


def _hash(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                     default=str, separators=(",", ":")).encode()).hexdigest()


def _write_table(output: Path, name: str, rows: list[dict]) -> dict:
    normalized = [{key: (json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                         if isinstance(value, (dict, list)) else value)
                   for key, value in row.items()} for row in rows]
    json_path, csv_path = output / f"{name}.json", output / f"{name}.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2,
                                    default=str), encoding="utf-8")
    fields = sorted({key for row in normalized for key in row})
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(normalized)
    return {"table_name": name, "json_path": str(json_path), "csv_path": str(csv_path),
            "rows_hash": _hash(rows), "row_count": len(rows)}


def _sidecar(path: Path, *, title: str, definition: str, unit: str, source: str,
             periods: list[str], rows: list[dict], manifest_id: str | None,
             observation_ids: list[str] | None = None) -> dict:
    ids = (sorted(set(observation_ids)) if observation_ids is not None else
           sorted({item for row in rows for item in row.get("input_observation_ids", [])}))
    body = {"title": title, "metric_definition": definition, "unit": unit, "source": source,
            "periods": periods, "rows_hash": _hash(rows), "observation_ids": ids,
            "manifest_id": manifest_id, "renderer_version": RENDERER_VERSION}
    sidecar = path.with_suffix(".json")
    sidecar.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return {"title": title, "png_path": str(path), "sidecar_path": str(sidecar), **body}


def render_ai_adoption_charts(*, packet: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    """Render supplied review data only; failures never alter the factual packet."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    axis_rows = sorted(packet.get("axis_overview", []), key=lambda row: row["axis_id"])
    task = packet.get("axes", {}).get("task_production", {}).get("detail", {})
    btos = packet.get("axes", {}).get("enterprise_breadth", {}).get("detail", {})
    rps = packet.get("axes", {}).get("worker_persistence", {}).get("detail", {})
    btos_cross_section = sorted(
        btos.get("strata", {}).get("industry", []) + btos.get("strata", {}).get("employment_size", []),
        key=lambda row: (row.get("entity_id", ""), row.get("metric_id", "")))
    rps_all = sorted([dict(row, series=name) for name, values in rps.get("history", {}).items()
                      for row in values], key=lambda row: (row.get("series", ""), _period_key(row.get("period", ""))))
    tables = [
        _write_table(output, "three_axis_overview", axis_rows),
        _write_table(output, "btos_national_history", btos.get("history", [])),
        _write_table(output, "btos_latest_industry_size", btos_cross_section),
        _write_table(output, "rps_work_use_and_hours", rps_all),
        _write_table(output, "anthropic_production_series", sorted(task.get("period_rows", []),
                                                                   key=lambda r: (r.get("period", ""), r.get("grain", "")))),
        _write_table(output, "anthropic_occupation_coverage", task.get("occupation_task_coverage", [])),
        _write_table(output, "anthropic_top_occupations", packet.get("top_occupations", [])),
        _write_table(output, "anthropic_top_tasks", packet.get("top_tasks", [])),
    ]
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        import seaborn as sns

        font_path = "/System/Library/Fonts/PingFang.ttc"
        font = font_manager.FontProperties(fname=font_path) if Path(font_path).exists() else None
        if font:
            matplotlib.rcParams["font.family"] = font.get_name()
        sns.set_theme(style="whitegrid")
        manifest_id = (packet.get("manifest") or {}).get("snapshot_id")
        descriptors = []

        # Source-native panels: visually separate axes with incompatible denominators.
        panels = []
        panels.append(("BTOS 美国企业 AI 采用广度", btos.get("history", []), "value",
                       "企业占比（%）", "US Census BTOS"))
        panels.append(("RPS 美国员工工作使用持续性", rps.get("history", {}).get("last_week", []),
                       "value", "员工占比（%）", "RPS / FRED"))
        panels.append(("RPS AI 辅助工时占比", rps.get("history", {}).get("assisted_hours", []),
                       "value", "总工作工时占比（%）", "RPS / FRED"))
        panels.append(("RPS 自报节省工时占比", rps.get("history", {}).get("time_saved", []),
                       "value", "总工作工时占比（%）", "RPS / FRED"))
        for index, (title, rows, field, unit, source) in enumerate(panels, start=1):
            if not rows: continue
            rows = sorted(rows, key=lambda row: _period_key(row.get("period", "")))
            slug = ("btos_enterprise_breadth", "rps_worker_persistence", "rps_assisted_hours",
                    "rps_time_saved")[index - 1]
            chart = output / f"{slug}.png"
            fig, axis = plt.subplots(figsize=((13.5 if source == "US Census BTOS" else 7.5), 4.8), dpi=150)
            axis.plot([_period_label(row) for row in rows], [row.get(field) for row in rows], marker="o")
            axis.set_title(title, fontproperties=font); axis.set_ylabel(unit, fontproperties=font)
            if source == "US Census BTOS":
                axis.set_xlabel("调查参考期结束日", fontproperties=font)
            for label in axis.get_xticklabels() + axis.get_yticklabels():
                label.set_fontproperties(font)
            axis.tick_params(axis="x", rotation=(45 if source == "US Census BTOS" else 30), labelsize=8)
            fig.tight_layout()
            fig.savefig(chart, bbox_inches="tight"); plt.close(fig)
            enriched = [{**row, "input_observation_ids": row.get("input_observation_ids") or
                         row.get("lineage", {}).get("input_observation_ids", []) or
                         ([row["observation_id"]] if row.get("observation_id") else [])} for row in rows]
            descriptors.append(_sidecar(chart, title=title,
                definition="来源原生 headline 的可比期间序列；不与其他面板数值合并。",
                unit=unit, source=source, periods=[str(row.get("period")) for row in rows],
                rows=enriched, manifest_id=manifest_id))

        # Anthropic's four core measures are two concepts observed at two
        # independent classification grains. Keep all four visible together.
        production_rows = sorted(task.get("period_rows", []),
                                 key=lambda row: (_period_key(row.get("period", "")), row.get("grain", "")))
        if production_rows:
            title = "Anthropic 职业/任务可见单元生产化率与生产化流量份额"
            chart = output / "anthropic_production_four_metrics.png"
            fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), dpi=150)
            for axis, field, panel_title in (
                (axes[0], "visible_production_rate_pct", "职业/任务可见单元生产化率"),
                (axes[1], "production_traffic_share_pct", "职业/任务生产化流量份额"),
            ):
                for grain, color, label in (("occupation", "#2f6f9f", "职业"),
                                             ("task", "#bc6c25", "任务")):
                    rows = [row for row in production_rows if row.get("grain") == grain]
                    axis.plot([row.get("period") for row in rows], [row.get(field) for row in rows],
                              marker="o", color=color, label=label)
                axis.set_title(panel_title, fontproperties=font)
                axis.set_ylabel("占比（%）", fontproperties=font)
                axis.legend(title="统计维度", prop=font, title_fontproperties=font)
                for label in axis.get_xticklabels() + axis.get_yticklabels():
                    label.set_fontproperties(font)
            periods = sorted({str(row.get("period")) for row in production_rows}, key=_period_key)
            fig.suptitle(("Anthropic 生产化月度比较（历史不足，不能判断持续趋势）"
                          if len(periods) < 3 else "Anthropic 生产化可比期间序列"),
                         fontproperties=font)
            fig.tight_layout(); fig.savefig(chart, bbox_inches="tight"); plt.close(fig)
            enriched = [{**row, "input_observation_ids":
                         row.get("lineage", {}).get("input_observation_ids", [])}
                        for row in production_rows]
            descriptors.append(_sidecar(
                chart, title=title,
                definition="左图为达标公开单元数/可见公开单元数；右图为达标单元 Usage Share 之和。职业与任务是独立分类视角。",
                unit="percent", source="Anthropic Economic Index 1P API",
                periods=periods, rows=enriched, manifest_id=manifest_id))

        coverage = task.get("occupation_coverage_distribution", {})
        curve = sorted(coverage.get("points", []), key=lambda row: row.get("minimum_coverage_pct", 0))
        if curve:
            title = "Anthropic 职业内已确认生产化任务覆盖分布"
            chart = output / "anthropic_occupation_task_coverage_ccdf.png"
            fig, axis = plt.subplots(figsize=(8, 5), dpi=150)
            axis.step([row.get("minimum_coverage_pct") for row in curve],
                      [row.get("occupation_share_pct") for row in curve],
                      where="post", color="#4c956c")
            axis.set_xlabel("职业内已确认生产化任务覆盖率（%）", fontproperties=font)
            axis.set_ylabel("覆盖率至少达到该水平的职业占比（%）", fontproperties=font)
            eligible = curve[0].get("eligible_occupation_count", 0)
            axis.set_title(f"{title}（有任务组合的职业={eligible}）", fontproperties=font)
            for label in axis.get_xticklabels() + axis.get_yticklabels():
                label.set_fontproperties(font)
            fig.tight_layout(); fig.savefig(chart, bbox_inches="tight"); plt.close(fig)
            coverage_observation_ids = sorted({observation_id
                for row in task.get("occupation_task_coverage", [])
                if str(row.get("period")) in {str(item.get("period")) for item in curve}
                for observation_id in row.get("lineage", {}).get("input_observation_ids", [])})
            descriptors.append(_sidecar(
                chart, title=title,
                definition="分子为职业内达到生产化代理且公开的任务数；分母为该职业全部去重 O*NET 任务数。",
                unit="percent", source="Anthropic Economic Index 1P API + O*NET taxonomy",
                periods=sorted({str(row.get("period")) for row in curve}), rows=curve,
                manifest_id=manifest_id, observation_ids=coverage_observation_ids))

        # Anthropic TOPN remains after the overall distribution in the report and
        # is rendered on its own source-native Usage Share axis.
        for name, title, rows in (("top_occupations", "Anthropic TOP10 生产化职业", packet.get("top_occupations", [])),
                                  ("top_tasks", "Anthropic TOP10 生产化任务", packet.get("top_tasks", []))):
            if not rows: continue
            ranked = list(reversed(rows[:10]))
            chart = output / f"anthropic_{name}.png"
            fig, axis = plt.subplots(figsize=(10, 5.5), dpi=150)
            labels = [str(row.get("entity_name", row.get("entity_id", "")))[:60] for row in ranked]
            axis.barh(labels, [row.get("usage_share_pct", 0) for row in ranked], color="#4c78a8")
            axis.set_title(title, fontproperties=font); axis.set_xlabel("1P API Usage Share（%）", fontproperties=font)
            for label in axis.get_xticklabels() + axis.get_yticklabels():
                label.set_fontproperties(font)
            fig.tight_layout(); fig.savefig(chart, bbox_inches="tight"); plt.close(fig)
            enriched = [{**row, "input_observation_ids": row.get("lineage", {}).get("input_observation_ids", [])}
                        for row in ranked]
            descriptors.append(_sidecar(chart, title=title,
                definition="满足生产化代理的公开单元，按最新月 1P API Usage Share 排序。",
                unit="percent", source="Anthropic Economic Index 1P API",
                periods=sorted({str(row.get("period")) for row in ranked}), rows=enriched,
                manifest_id=manifest_id))
        return {"descriptors": descriptors, "tables": tables}
    except Exception as exc:  # rendering is optional; governed facts remain available
        return {"descriptors": [], "tables": tables,
                "visualization_warning": f"renderer failed: {type(exc).__name__}: {exc}"}
