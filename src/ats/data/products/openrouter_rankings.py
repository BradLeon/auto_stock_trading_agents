"""Governed OpenRouter token-economy DataProducts.

All outputs are derived from accepted daily token observations.  They are
usage/competition evidence for the OpenRouter public routed cohort, not global
revenue estimates and not request-share estimates.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Iterable

SOURCE_ID = "openrouter_rankings"
DATASET_ID = "openrouter_rankings_daily"
DERIVATION_VERSION = "openrouter_token_economy/v1"
MODEL_RANKING_START_DATE = "2026-01-01"


def _dims(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("dimensions")
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(row.get("dimensions_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _period(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _month_start(day: date) -> date:
    return day.replace(day=1)


def canonical_model_group(model_permaslug: Any) -> tuple[str, str]:
    """Merge dated model releases into a stable provider/model-version label."""
    raw = str(model_permaslug or "").strip().casefold()
    if raw in {"", "other", "unknown"}:
        return "other", "Other"
    route = raw.split(":", 1)[0]
    provider, _, name = route.partition("/")
    name = re.sub(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$", "", name)
    name = name or route
    return f"{provider}/{name}", name


def _hash(rows: Iterable[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(list(rows), ensure_ascii=False, sort_keys=True,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def _source_as_of(rows: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for row in rows:
        raw = row.get("raw_payload")
        if not raw:
            continue
        try:
            body = json.loads(raw) if isinstance(raw, str) else raw
            meta = body.get("meta") if isinstance(body, dict) else {}
            if meta and meta.get("as_of"):
                values.append(str(meta["as_of"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return max(values, default="")


def _query(products, *, as_of=None, include_vintages: bool = False,
           start_date: str = "", end_date: str = "") -> list[dict[str, Any]]:
    rows = products.structured.observations(
        dataset_id=DATASET_ID, source_id=SOURCE_ID, as_of=as_of,
        latest_only=not include_vintages, accepted_only=True, limit=2_000_000)
    if start_date:
        rows = [row for row in rows if str(row.get("period", ""))[:10] >= start_date[:10]]
    if end_date:
        rows = [row for row in rows if str(row.get("period", ""))[:10] <= end_date[:10]]
    return rows


def _complete_week_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_week: dict[str, list[dict[str, Any]]] = defaultdict(list)
    days: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        try:
            day = _period(row.get("period"))
        except (TypeError, ValueError):
            continue
        week = _week_start(day).isoformat()
        by_week[week].append(row)
        days[week].add(day.isoformat())
    return {week: values for week, values in by_week.items() if len(days[week]) == 7}


def _base_result(rows: list[dict[str, Any]], *, weeks: list[str], quality: dict[str, Any],
                 as_of=None, include_vintages: bool = False, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    ids = sorted({str(row.get("observation_id")) for row in rows if row.get("observation_id")})
    artifacts = sorted({str(row.get("artifact_id")) for row in rows if row.get("artifact_id")})
    latest = max(weeks, default="")
    regimes = sorted({_dims(row).get("methodology_regime", "") for row in rows if _dims(row).get("methodology_regime")})
    return {
        "status": "ok" if rows and weeks else "no_coverage", "source_id": SOURCE_ID,
        "dataset_id": DATASET_ID, "periods": weeks, "latest_period": latest,
        "complete_periods": weeks, "quality": quality,
        "methodology_regimes": regimes,
        "freshness": {"latest_observation_period": max((str(r.get("period", "")) for r in rows), default=""),
                       "source_native_cadence": "daily_utc", "source_as_of": _source_as_of(rows)},
        "source_as_of": _source_as_of(rows),
        "lineage": {"derivation_version": DERIVATION_VERSION, "input_observation_ids": ids,
                    "artifact_ids": artifacts, "input_observation_ids_hash": _hash(ids)},
        "limitations": ["OpenRouter public routed token volume, not global market volume or revenue.",
                        "Token counts combine prompt and completion tokens; model tokenizers differ.",
                        "Other is not reverse-assigned to authors; unknown authors remain unknown."],
        "vintage_mode": "all_revisions" if include_vintages else "current",
        "manifest": manifest,
    }


def openrouter_token_volume_series(products, *, start_date: str = "", end_date: str = "",
                                   as_of=None, include_vintages: bool = False,
                                   as_frame: bool = False) -> dict[str, Any]:
    rows = _query(products, as_of=as_of, include_vintages=include_vintages,
                  start_date=start_date, end_date=end_date)
    regimes = sorted({_dims(row).get("methodology_regime", "") for row in rows if _dims(row).get("methodology_regime")})
    if len(regimes) > 1:
        result = _base_result(rows, weeks=[], quality={"status": "methodology_drift", "regimes": regimes},
                              as_of=as_of, include_vintages=include_vintages)
        result.update({"rows": [], "monthly_rows": [], "unit": "tokens",
                       "metric_definition": "not derived across methodology regimes"})
        return result
    weeks = _complete_week_rows(rows)
    output: list[dict[str, Any]] = []
    for week, values in sorted(weeks.items()):
        daily: dict[str, int] = defaultdict(int)
        models: dict[str, int] = defaultdict(int)
        authors: dict[str, int] = defaultdict(int)
        obs: set[str] = set()
        for row in values:
            token = int(float(row.get("value") or 0))
            dims = _dims(row)
            day = str(row.get("period"))[:10]
            daily[day] += token
            models[str(dims.get("model_permaslug") or row.get("entity_id"))] += token
            authors[str(dims.get("author") or "unknown_author")] += token
            if row.get("observation_id"):
                obs.add(str(row["observation_id"]))
        output.append({"period": week, "week_end": (date.fromisoformat(week) + timedelta(days=6)).isoformat(),
                       "total_tokens": sum(daily.values()), "model_tokens": dict(models),
                       "author_tokens": dict(authors), "daily_tokens": dict(daily),
                       "observation_ids": sorted(obs), "complete": True})
    for index, item in enumerate(output):
        recent = output[max(0, index - 3): index + 1]
        item["moving_average_4w"] = sum(row["total_tokens"] for row in recent) / len(recent)
    # Monthly output is a convenience view over the same accepted daily facts;
    # formal direction remains complete-week based, so an open month is marked
    # incomplete and never enters ``rows``.
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        try:
            day = _period(row.get("period"))
        except (TypeError, ValueError):
            continue
        by_month[_month_start(day).isoformat()].append(row)
    monthly_rows = []
    for month, values in sorted(by_month.items()):
        daily_days = {_period(row.get("period")).isoformat() for row in values}
        daily = sum(int(float(row.get("value") or 0)) for row in values)
        models: dict[str, int] = defaultdict(int)
        authors: dict[str, int] = defaultdict(int)
        for row in values:
            dims = _dims(row)
            token = int(float(row.get("value") or 0))
            models[str(dims.get("model_permaslug") or row.get("entity_id"))] += token
            authors[str(dims.get("author") or "unknown_author")] += token
        next_month = (date.fromisoformat(month).replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = next_month - timedelta(days=1)
        monthly_rows.append({"period": month, "month_end": month_end.isoformat(),
                             "total_tokens": daily, "model_tokens": dict(models),
                             "author_tokens": dict(authors), "day_count": len(daily_days),
                             "complete": len(daily_days) == month_end.day})
    quality = {"status": "accepted" if output else "no_coverage", "complete_week_count": len(output),
               "excluded_incomplete_days": sorted(set(str(r.get("period", ""))[:10] for r in rows)
                                                  - {d for item in output for d in item["daily_tokens"]})}
    result = _base_result(rows, weeks=sorted(weeks), quality=quality, as_of=as_of,
                          include_vintages=include_vintages,
                          manifest=(products.snapshot_manifest(
                              consumer="evidence_observer", purpose="openrouter_token_volume_series",
                              as_of=as_of or datetime.now(timezone.utc), rows=[dict(row, selected_source=SOURCE_ID,
                                                                                    derivation_version=DERIVATION_VERSION)
                                                                              for row in rows],
                              metadata={"complete_weeks": sorted(weeks), "include_vintages": include_vintages}) if rows and hasattr(products, "snapshot_manifest") else None))
    result.update({"rows": output, "monthly_rows": monthly_rows, "unit": "tokens",
                   "metric_definition": "sum daily total_tokens by complete UTC week",
                   "monthly_metric_definition": "sum daily total_tokens by calendar month; incomplete month is audit-only"})
    if as_frame:
        import pandas as pd
        frame = pd.DataFrame(output)
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


def _weekly_author_rows(products, **kwargs) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    volume = openrouter_token_volume_series(products, **kwargs)
    rows = []
    for week in volume.get("rows") or []:
        for author, tokens in sorted((week.get("author_tokens") or {}).items()):
            rows.append({"period": week["period"], "author": author, "tokens": tokens,
                         "share": tokens / week["total_tokens"] if week["total_tokens"] else 0.0,
                         "total_tokens": week["total_tokens"], "observation_ids": week.get("observation_ids", [])})
    return volume, rows


def openrouter_author_share_series(products, *, start_date: str = "", end_date: str = "",
                                   as_of=None, include_vintages: bool = False,
                                   as_frame: bool = False) -> dict[str, Any]:
    volume, output = _weekly_author_rows(products, start_date=start_date, end_date=end_date,
                                         as_of=as_of, include_vintages=include_vintages)
    result = dict(volume, rows=output, unit="tokens_and_share",
                  metric_definition="author weekly tokens / total complete-week tokens")
    result["coverage"] = {"top_n_plus_other": True, "authors": sorted({r["author"] for r in output}),
                           "other_bucket": "unattributed_other"}
    if as_frame:
        import pandas as pd
        frame = pd.DataFrame(output)
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


def openrouter_model_leaderboard(products, *, period: str = "", top_n: int = 10,
                                 as_of=None, include_vintages: bool = False,
                                 as_frame: bool = False) -> dict[str, Any]:
    rows = _query(products, as_of=as_of, include_vintages=include_vintages)
    regimes = sorted({_dims(row).get("methodology_regime", "") for row in rows if _dims(row).get("methodology_regime")})
    if len(regimes) > 1:
        result = _base_result(rows, weeks=[], quality={"status": "methodology_drift", "regimes": regimes},
                              as_of=as_of, include_vintages=include_vintages)
        result.update({"rows": [], "period": "", "previous_period": "", "total_tokens": 0,
                       "previous_total_tokens": 0, "top_n": top_n})
        return result
    weeks = _complete_week_rows(rows)
    selected = period or (max(weeks) if weeks else "")
    values = weeks.get(selected, [])
    ordered_weeks = sorted(weeks)
    previous = ordered_weeks[ordered_weeks.index(selected) - 1] if selected in ordered_weeks and ordered_weeks.index(selected) > 0 else ""

    def aggregate(period_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], int]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in period_rows:
            dims = _dims(row)
            model = str(dims.get("model_permaslug") or row.get("entity_id"))
            cell = grouped.setdefault(model, {"model_permaslug": model,
                                             "model_name": dims.get("model_name", model),
                                             "author": dims.get("author", "unknown_author"), "tokens": 0,
                                             "is_free_route": bool(dims.get("is_free_route")),
                                             "observation_ids": []})
            cell["tokens"] += int(float(row.get("value") or 0))
            if row.get("observation_id"):
                cell["observation_ids"].append(row["observation_id"])
        return grouped, sum(item["tokens"] for item in grouped.values())

    grouped, total = aggregate(values)
    previous_grouped, previous_total = aggregate(weeks.get(previous, []))
    output = sorted(grouped.values(), key=lambda item: (-item["tokens"], item["model_permaslug"]))[:top_n]
    for item in output:
        item["share"] = item["tokens"] / total if total else 0.0
        previous_item = previous_grouped.get(item["model_permaslug"], {})
        item["previous_tokens"] = previous_item.get("tokens", 0)
        item["previous_share"] = (item["previous_tokens"] / previous_total
                                   if previous_total else 0.0)
        item["token_change"] = item["tokens"] - item["previous_tokens"]
        item["share_change"] = item["share"] - item["previous_share"]
        item["period"] = selected
        item["free_route_label"] = "free" if item["is_free_route"] else "paid/unknown"
    result = _base_result(rows, weeks=sorted(weeks), quality={"status": "accepted" if output else "no_coverage", "top_n": top_n},
                          as_of=as_of, include_vintages=include_vintages)
    result.update({"rows": output, "period": selected, "previous_period": previous,
                   "total_tokens": total, "previous_total_tokens": previous_total, "top_n": top_n})
    if as_frame:
        import pandas as pd
        frame = pd.DataFrame(output)
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


def openrouter_model_ranking_series(products, *, top_n: int = 10,
                                    start_date: str = MODEL_RANKING_START_DATE,
                                    end_date: str = "",
                                    as_of=None, include_vintages: bool = False,
                                    as_frame: bool = False) -> dict[str, Any]:
    """Return weekly model token volume and rank trajectories.

    The stable model set is selected by cumulative tokens across the selected
    complete history, while ``rank`` is calculated against every grouped model
    in each week. Dated releases are merged to the same model-version label.
    The residual ``other_series`` row keeps the stacked token volume additive
    without pretending that the official ``Other`` bucket belongs to a model.
    """
    rows = _query(products, as_of=as_of, include_vintages=include_vintages,
                 start_date=start_date, end_date=end_date)
    volume = openrouter_token_volume_series(products, start_date=start_date,
                                            end_date=end_date, as_of=as_of,
                                            include_vintages=include_vintages)
    periods = list(volume.get("periods") or [])
    if volume.get("status") != "ok" or not periods:
        result = _base_result(rows, weeks=[], quality={"status": "no_coverage"},
                              as_of=as_of, include_vintages=include_vintages)
        result.update({"rows": [], "selected_models": [], "latest_rows": [],
                       "top_n": top_n, "start_date": start_date or "",
                       "grouping": "provider/model version with trailing release date removed",
                       "metric_definition":
                       "weekly grouped model token volume and rank over complete UTC weeks"})
        return result

    labels: dict[str, dict[str, Any]] = {}
    variants: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        dims = _dims(row)
        slug = str(dims.get("model_permaslug") or row.get("entity_id") or "")
        group, display_name = canonical_model_group(slug)
        variants[group].add(slug)
        current = labels.setdefault(group, {"model_name": display_name,
                                             "author": dims.get("author", "unknown_author"),
                                             "is_free_route": False,
                                             "is_other": bool(dims.get("is_other"))})
        current["is_free_route"] = bool(current.get("is_free_route")) or bool(dims.get("is_free_route"))

    cumulative: dict[str, int] = defaultdict(int)
    for week in volume.get("rows") or []:
        grouped_tokens: dict[str, int] = defaultdict(int)
        for slug, tokens in (week.get("model_tokens") or {}).items():
            group, _display_name = canonical_model_group(slug)
            if group != "other":
                grouped_tokens[group] += int(tokens)
        for group, tokens in grouped_tokens.items():
            cumulative[group] += tokens
    selected = [slug for slug, _ in sorted(cumulative.items(), key=lambda item: (-item[1], item[0]))[:top_n]]
    selected_set = set(selected)
    output: list[dict[str, Any]] = []
    latest_rows: list[dict[str, Any]] = []
    for week in volume.get("rows") or []:
        model_tokens: dict[str, int] = defaultdict(int)
        for slug, tokens in (week.get("model_tokens") or {}).items():
            group, _display_name = canonical_model_group(slug)
            model_tokens[group] += int(tokens)
        total = int(week.get("total_tokens") or 0)
        ranked = sorted(((slug, tokens) for slug, tokens in model_tokens.items()
                         if slug != "other"),
                        key=lambda item: (-item[1], item[0]))
        rank_by_slug = {slug: rank for rank, (slug, _tokens) in enumerate(ranked, start=1)}
        rows_for_week: list[dict[str, Any]] = []
        for slug in selected:
            tokens = model_tokens.get(slug, 0)
            meta = labels.get(slug, {})
            item = {"period": week["period"], "model_permaslug": slug,
                    "model_group": slug,
                    "model_name": meta.get("model_name", slug),
                    "author": meta.get("author", "unknown_author"),
                    "tokens": tokens, "share": tokens / total if total else 0.0,
                    "rank": rank_by_slug.get(slug),
                    "is_free_route": bool(meta.get("is_free_route")),
                    "model_variants": sorted(variants.get(slug, set())),
                    "is_other_series": False}
            rows_for_week.append(item)
            output.append(item)
        selected_tokens = sum(item["tokens"] for item in rows_for_week)
        output.append({"period": week["period"], "model_permaslug": "__other__",
                       "model_group": "__other__",
                       "model_name": "Other / unselected", "author": "—",
                       "tokens": max(0, total - selected_tokens),
                       "share": max(0, total - selected_tokens) / total if total else 0.0,
                       "rank": None, "is_free_route": False, "model_variants": [],
                       "is_other_series": True})
        if week["period"] == periods[-1]:
            latest_rows = rows_for_week
    result = _base_result(rows, weeks=periods,
                          quality={"status": "accepted", "top_n": top_n,
                                   "selected_by": "cumulative_tokens"},
                          as_of=as_of, include_vintages=include_vintages)
    result.update({"rows": output, "selected_models": selected,
                   "latest_rows": latest_rows, "latest_period": periods[-1],
                   "top_n": top_n,
                   "metric_definition": "weekly grouped model token volume and rank over complete UTC weeks",
                   "grouping": "provider/model version with trailing release date removed",
                   "start_date": start_date or ""})
    if as_frame:
        import pandas as pd
        frame = pd.DataFrame(output)
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


def openrouter_concentration_series(products, *, start_date: str = "", end_date: str = "",
                                    as_of=None, include_vintages: bool = False,
                                    as_frame: bool = False) -> dict[str, Any]:
    volume, authors = _weekly_author_rows(products, start_date=start_date, end_date=end_date,
                                           as_of=as_of, include_vintages=include_vintages)
    out = []
    for week in volume.get("rows") or []:
        named = {a: int(t) for a, t in (week.get("author_tokens") or {}).items() if a != "unattributed_other"}
        vals = sorted(named.values(), reverse=True)
        total = week["total_tokens"] or 0
        out.append({"period": week["period"], "total_tokens": total,
                    "top3_share": sum(vals[:3]) / total if total else 0.0,
                    "top5_share": sum(vals[:5]) / total if total else 0.0,
                    "hhi": sum((value / total) ** 2 for value in vals) if total else 0.0,
                    "other_share": (week.get("author_tokens") or {}).get("unattributed_other", 0) / total if total else 0.0,
                    "unknown_author_share": (week.get("author_tokens") or {}).get("unknown_author", 0) / total if total else 0.0,
                    "limitation": "HHI excludes unattributed_other from the named-author concentration numerator."})
    result = dict(volume, rows=out, metric_definition="Top-3/Top-5 author share and HHI over complete UTC weeks")
    if as_frame:
        import pandas as pd
        frame = pd.DataFrame(out)
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


def _trend(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    if len(rows) < 8:
        return {"status": "insufficient_history", "complete_week_count": len(rows)}
    recent = rows[-4:]
    prior = rows[-8:-4]
    old = sum(float(r.get(key, 0)) for r in prior) / 4
    new = sum(float(r.get(key, 0)) for r in recent) / 4
    change = (new / old - 1) if old else None
    deltas = []
    for previous_row, current_row in zip(rows[-5:-1], rows[-4:]):
        previous_value = float(previous_row.get(key, 0))
        current_value = float(current_row.get(key, 0))
        if previous_value:
            deltas.append(current_value / previous_value - 1)
    if any(item > 0 for item in deltas) and any(item < 0 for item in deltas):
        status = "mixed"
    else:
        status = "stable" if change is None or abs(change) < 0.1 else ("expanding" if change > 0 else "contracting")
    return {"status": status, "latest_4_week_average": new, "prior_4_week_average": old,
            "change_rate": change, "recent_periods": [r.get("period") for r in recent],
            "prior_periods": [r.get("period") for r in prior]}


def openrouter_token_evidence_bundle(products, *, as_of=None, snapshot_consumer: str = "",
                                     snapshot_purpose: str = "") -> dict[str, Any]:
    volume = openrouter_token_volume_series(products, as_of=as_of)
    authors = openrouter_author_share_series(products, as_of=as_of)
    leaderboard = openrouter_model_leaderboard(products, as_of=as_of)
    model_ranking = openrouter_model_ranking_series(
        products, as_of=as_of, top_n=10, start_date=MODEL_RANKING_START_DATE)
    concentration = openrouter_concentration_series(products, as_of=as_of)
    total_trend = _trend(volume.get("rows") or [], "total_tokens")
    latest_authors = [row for row in authors.get("rows") or [] if row.get("period") == authors.get("latest_period")]
    latest_authors.sort(key=lambda row: -row.get("tokens", 0))
    warnings = list(volume.get("limitations") or [])
    if volume.get("status") != "ok":
        warnings.append("OpenRouter 没有通过质量门的完整 UTC 周数据；不影响 Frontier Labs 收入段。")
    elif total_trend.get("status") == "insufficient_history":
        warnings.append("OpenRouter 完整 UTC 周少于 8 周；仅展示水平和榜单，不判断持续扩大或收缩。")
    facts = {"latest_complete_week": volume.get("latest_period"),
             "latest_total_tokens": (volume.get("rows") or [{}])[-1].get("total_tokens") if volume.get("rows") else None,
             "trend": total_trend, "top_authors": latest_authors[:10],
             # Keep the latest model evidence aligned with the grouped, 2026+
             # ranking series shown in the report.  The raw leaderboard remains
             # available in the bundle for callers that need the ungrouped view.
             "top_models": model_ranking.get("latest_rows") or leaderboard.get("rows") or [],
             "model_ranking_start_date": MODEL_RANKING_START_DATE,
             "concentration": (concentration.get("rows") or [{}])[-1] if concentration.get("rows") else {},
             "observation_count": len(volume.get("lineage", {}).get("input_observation_ids", []))}
    bundle_status = "unavailable"
    if volume.get("status") == "ok":
        bundle_status = ("insufficient_history" if total_trend.get("status") == "insufficient_history"
                         else "ok")
    return {"status": bundle_status,
            "section_id": "openrouter_routed_usage_and_competition",
            "section_claim_text": "OpenRouter 公共路由 token 用量是否持续扩大，并在模型厂商之间扩散或集中？",
            "volume": volume, "authors": authors, "leaderboard": leaderboard,
            "model_ranking": model_ranking,
            "concentration": concentration, "facts": facts, "warnings": sorted(set(warnings)),
            "lineage": volume.get("lineage"), "manifest": volume.get("manifest"),
            "derivation_version": DERIVATION_VERSION,
            "snapshot_consumer": snapshot_consumer, "snapshot_purpose": snapshot_purpose,
            "directional_status": total_trend.get("status", "unavailable")}


__all__ = ["openrouter_token_volume_series", "openrouter_author_share_series",
           "openrouter_model_leaderboard", "openrouter_model_ranking_series",
           "openrouter_concentration_series",
           "openrouter_token_evidence_bundle"]
