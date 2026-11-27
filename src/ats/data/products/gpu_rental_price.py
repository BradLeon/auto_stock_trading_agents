"""GPU rental price series, monthly values and coverage status, known at a point in time.

One source and one rental form per series.  The on-demand series is the reading and
alone decides coverage; spot and 12-month reservation series travel beside it as
corroboration.  A trend is stated only from enough consecutive complete months of
the same series; otherwise the status says why.
A reader must treat `uncovered`, `stale` and `insufficient_history` as having no
reading, never as an unchanged price.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from statistics import median
from typing import Any

from ..sources.getdeploying_gpu_prices import (
    ATTRIBUTION,
    DATASET_ID,
    LICENSE,
    METHODOLOGY_REGIME,
    RENTAL_FORM,
    SOURCE_ID,
    UNIT,
    dataset_settings,
)

METRIC_ID = "ai.gpu.rental.listed_price"
PRICE_NOTE = ("各供应商挂牌价中位数的跨供应商中位数，不是成交价；供应商进出会改变读数。"
              "月值取该月各周读数的中位数，只用整月已结束的月份。")
CORROBORATION_NOTE = ("按需价是判读用价；现货价与 12 个月预订价只作旁证，不用于触发趋势规则，也不决定覆盖状态。"
                      "各租赁形态的供应商组成不同，形态之间的价差不是同一批供应商的比价。")
STATUS_LABELS = {
    "ok": "可读", "covered": "已覆盖", "uncovered": "未覆盖", "stale": "数据过期",
    "insufficient_history": "历史不足", "methodology_drift": "口径变化",
}


def _mapping(row: dict[str, Any], name: str, stored_as: str) -> dict[str, Any]:
    """A parsed field of one observation row, whether returned decoded or as stored text."""
    value = row.get(name)
    if not isinstance(value, dict):
        value = json.loads(row.get(stored_as) or "{}")
    return value


def _last_monday(year: int, month: int) -> date:
    following = date(year + month // 12, month % 12 + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=last.weekday())


def _months_apart(earlier: str, later: str) -> int:
    first, second = date.fromisoformat(f"{earlier}-01"), date.fromisoformat(f"{later}-01")
    return (second.year - first.year) * 12 + second.month - first.month


def monthly_values(weekly: list[dict[str, Any]], *, collected_through: date,
                   min_weeks: int) -> list[dict[str, Any]]:
    """Months whose every week has ended and which hold at least `min_weeks` readings."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in weekly:
        grouped.setdefault(item["week"][:7], []).append(item)
    months = []
    for month, items in sorted(grouped.items()):
        year, number = (int(part) for part in month.split("-"))
        if _last_monday(year, number) + timedelta(days=7) > collected_through:
            continue
        if len(items) < min_weeks:
            continue
        months.append({"month": month, "value": median(item["price"] for item in items),
                       "weeks": len(items),
                       "min_provider_count": min(item["provider_count"] for item in items)})
    return months


def monthly_trend(months: list[dict[str, Any]], *, required: int,
                  thin_sample_providers: int) -> dict[str, Any]:
    """Direction over the latest `required` consecutive months, or why there is none."""
    run: list[dict[str, Any]] = []
    for item in reversed(months):
        if run and _months_apart(item["month"], run[0]["month"]) != 1:
            break
        run.insert(0, item)
    trend: dict[str, Any] = {
        "status": "insufficient_history", "status_label": STATUS_LABELS["insufficient_history"],
        "required_months": required, "consecutive_months": len(run), "direction": "",
        "months": [], "monthly_changes": [], "consecutive_declines": None, "thin_sample": None,
    }
    if len(run) < required:
        return trend
    used = run[-required:]
    changes = [later["value"] / earlier["value"] - 1 for earlier, later in pairwise(used)]
    if all(change < 0 for change in changes):
        direction = "down"
    elif all(change > 0 for change in changes):
        direction = "up"
    else:
        direction = "mixed"
    declines = 0
    for earlier, later in zip(reversed(run[:-1]), reversed(run[1:])):
        if later["value"] >= earlier["value"]:
            break
        declines += 1
    trend.update(
        status="ok", status_label=STATUS_LABELS["ok"], direction=direction,
        months=[item["month"] for item in used], monthly_changes=changes,
        consecutive_declines=declines,
        thin_sample=any(item["min_provider_count"] < thin_sample_providers for item in used))
    return trend


