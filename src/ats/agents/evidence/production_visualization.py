"""Deterministic, optional rendering for the AI production-penetration packet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

RENDERER_VERSION = "ai_production_penetration_renderer/v1"


def _hash(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            records, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


def _write_sidecar(
    path: Path,
    *,
    chart_type: str,
    records: list[dict[str, Any]],
    manifest: dict | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    data_hash = _hash(records)
    sidecar = {
        "renderer_version": RENDERER_VERSION,
        "chart_type": chart_type,
        "data_hash": data_hash,
        "records": records,
        "manifest_id": (manifest or {}).get("snapshot_id"),
        **metadata,
    }
    sidecar_path = path.with_suffix(".json")
    sidecar_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "chart_type": chart_type,
        "png_path": str(path),
        "sidecar_path": str(sidecar_path),
        "data_hash": data_hash,
        "renderer_version": RENDERER_VERSION,
    }


def render_production_charts(
    *,
    frames: dict[str, Any],
    output_dir: str | Path,
    manifest: dict | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Render only supplied DataFrames; never query or derive governed data here."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import seaborn as sns
        from matplotlib import font_manager
    except (ImportError, RuntimeError) as exc:
        return {"descriptors": [], "visualization_warning": f"optional renderer unavailable: {exc}"}
    try:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        # A macOS system font is used explicitly for Chinese reader-facing labels;
        # falling back to DejaVu would silently render missing-glyph boxes.
        chinese_font_path = "/System/Library/Fonts/PingFang.ttc"
        font_properties = (
            font_manager.FontProperties(fname=chinese_font_path)
            if Path(chinese_font_path).exists()
            else font_manager.FontProperties(family="DejaVu Sans")
        )
        matplotlib.rcParams["axes.unicode_minus"] = False
        summary = frames["summary"].copy().sort_values(["period", "grain"])
        records = summary.to_dict(orient="records")
        sns.set_theme(style="whitegrid", context="notebook")
        periods = sorted(summary["period"].dropna().unique())
        descriptors = []
        chart_path = output / "ai_production_penetration.png"
        metrics = [
            ("visible_production_rate_pct", "职业/任务可见单元生产化率"),
            ("production_traffic_share_pct", "职业/任务生产化流量份额"),
        ]
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), dpi=150, sharex=False)
        for axis, (field, label) in zip(axes, metrics):
            for grain, color in (("occupation", "#2f6f9f"), ("task", "#bc6c25")):
                selected = summary[summary["grain"] == grain]
                # Do not draw a visual bridge across a provider-methodology regime.
                for index, (_methodology, regime) in enumerate(
                    selected.groupby("methodology_version", dropna=False)
                ):
                    axis.plot(
                        regime["period"],
                        regime[field],
                        marker="o",
                        color=color,
                        label={"occupation": "职业", "task": "任务"}[grain]
                        if index == 0
                        else "_nolegend_",
                    )
            axis.set_title(label, fontproperties=font_properties)
            axis.set_ylabel("占比（%）", fontproperties=font_properties)
            axis.legend(
                title="统计维度",
                prop=font_properties,
                title_fontproperties=font_properties,
            )
        title = (
            "AI 应用层生产化：月度比较（历史不足，不能判断持续趋势）"
            if len(periods) < 3
            else "AI 应用层生产化：可比期间序列"
        )
        fig.suptitle(title, fontproperties=font_properties)
        fig.tight_layout()
        fig.savefig(chart_path, bbox_inches="tight")
        plt.close(fig)
        descriptors.append(
            _write_sidecar(
                chart_path,
                chart_type="monthly_comparison" if len(periods) < 3 else "time_series",
                records=records,
                manifest=manifest,
                metadata={**metadata, "period_range": periods, "unit": "percent"},
            )
        )

        coverage = frames["occupation_coverage_distribution"].copy()
        curve = coverage[coverage["point_type"] == "curve"].sort_values("minimum_coverage_pct")
        if not curve.empty:
            coverage_path = output / "ai_production_occupation_coverage_ccdf.png"
            fig, axis = plt.subplots(figsize=(7, 4.8), dpi=150)
            axis.step(
                curve["minimum_coverage_pct"],
                curve["occupation_share_pct"],
                where="post",
                color="#4c956c",
            )
            axis.set_xlabel("职业任务组合的已确认生产化覆盖（%）", fontproperties=font_properties)
            axis.set_ylabel("覆盖率至少达到该水平的职业占比（%）", fontproperties=font_properties)
            latest = curve.iloc[0]
            axis.set_title(
                "职业任务组合的已确认生产化覆盖分布"
                f"（有任务组合的职业={int(latest['eligible_occupation_count'])}）",
                fontproperties=font_properties,
            )
            fig.tight_layout()
            fig.savefig(coverage_path, bbox_inches="tight")
            plt.close(fig)
            descriptors.append(
                _write_sidecar(
                    coverage_path,
                    chart_type="occupation_coverage_ccdf",
                    records=coverage.to_dict(orient="records"),
                    manifest=manifest,
                    metadata={
                        **metadata,
                        "period": str(latest["period"]),
                        "taxonomy_version": str(latest["taxonomy_version"]),
                        "unit": "percent",
                        "reader_metric_name": "职业任务组合的已确认生产化覆盖",
                        "coverage_semantics": "公开 1P API 可确认的任务覆盖；未公开或无法映射任务保留在分母。",
                        "lower_bound": True,
                    },
                )
            )
        return {"descriptors": descriptors}
    except Exception as exc:  # noqa: BLE001 - rendering is deliberately non-fatal to the factual packet
        return {
            "descriptors": [],
            "visualization_warning": f"renderer failed: {type(exc).__name__}: {exc}",
        }
