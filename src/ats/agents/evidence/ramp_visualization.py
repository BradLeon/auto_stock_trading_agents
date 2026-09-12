"""Deterministic Ramp charts for the L1 review packet.

The renderer consumes only ``packet['supplemental_signals']``.  Every PNG, table
and sidecar is generated from the exact same rows and carries the slice manifest
and rows hash, so a reader can reproduce the figure without querying Ramp.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

RENDERER_VERSION = "ramp_ai_index_renderer/v3"


def _find_cjk_font() -> str | None:
    """Find an installed font that contains the Chinese glyphs used by charts."""
    candidates = (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    )
    try:
        from matplotlib import ft2font
    except Exception:
        return None
    required = "采用率期间行业支出模型份额"
    for candidate in candidates:
        if not Path(candidate).exists():
            continue
        try:
            cmap = ft2font.FT2Font(candidate).get_charmap()
            if all(ord(char) in cmap for char in required):
                return candidate
        except Exception:
            continue
    return None


def _configure_font(plt) -> str | None:
    """Install a verified CJK font, avoiding macOS Arial square-glyph output."""
    path = _find_cjk_font()
    if path:
        from matplotlib import font_manager

        font_manager.fontManager.addfont(path)
        name = font_manager.FontProperties(fname=path).get_name()
        plt.rcParams["font.family"] = [name, "DejaVu Sans"]
    else:
        # A readable English fallback is preferable to silently emitting boxes
        # on a minimal CI/container image.
        plt.rcParams["font.family"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return path


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
            writer.writerow({key: (json.dumps(value, ensure_ascii=False, sort_keys=True,
                                               default=str) if isinstance(value, (dict, list)) else value)
                             for key, value in row.items()})
    return {"table_name": name, "json_path": str(json_path), "csv_path": str(csv_path),
            "rows_hash": _hash(rows), "row_count": len(rows),
            "renderer_version": RENDERER_VERSION}


def _sidecar(path: Path, *, title: str, definition: str, unit: str,
             source: str, rows: list[dict[str, Any]], manifest: dict[str, Any] | None,
             scope: str, display: dict[str, Any] | None = None) -> dict[str, Any]:
    ids = sorted({row.get("observation_id", "") for row in rows if row.get("observation_id")})
    artifacts = sorted({row.get("artifact_id", "") for row in rows if row.get("artifact_id")})
    manifest_id = (manifest or {}).get("snapshot_id")
    body = {"title": title, "metric_definition": definition, "unit": unit,
            "source": source, "scope": scope, "chart_slug": path.stem,
            "periods": sorted({str(row.get("period", "")) for row in rows}),
            "rows_hash": _hash(rows), "observation_ids": ids, "artifact_ids": artifacts,
            "manifest_id": manifest_id, "renderer_version": RENDERER_VERSION}
    if display:
        body["display"] = display
    # Keep chart provenance separate from the same-scope JSON table.  Using
    # ``with_suffix('.json')`` would overwrite ``_write_table`` output for
    # scopes whose chart stem and table stem are identical.
    sidecar = path.with_name(f"{path.stem}.sidecar.json")
    sidecar.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                  indent=2, default=str), encoding="utf-8")
    return {"title": title, "png_path": str(path), "sidecar_path": str(sidecar), **body}


def _plot(rows: list[dict[str, Any]], *, path: Path, title: str, y_label: str,
          value_field: str = "value", group_field: str = "", unit: str = "percent",
          source: str = "Ramp AI Index", scope: str = "", manifest=None,
          definition: str = "", display: dict[str, Any] | None = None) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme(style="whitegrid")
    _configure_font(plt)
    fig, axis = plt.subplots(figsize=(10, 5.2), dpi=150)
    periods = sorted({str(row.get("period", ""))[:7] for row in rows})
    positions = {period: index for index, period in enumerate(periods)}
    if group_field:
        groups = sorted({str(row.get(group_field, "")) for row in rows})
        for group in groups:
            subset = [row for row in rows if str(row.get(group_field, "")) == group]
            subset.sort(key=lambda row: str(row.get("period", "")))
            axis.plot([positions[str(row.get("period", ""))[:7]] for row in subset],
                      [row.get(value_field) for row in subset], marker="o", label=group)
        axis.legend(title=group_field, loc="best")
    else:
        subset = sorted(rows, key=lambda row: str(row.get("period", "")))
        axis.plot([positions[str(row.get("period", ""))[:7]] for row in subset],
                  [row.get(value_field) for row in subset], marker="o")
    _sparse_period_ticks(axis, periods)
    if periods:
        axis.set_xlim(-0.5, len(periods) - 0.5)
    axis.set_title(title)
    axis.set_xlabel("期间")
    axis.set_ylabel(y_label)
    axis.tick_params(axis="x", rotation=35)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return _sidecar(path, title=title, definition=definition, unit=unit,
                    source=source, rows=rows, manifest=manifest, scope=scope,
                    display=display)


def _sparse_period_ticks(axis, periods: list[str], *, max_ticks: int = 8) -> None:
    """Use a readable subset of monthly labels while retaining all plotted points."""
    if not periods:
        return
    if len(periods) <= max_ticks:
        indexes = list(range(len(periods)))
    else:
        # Annual January labels make a long monthly history scannable. Always
        # retain the first and latest month, even when the series starts or
        # ends mid-year.
        candidates = [i for i, period in enumerate(periods) if period.endswith("-01")]
        indexes = sorted(set([0, len(periods) - 1] + candidates))
        if len(indexes) > max_ticks:
            indexes = [round(i * (len(periods) - 1) / (max_ticks - 1))
                       for i in range(max_ticks)]
    axis.set_xticks(indexes)
    axis.set_xticklabels([periods[index] for index in indexes], rotation=35, ha="right")


def _plot_spend(rows: list[dict[str, Any]], *, path: Path, manifest, scope: str) -> dict[str, Any]:
    """Render a readable spend distribution without the unrepresentative Top 1% tail."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    labels = {"median": "Median（中位数）", "top30": "Top 30%", "top10": "Top 10%"}
    order = ("median", "top30", "top10")
    available = [quantile for quantile in order
                 if any(str(row.get("quantile", "")).casefold() == quantile for row in rows)]
    # Ramp's current public export supplies Median, Top 10% and Top 1%, but no
    # Top 30% series. Never interpolate or rename Top 1% as Top 30%; the
    # missing series is disclosed in the sidecar and report instead.
    if not available:
        return _plot(rows, path=path, title="Ramp AI 支出/员工（月度分位数）",
                     y_label="USD/员工/月", group_field="quantile", unit="usd_per_employee_month",
                     scope=scope, manifest=manifest, definition="官方导出未提供可绘制的分位数。")
    sns.set_theme(style="whitegrid")
    import matplotlib.pyplot as plt
    _configure_font(plt)
    fig, axis = plt.subplots(figsize=(10, 5.2), dpi=150)
    periods = sorted({str(row.get("period", ""))[:7] for row in rows})
    positions = {period: index for index, period in enumerate(periods)}
    for quantile in available:
        subset = sorted((row for row in rows
                         if str(row.get("quantile", "")).casefold() == quantile),
                        key=lambda row: str(row.get("period", "")))
        axis.plot([positions[str(row.get("period", ""))[:7]] for row in subset],
                  [row.get("value") for row in subset], marker="o", linewidth=1.8,
                  label=labels[quantile])
    # The range spans orders of magnitude; log scale prevents the median from
    # disappearing while preserving the original USD values.
    axis.set_yscale("log")
    _sparse_period_ticks(axis, periods)
    if periods:
        axis.set_xlim(-0.5, len(periods) - 0.5)
    axis.set_title("Ramp AI 支出/员工（月度分位数）")
    axis.set_xlabel("期间")
    axis.set_ylabel("USD/员工/月（对数刻度）")
    axis.legend(title="分位数")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    missing = [label for label in ("top30",) if label not in available]
    definition = ("Ramp 网络企业 AI 月支出的 source-native 分位数；图中保留 Median 与 Top 10%，"
                  "不绘制 Top 1% 以避免极端尾部压缩趋势。当前官方公开导出未提供 Top 30%，"
                  "因此不插值、不估算。")
    return _sidecar(path, title="Ramp AI 支出/员工（月度分位数）", definition=definition,
                    unit="usd_per_employee_month", source="Ramp AI Index",
                    rows=rows, manifest=manifest, scope=scope,
                    display={"series": [labels[item] for item in available],
                             "missing_source_series": missing, "scale": "log10"})


