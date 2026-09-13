"""Deterministic Frontier AI Labs revenue charts for the L1 commercialization packet.

Only the same governed rows feed the PNG, the CSV/JSON table and the sidecar, so
all three share one rows hash.  Lines connect points inside one comparable cell
only; they are a direction aid between discrete disclosures, never a monthly
estimate.  Different metric identities are never connected or merged.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .chart_font import configure_font, find_cjk_font, glyph_report

RENDERER_VERSION = "frontier_labs_revenue_renderer/v1"
PALETTE = ["#C0392B", "#1F618D", "#117A65", "#7D3C98"]


def _hash(rows: list[dict[str, Any]]) -> str:
    body = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _write_table(output: Path, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    json_path, csv_path = output / f"{name}.json", output / f"{name}.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                    indent=2, default=str), encoding="utf-8")
    fields = sorted({key for row in rows for key in row})
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: (json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                      if isinstance(value, (dict, list)) else value)
                for key, value in row.items()})
    return {"table_name": name, "json_path": str(json_path), "csv_path": str(csv_path),
            "rows_hash": _hash(rows), "row_count": len(rows),
            "renderer_version": RENDERER_VERSION}


def _sidecar(path: Path, *, title: str, definition: str, unit: str, source: str,
             rows: list[dict[str, Any]], manifest: dict[str, Any] | None,
             metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    ids = sorted({str(row.get("observation_id") or "") for row in rows
                  if row.get("observation_id")})
    artifacts = sorted({str(row.get("artifact_id") or "") for row in rows
                        if row.get("artifact_id")})
    body = {
        "title": title, "metric_definition": definition, "unit": unit, "source": source,
        "chart_slug": path.stem,
        "periods": sorted({str(row.get("period") or "") for row in rows}),
        "rows_hash": _hash(rows), "observation_ids": ids, "artifact_ids": artifacts,
        "manifest_id": (manifest or {}).get("snapshot_id"),
        "renderer_version": RENDERER_VERSION,
    }
    if metadata:
        body["display"] = metadata
    sidecar = path.with_name(f"{path.stem}.sidecar.json")
    sidecar.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2,
                                  default=str), encoding="utf-8")
    return {"title": title, "png_path": str(path), "sidecar_path": str(sidecar), **body}


def _chart_rows(company: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(company.get("history_rows") or [])
    return sorted(rows, key=lambda item: (str(item.get("period_start") or ""),
                                          str(item.get("period") or "")))


def _panel(axis, rows: list[dict[str, Any]], *, title: str, color: str,
           company: str, trend: dict[str, Any], gap: dict[str, Any]) -> None:
    periods = [str(row.get("period") or "") for row in rows]
    values = [float(row.get("value_usd_bn") or 0.0) for row in rows]
    axis.plot(periods, values, marker="o", color=color, linewidth=2.0,
              markersize=6, label="同一可比口径的离散披露点")
    for index, row in enumerate(rows):
        identity = str(row.get("observation_identity_label") or "")
        axis.annotate(
            identity,
            (periods[index], values[index]),
            textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8,
            color="#555555")
    axis.set_title(f"{company}：{title}", fontsize=12)
    axis.set_ylabel("USD 十亿美元", fontsize=10)
    axis.set_xlabel("披露参考期（真实月份，不做月度插值）", fontsize=9)
    axis.tick_params(axis="x", rotation=45, labelsize=8)
    axis.grid(alpha=0.3, linestyle=":")
    status = str(trend.get("status") or "")
    note = f"方向：{trend.get('status_label', status)}"
    if gap.get("status") == "gap" and gap.get("missing_periods"):
        note += f"；缺口月份：{', '.join(gap['missing_periods'][:6])}"
    axis.text(0.01, 0.02, note, transform=axis.transAxes, fontsize=8, color="#333333")
    axis.legend(fontsize=8, loc="upper left")


def render_frontier_labs_revenue_charts(*, packet: dict[str, Any],
                                        output_dir: str | Path) -> dict[str, Any]:
    """Render the dual-panel revenue figure and every shared-hash table."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    companies = [item for item in (packet.get("companies") or [])
                 if item.get("history_rows")]
    manifest = packet.get("manifest") or packet.get("snapshot_manifest")
    tables: list[dict[str, Any]] = []
    descriptors: list[dict[str, Any]] = []
    if not companies:
        return {"descriptors": [], "tables": tables,
                "visualization_warning": "frontier_labs_revenue: 没有通过质量门的收入观察，未生成图表。"}
    history_rows = list(packet.get("history_rows") or [])
    if history_rows:
        tables.append(_write_table(output, "frontier_labs_revenue_history", history_rows))
    latest_rows = []
    for company in companies:
        view = company.get("headline") or {}
        record = view.get("record")
        if record:
            latest_rows.append(record)
    if latest_rows:
        tables.append(_write_table(output, "frontier_labs_revenue_latest", latest_rows))
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import seaborn as sns

        sns.set_theme(style="whitegrid")
        font_path = configure_font(plt)
        figure, axes = plt.subplots(1, len(companies), figsize=(6.2 * len(companies), 5.0),
                                    dpi=150)
        if len(companies) == 1:
            axes = [axes]
        for index, company in enumerate(companies):
            rows = _chart_rows(company)
            cell = company.get("comparison_cell") or {}
            title = str(cell.get("metric_label") or "可比收入序列")
            _panel(axes[index], rows, title=title,
                   color=PALETTE[index % len(PALETTE)],
                   company=str(company.get("company") or ""),
                   trend={"status": (company.get("trend") or {}).get("status", ""),
                          "status_label": (company.get("trend") or {}).get("status_label", "")},
                   gap=company.get("period_gap") or {})
        figure.suptitle("Frontier AI Labs 可比收入水平与趋势（离散披露点）", fontsize=13)
        figure.tight_layout(rect=(0, 0, 1, 0.94))
        path = output / "frontier_labs_revenue_trend.png"
        figure.savefig(path)
        plt.close(figure)
        chart_rows = [row for company in companies for row in _chart_rows(company)]
        descriptors.append(_sidecar(
            path, title="Frontier AI Labs 可比收入水平与趋势",
            definition="同一可比 cell（公司 × 计量口径 × 观察身份 × methodology regime）内的离散披露点；"
                       "连线只表示方向，不表示未披露月份的估计值。",
            unit="USD 十亿美元", source="Sacra 公开公司页面 + TickerTrends 2026H1 冻结回填",
            rows=chart_rows, manifest=manifest,
            metadata={"font_path": font_path or "",
                      "glyph_check": glyph_report("Frontier AI Labs 可比收入水平与趋势离散披露点",
                                                  font_path=font_path)}))
        if history_rows:
            # The chart itself must be reproducible from the same rows hash that
            # the CSV/JSON table and the Markdown report publish.
            descriptors[-1]["rows_hash"] = _hash(chart_rows)
            for table in tables:
                if table["table_name"] == "frontier_labs_revenue_history":
                    table["chart_rows_hash"] = _hash(chart_rows)
        return {"descriptors": descriptors, "tables": tables,
                "font_path": font_path or "",
                "glyph_status": glyph_report("收入运行率最新历史变化", font_path=font_path)}
    except Exception as exc:  # visuals must never invalidate an observation packet
        return {"descriptors": [], "tables": tables,
                "visualization_warning":
                    f"frontier labs revenue renderer failed: {type(exc).__name__}: {exc}"}
