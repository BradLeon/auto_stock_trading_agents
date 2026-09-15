"""Four deterministic OpenRouter token-economy charts and shared sidecars."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .chart_font import configure_font, glyph_report

RENDERER_VERSION = "openrouter_rankings_renderer/v1"


def _hash(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def _write_table(out: Path, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    json_path, csv_path = out / f"{name}.json", out / f"{name}.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2, default=str), encoding="utf-8")
    fields = sorted({key for row in rows for key in row})
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else value for key, value in row.items()})
    return {"table_name": name, "json_path": str(json_path), "csv_path": str(csv_path),
            "row_count": len(rows), "rows_hash": _hash(rows), "renderer_version": RENDERER_VERSION}


def _sidecar(path: Path, title: str, definition: str, unit: str, rows: list[dict[str, Any]], manifest: dict[str, Any] | None, font_path: str, source_as_of: str = "", derivation_version: str = "") -> dict[str, Any]:
    payload = {"title": title, "metric_definition": definition, "unit": unit,
               "source": "OpenRouter Rankings Data API", "license": "CC BY 4.0",
               "chart_slug": path.stem, "rows_hash": _hash(rows),
               "periods": sorted({str(row.get("period", "")) for row in rows}),
               "manifest_id": (manifest or {}).get("snapshot_id"),
               "source_as_of": source_as_of,
               "derivation_version": derivation_version,
               "renderer_version": RENDERER_VERSION, "font_path": font_path,
               "glyph_check": glyph_report(title, font_path=font_path)}
    sidecar = path.with_name(f"{path.stem}.sidecar.json")
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str), encoding="utf-8")
    return {**payload, "title": title, "png_path": str(path), "sidecar_path": str(sidecar)}


def render_openrouter_charts(*, bundle: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    volume = bundle.get("volume") or {}
    authors = bundle.get("authors") or {}
    leaderboard = bundle.get("leaderboard") or {}
    model_ranking = bundle.get("model_ranking") or {}
    concentration = bundle.get("concentration") or {}
    volume_rows = volume.get("rows") or []
    author_rows = authors.get("rows") or []
    concentration_rows = concentration.get("rows") or []
    source_as_of = str(volume.get("source_as_of") or "")
    derivation_version = str(bundle.get("derivation_version") or volume.get("lineage", {}).get("derivation_version") or "")
    descriptors, tables = [], []
    all_rows = volume_rows + author_rows + leaderboard.get("rows", []) + concentration_rows
    if not volume_rows:
        return {"descriptors": [], "tables": [], "visualization_warning": "openrouter: 没有完整 UTC 周数据，未生成图表。"}
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        font_path = configure_font(plt) or ""
        if not font_path:
            # Do not publish PNGs that would render CJK labels as square
            # glyphs. Preserve a same-source machine-readable fallback.
            tables.extend([
                _write_table(out, "openrouter_token_volume_weekly", volume_rows),
                _write_table(out, "openrouter_author_share_100", author_rows),
                _write_table(out, "openrouter_model_leaderboard", leaderboard.get("rows") or []),
                _write_table(out, "openrouter_model_ranking_series", model_ranking.get("rows") or []),
                _write_table(out, "openrouter_concentration", concentration_rows),
            ])
            return {"descriptors": [], "tables": tables, "font_path": "",
                    "glyph_status": glyph_report("OpenRouter token economy", font_path=""),
                    "visualization_warning": "openrouter: 未找到通过 glyph 检查的中文字体，已降级为同源 CSV/JSON 表格。"}
        periods = [str(row["period"]) for row in volume_rows]
        dates = np.arange(len(periods))
        # 1. total volume + 4-week average
        rows = [{"period": row["period"], "total_tokens": row["total_tokens"],
                 "moving_average_4w": (sum(r["total_tokens"] for r in volume_rows[max(0, i-3):i+1]) / min(i+1, 4))}
                for i, row in enumerate(volume_rows)]
        tables.append(_write_table(out, "openrouter_token_volume_weekly", rows))
        fig, ax = plt.subplots(figsize=(11, 5), dpi=150)
        ax.bar(dates, [r["total_tokens"] / 1e12 for r in rows], color="#6c8cff", label="Weekly tokens (T)")
        ax.plot(dates, [r["moving_average_4w"] / 1e12 for r in rows], color="#e67e22", marker="o", label="4-week average")
        ax.set_title("OpenRouter public routed token volume")
        ax.set_ylabel("Trillion tokens")
        ax.set_xticks(dates[::max(1, len(dates)//8)], periods[::max(1, len(dates)//8)], rotation=35, ha="right")
        ax.legend(); ax.grid(axis="y", alpha=.25); fig.tight_layout()
        path = out / "openrouter_token_volume_weekly.png"; fig.savefig(path); plt.close(fig)
        descriptors.append(_sidecar(path, "OpenRouter public routed token volume", "Sum of accepted daily total_tokens over complete UTC weeks; line is four-week moving average.", "tokens", rows, bundle.get("manifest"), font_path, source_as_of, derivation_version))

        # stable author set: top eight across latest 12 weeks + Other + unknown
        last_periods = periods[-12:]
        subset = [r for r in author_rows if r.get("period") in last_periods]
        sums = {}
        for row in subset: sums[row["author"]] = sums.get(row["author"], 0) + int(row.get("tokens", 0))
        selected = [a for a, _ in sorted(sums.items(), key=lambda kv: (-kv[1], kv[0])) if a not in {"unattributed_other", "unknown_author"}][:8]
        selected += [a for a in ("unknown_author", "unattributed_other") if a in sums]
        colors = {author: f"#{hashlib.md5(author.encode()).hexdigest()[:6]}" for author in selected}
        # Absolute author tokens and token shares carry nearly identical
        # information in the review.  Keep the absolute values in the
        # governed DataProduct, but publish only the normalized share chart.
        for normalized in (True,):
            chart_rows, matrix = [], []
            for period in last_periods:
                cells = {r["author"]: r for r in author_rows if r.get("period") == period}
                total = sum(int(r.get("tokens", 0)) for r in cells.values())
                values = {a: int(cells.get(a, {}).get("tokens", 0)) for a in selected}
                values["other_authors"] = max(0, total - sum(values.values()))
                denom = total if normalized else 1
                for author, token in values.items():
                    value = token / denom if denom else 0
                    matrix.append({"period": period, "author": author, "tokens": token, "share": value})
                    chart_rows.append({"period": period, "author": author, "tokens": token, "share": value})
            slug = "openrouter_author_share_100"
            tables.append(_write_table(out, slug, chart_rows))
            fig, ax = plt.subplots(figsize=(11, 5), dpi=150)
            bottom = np.zeros(len(last_periods))
            for author in selected + ["other_authors"]:
                values = [next((r["share"] if normalized else r["tokens"] / 1e12 for r in matrix if r["period"] == p and r["author"] == author), 0) for p in last_periods]
                ax.bar(np.arange(len(last_periods)), values, bottom=bottom, label=author, color=colors.get(author, "#b8b8b8"))
                bottom += np.array(values)
            ax.set_title("OpenRouter author token share" if normalized else "OpenRouter author tokens")
            ax.set_ylabel("Share" if normalized else "Trillion tokens")
            ax.set_xticks(np.arange(len(last_periods)), last_periods, rotation=35, ha="right")
            if normalized: ax.set_ylim(0, 1); ax.yaxis.set_major_formatter(lambda value, pos: f"{value:.0%}")
            ax.legend(ncol=3, fontsize=8, loc="upper left"); ax.grid(axis="y", alpha=.2); fig.tight_layout()
            path = out / f"{slug}.png"; fig.savefig(path); plt.close(fig)
            descriptors.append(_sidecar(path, ax.get_title(), "Author tokens and token share; same author set and colors across both charts.", "share" if normalized else "tokens", chart_rows, bundle.get("manifest"), font_path, source_as_of, derivation_version))

        # 4. model ranking and token-volume history.  The stable model set is
        # selected by cumulative tokens; each line is ranked against all
        # models in that week, so rank movement and volume growth are visible
        # in the same review artifact.
        model_rows = leaderboard.get("rows") or []
        ranking = bundle.get("model_ranking") or {}
        ranking_rows = ranking.get("rows") or []
        tables.append(_write_table(out, "openrouter_model_leaderboard", model_rows))
        tables.append(_write_table(out, "openrouter_model_ranking_series", ranking_rows))
        ranking_periods = list(ranking.get("periods") or periods)
        selected_models = list(ranking.get("selected_models") or [])
        if ranking_rows and selected_models:
            by_period_model = {(str(row.get("period")), str(row.get("model_permaslug"))): row
                               for row in ranking_rows}
            label_map = {str(row.get("model_permaslug")): str(row.get("model_name") or row.get("model_permaslug"))
                         for row in ranking_rows if not row.get("is_other_series")}
            color_map = {model: f"#{hashlib.md5(model.encode()).hexdigest()[:6]}" for model in selected_models}
            x = np.arange(len(ranking_periods))
            fig, (token_ax, rank_ax) = plt.subplots(
                2, 1, figsize=(12, 8), dpi=150, sharex=True,
                gridspec_kw={"height_ratios": [2.2, 1]},
            )
            for model in selected_models + ["__other__"]:
                values = [by_period_model.get((period, model), {}).get("tokens", 0) / 1e12
                          for period in ranking_periods]
                bottom = getattr(token_ax, "_openrouter_bottom", np.zeros(len(ranking_periods)))
                token_ax.bar(x, values, bottom=bottom, width=0.86,
                             label=("Other / unselected" if model == "__other__" else label_map.get(model, model)),
                             color=("#b8b8b8" if model == "__other__" else color_map[model]))
                token_ax._openrouter_bottom = bottom + np.asarray(values)
                if model != "__other__":
                    ranks = [by_period_model.get((period, model), {}).get("rank", np.nan)
                             for period in ranking_periods]
                    rank_ax.plot(x, ranks, marker="o", markersize=3, linewidth=1.4,
                                 label=label_map.get(model, model), color=color_map[model])
            token_ax.set_title("OpenRouter model ranking and token volume")
            token_ax.set_ylabel("Trillion tokens")
            token_ax.legend(ncol=3, fontsize=7, loc="upper left")
            token_ax.grid(axis="y", alpha=.2)
            rank_ax.set_ylabel("Rank (1 = highest)")
            rank_ax.invert_yaxis()
            rank_ax.set_ylim(len(selected_models) + 1, 0.5)
            rank_ax.set_yticks(range(1, len(selected_models) + 1))
            rank_ax.grid(alpha=.2)
            step = max(1, len(ranking_periods) // 8)
            ticks = list(range(0, len(ranking_periods), step))
            if ticks[-1] != len(ranking_periods) - 1:
                ticks.append(len(ranking_periods) - 1)
            rank_ax.set_xticks(ticks, [ranking_periods[i] for i in ticks], rotation=35, ha="right")
            fig.tight_layout()
            path = out / "openrouter_model_leaderboard.png"; fig.savefig(path); plt.close(fig)
            descriptors.append(_sidecar(
                path, "OpenRouter model ranking and token volume",
                "Top models selected by cumulative tokens from complete weeks starting 2026-01-01; dated releases of the same provider/model version are merged. Stacked bars show weekly token volume and lines show weekly rank against all grouped models.",
                "tokens", ranking_rows, bundle.get("manifest"), font_path, source_as_of, derivation_version))

        # 5. concentration
        tables.append(_write_table(out, "openrouter_concentration", concentration_rows))
        fig, ax = plt.subplots(figsize=(11, 5), dpi=150)
        concentration_periods = periods[-len(concentration_rows):]
        x = np.arange(len(concentration_rows))
        ax.plot(x, [r.get("top3_share", 0) for r in concentration_rows], marker="o", label="Top-3")
        ax.plot(x, [r.get("top5_share", 0) for r in concentration_rows], marker="o", label="Top-5")
        ax.plot(x, [r.get("hhi", 0) for r in concentration_rows], marker="o", label="HHI")
        step = max(1, len(concentration_periods) // 8)
        ticks = list(range(0, len(concentration_periods), step))
        if ticks[-1] != len(concentration_periods) - 1:
            ticks.append(len(concentration_periods) - 1)
        ax.set_title("OpenRouter author concentration")
        ax.set_ylabel("Share / HHI (0–1)")
        ax.set_ylim(0, 1)
        ax.set_xticks(ticks, [concentration_periods[i] for i in ticks], rotation=35, ha="right")
        ax.yaxis.set_major_formatter(lambda value, pos: f"{value:.2f}")
        ax.legend(); ax.grid(alpha=.25); fig.tight_layout()
        path = out / "openrouter_concentration.png"; fig.savefig(path); plt.close(fig)
        descriptors.append(_sidecar(path, "OpenRouter author concentration", "Top-3, Top-5 and HHI over complete UTC weeks; unattributed Other is not reverse-assigned.", "ratio", concentration_rows, bundle.get("manifest"), font_path, source_as_of, derivation_version))
        return {"descriptors": descriptors, "tables": tables, "font_path": font_path,
                "glyph_status": glyph_report("OpenRouter token economy", font_path=font_path)}
    except Exception as exc:
        return {"descriptors": [], "tables": tables, "visualization_warning": f"openrouter renderer failed: {type(exc).__name__}: {exc}"}