def _plot_market_share(rows: list[dict[str, Any]], *, path: Path, manifest, scope: str) -> dict[str, Any]:
    """Render the overall model market share as a compact 100% stacked chart."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    source_rows = [row for row in rows if row.get("value") is not None]
    periods = sorted({str(row.get("period", ""))[:7] for row in source_rows})
    if not source_rows or not periods:
        return _plot(rows, path=path, title="Ramp 模型市场份额（Overall）",
                     y_label="模型 API 支出份额（%）", group_field="series_label", scope=scope,
                     manifest=manifest, definition="Token Spend Management cohort 的模型 API spend share。")
    for row in source_rows:
        row.setdefault("series_label", f"{row.get('provider', '')}/{row.get('model', '')}".strip("/"))
    latest = periods[-1]
    latest_values: dict[str, float] = {}
    history_values: dict[str, float] = {}
    for row in source_rows:
        label = str(row.get("series_label", "Other"))
        history_values[label] = history_values.get(label, 0.0) + float(row.get("value") or 0.0)
        if str(row.get("period", ""))[:7] == latest:
            latest_values[label] = latest_values.get(label, 0.0) + float(row.get("value") or 0.0)
    # Preserve long-lived models as well as newly dominant models. Selecting
    # only the latest month would make the earlier history collapse into
    # ``Other`` whenever provider model names are rolled forward.
    history_top = [label for label, _ in sorted(history_values.items(), key=lambda item: (-item[1], item[0]))[:4]]
    latest_top = [label for label, _ in sorted(latest_values.items(), key=lambda item: (-item[1], item[0]))[:8]]
    keep = list(dict.fromkeys(history_top + latest_top))[:8]
    display_labels = keep + ["Other"]
    matrix = {label: [0.0] * len(periods) for label in display_labels}
    for row in source_rows:
        period = str(row.get("period", ""))[:7]
        label = str(row.get("series_label", "Other"))
        if period not in periods:
            continue
        matrix[label if label in keep else "Other"][periods.index(period)] += float(row.get("value") or 0.0)
    sns.set_theme(style="whitegrid")
    _configure_font(plt)
    fig, axis = plt.subplots(figsize=(10, 5.2), dpi=150)
    x = list(range(len(periods)))
    axis.stackplot(x, [matrix[label] for label in display_labels], labels=display_labels,
                   alpha=0.88, linewidth=0.25)
    _sparse_period_ticks(axis, periods)
    axis.set_xlim(-0.5, len(periods) - 0.5)
    axis.set_ylim(0, 100)
    axis.set_title("Ramp 模型市场份额（Overall）")
    axis.set_xlabel("期间")
    axis.set_ylabel("模型 API 支出份额（%）")
    axis.legend(title="代表模型（历史与最新期，其余合并）", loc="upper left", bbox_to_anchor=(1.01, 1),
                fontsize=8)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    definition = ("Token Spend Management 连接企业的模型归因 API spend share；保留历史累计份额靠前且"
                  "最新期仍有代表性的模型（最多 8 个），其余模型合并为 Other，合计仍为该 cohort 的 100%。")
    return _sidecar(path, title="Ramp 模型市场份额（Overall）", definition=definition,
                    unit="percent", source="Ramp AI Index", rows=rows, manifest=manifest, scope=scope,
                    display={"series": display_labels, "latest_period": latest,
                             "top_n": len(keep), "aggregation": "history_top4_plus_latest_top8_capped8_plus_other"})


def render_ramp_charts(*, packet: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    """Render Ramp figures and tables; factual packet survives renderer failure."""
    signal = packet.get("supplemental_signals", {}).get("ramp_paid_adoption", {})
    slices = signal.get("slices") or {}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    tables: list[dict[str, Any]] = []
    descriptors: list[dict[str, Any]] = []
    try:
        adoption = list((slices.get("adoption_overall") or {}).get("rows") or [])
        models = list((slices.get("adoption_overall_models") or {}).get("rows") or [])
        sectors = list((slices.get("adoption_sector") or {}).get("rows") or [])
        spend = list((slices.get("spend_per_employee_overall") or {}).get("rows") or [])
        shares = list((slices.get("model_market_share_overall") or {}).get("rows") or [])
        # Keep provider-published pp deltas in tables, but never plot them as
        # extra level observations.
        adoption = [row for row in adoption if row.get("metric_id", "").endswith("adoption_share") or not row.get("metric_id")]
        models = [row for row in models if row.get("metric_id", "").endswith("adoption_share") or not row.get("metric_id")]
        sectors = [row for row in sectors if row.get("metric_id", "").endswith("adoption_share") or not row.get("metric_id")]
        for name, rows in (("ramp_adoption_overall", adoption), ("ramp_adoption_models", models),
                           ("ramp_adoption_sector", sectors), ("ramp_spend_per_employee", spend),
                           ("ramp_model_market_share", shares)):
            if rows:
                tables.append(_write_table(output, name, rows))
        if adoption:
            descriptors.append(_plot(adoption, path=output / "ramp_adoption_overall.png",
                title="Ramp 付费企业 AI 采用率（Overall）", y_label="Ramp cohort 付费企业占比（%）",
                unit="percent", scope="adoption_overall", manifest=(slices.get("adoption_overall") or {}).get("manifest"),
                definition="当月对 AI 产品/服务发生正向 Ramp 付款的相关企业 ÷ Ramp 相关企业 cohort。"))
        if models:
            descriptors.append(_plot(models, path=output / "ramp_adoption_overall_models.png",
                title="Ramp 付费企业 AI 采用率（模型供应商）", y_label="Ramp cohort 付费企业占比（%）",
                group_field="segment", scope="adoption_overall_models",
                manifest=(slices.get("adoption_overall_models") or {}).get("manifest"),
                definition="Ramp 相关企业按模型供应商的付费采用率；同一企业可同时支付多个供应商，不能相加为 100%。"))
        if sectors:
            descriptors.append(_plot(sectors, path=output / "ramp_adoption_sector.png",
                title="Ramp 付费企业 AI 采用率（NAICS 行业）", y_label="付费企业占比（%）",
                group_field="segment", scope="adoption_sector", manifest=(slices.get("adoption_sector") or {}).get("manifest"),
                definition="按 Ramp/NAICS 行业 segment 的 source-native adoption rate；不与 Overall 相加。"))
        if spend:
            descriptors.append(_plot_spend(
                spend, path=output / "ramp_spend_per_employee.png",
                scope="spend_per_employee_overall",
                manifest=(slices.get("spend_per_employee_overall") or {}).get("manifest")))
        if shares:
            shares = [dict(row, series_label=(f"{row.get('provider', '')}/{row.get('model', '')}".strip("/")))
                      for row in shares]
            descriptors.append(_plot_market_share(
                shares, path=output / "ramp_model_market_share.png",
                scope="model_market_share_overall",
                manifest=(slices.get("model_market_share_overall") or {}).get("manifest")))
        return {"descriptors": descriptors, "tables": tables}
    except Exception as exc:  # optional visuals must not invalidate observations
        return {"descriptors": [], "tables": tables,
                "visualization_warning": f"ramp renderer failed: {type(exc).__name__}: {exc}"}