def _series(slug: str, form: str, role: str, items: list[dict[str, Any]], *, today: date,
            collected: date, rules: dict[str, Any]) -> dict[str, Any]:
    """One model and rental form: weekly readings, monthly values, trend and own status."""
    head = {"gpu_model": slug, "rental_form": form, "role": role}
    if not items:
        return {**head, "status": "uncovered", "status_label": STATUS_LABELS["uncovered"],
                "latest": None, "weekly": [], "monthly": [], "trend": None}
    items = sorted(items, key=lambda item: item["week"])
    age = (today - date.fromisoformat(items[-1]["week"])).days
    status = "stale" if age > int(rules["stale_after_days"]) else "covered"
    months = monthly_values(items, collected_through=collected,
                            min_weeks=int(rules["min_weeks_per_month"]))
    trend = monthly_trend(months, required=int(rules["months"]),
                          thin_sample_providers=int(rules["thin_sample_providers"]))
    if status == "stale":
        trend.update(status="stale", status_label=STATUS_LABELS["stale"], direction="",
                     monthly_changes=[], consecutive_declines=None)
    return {**head, "status": status, "status_label": STATUS_LABELS[status], "latest": items[-1],
            "weekly": items, "monthly": months, "trend": trend}


def rental_price(products, *, gpu_model: str = "", as_of: datetime | None = None
                 ) -> dict[str, Any]:
    settings = dataset_settings()
    rules = settings["trend"]
    supporting = list(settings["corroboration_forms"])
    stored = products.structured.observations(
        dataset_id=DATASET_ID, source_id=SOURCE_ID, as_of=as_of, latest_only=True,
        accepted_only=True, limit=2_000_000)
    packet: dict[str, Any] = {
        "dataset_id": DATASET_ID, "metric_id": METRIC_ID, "source_id": SOURCE_ID,
        "attribution": ATTRIBUTION, "license": LICENSE, "unit": UNIT,
        "rental_form": RENTAL_FORM, "corroboration_forms": supporting,
        "price_note": PRICE_NOTE, "corroboration_note": CORROBORATION_NOTE,
        "methodology_regime": METHODOLOGY_REGIME, "status": "uncovered",
        "status_label": STATUS_LABELS["uncovered"], "reason": "no_observation_known",
        "collected_through": "", "history_start": "", "latest_week": "", "series": [],
    }
    if not stored:
        return packet
    regimes: set[str] = set()
    weekly: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in stored:
        dims = _mapping(row, "dimensions", "dimensions_json")
        raw = _mapping(row, "raw", "raw_payload")
        regimes.add(str(dims.get("methodology_regime") or ""))
        form = str(dims.get("rental_form") or "")
        if form != RENTAL_FORM and form not in supporting:
            continue
        weekly.setdefault((str(dims["gpu_slug"]), form), []).append({
            "week": str(row["period"]), "price": float(row["value"]),
            "offering_median_price": raw.get("offering_median_price"),
            "min_price": raw.get("min_price"), "max_price": raw.get("max_price"),
            "provider_count": int(raw["provider_count"]),
            "offering_count": int(raw["offering_count"]),
            "observation_id": str(row.get("observation_id") or ""),
            "artifact_id": str(row.get("artifact_id") or ""),
            "known_at": str(row.get("known_at") or ""),
        })
    if regimes != {METHODOLOGY_REGIME}:
        packet.update(status="methodology_drift",
                      status_label=STATUS_LABELS["methodology_drift"],
                      reason="regimes:" + ",".join(sorted(regimes)))
        return packet
    primary = [item for (_slug, form), items in weekly.items() if form == RENTAL_FORM
               for item in items]
    if not primary:
        packet["reason"] = f"no_observation_for_form:{RENTAL_FORM}"
        return packet
    # The newest week arrives with every collection, so the latest vintage tells how far
    # the file has been read; weeks that did not change keep their first known time.
    collected = max(datetime.fromisoformat(item["known_at"])
                    for item in primary).astimezone(UTC).date()
    clock = {"today": (as_of or datetime.now(UTC)).astimezone(UTC).date(),
             "collected": collected, "rules": rules}
    series = []
    for slug in settings["gpu_models"]:
        if gpu_model and slug != gpu_model:
            continue
        entry = _series(slug, RENTAL_FORM, "primary", weekly.get((slug, RENTAL_FORM), []), **clock)
        entry["corroboration"] = {
            form: _series(slug, form, "corroboration", weekly.get((slug, form), []), **clock)
            for form in supporting}
        series.append(entry)
    found = [item for item in series if item["weekly"]]
    if not found:
        packet["reason"] = f"no_observation_for:{gpu_model or 'configured_models'}"
        packet["series"] = series
        return packet
    status = "covered" if any(item["status"] == "covered" for item in found) else "stale"
    packet.update(
        status=status, status_label=STATUS_LABELS[status],
        reason="" if status == "covered" else "latest_week_too_old",
        collected_through=collected.isoformat(),
        history_start=min(item["weekly"][0]["week"] for item in found),
        latest_week=max(item["latest"]["week"] for item in found), series=series)
    return packet
