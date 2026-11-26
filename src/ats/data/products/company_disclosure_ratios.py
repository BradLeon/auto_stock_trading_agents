"""Ratios derived from confirmed company disclosures, one row per ratio and quarter end.

Only the default value of each published observation enters (newest filing, see ``company_disclosure_metrics``), and
every row names the observations it used. A missing input makes the row ``not_computable`` and says which one; nothing
is interpolated, carried forward or replaced by a neighbouring basis. In particular the all-in commitments total is
never substituted by the contractual-obligations table total, which covers less.

The one value built from two observations is a fiscal Q4 revenue that no filing states on its own: the annual figure
minus the nine-month year-to-date figure ending the quarter before. It is arithmetic on published values, both inputs
are listed and the row is marked as derived.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Any

from . import company_disclosure_metrics as disclosures

RULE_VERSION = "company_disclosure_ratios/v1"
ANNUALIZE = 4
INPUT_METRICS = ("commitments.all_in_total", "commitments.next_12m", "rpo.total", "rpo.next_12m_share",
                 "revenue.total", "revenue.run_rate")

RATIOS = (
    # ratio, name, indicator of the ANT action guide, unit
    ("commitments.marginal_intensity", "边际承诺强度", "1a.c", "ratio"),
    ("rpo.coverage", "RPO 覆盖率", "1b.b", "ratio"),
    ("revenue.run_rate_gap", "宣传年化收入与 GAAP 季度收入×4 的差距", "3a", "USD"),
)
FORMULAS = {
    "commitments.marginal_intensity": "(本季末全部承诺合计 − 上季末) ÷ (本季收入×4 − 上季收入×4)",
    "rpo.coverage": "RPO × 12 个月内确认比例 ÷ 未来 12 个月到期的合同义务",
    "revenue.run_rate_gap": "公司自报年化收入运行率 − 本季 GAAP 收入×4（时点须为同一季末）",
}


class _Missing(Exception):
    def __init__(self, items: list[dict]):
        super().__init__("missing_inputs")
        self.items = items


def _quarter_end(text: str) -> date | None:
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    last = calendar.monthrange(day.year, day.month)[1]
    return day if day.month % 3 == 0 and day.day == last else None


def _previous_quarter_end(day: date) -> date:
    year, month = (day.year, day.month - 3) if day.month > 3 else (day.year - 1, day.month + 9)
    return date(year, month, calendar.monthrange(year, month)[1])


class _Inputs:
    """Published default values of one issuer, addressed by (metric, period end, period basis)."""

    def __init__(self, rows: list[dict]):
        self.rows = {(row["metric"], row["period"], row["period_basis"]): row for row in rows if not row["bucket"]}

    def instants(self, metric: str) -> list[str]:
        return sorted(period for key, period, basis in self.rows if key == metric and basis == "instant")

    def find(self, metric: str, period: date, basis: str = "instant") -> dict | None:
        row = self.rows.get((metric, period.isoformat(), basis))
        if row is None:
            return None
        return {"metric": metric, "period": row["period"], "period_basis": row["period_basis"], "value": row["value"],
                "observation_id": row["observation_id"], "form_type": row["form_type"],
                "basis_note": row["basis_note"], "conflict": row["conflict"]}

    def quarter_revenue(self, period: date) -> tuple[float, list[dict], bool] | None:
        """(value, observations used, derived from annual minus year-to-date)."""
        direct = self.find("revenue.total", period, "quarter")
        if direct:
            return direct["value"], [direct], False
        annual = self.find("revenue.total", period, "annual")
        # Only a fiscal year end has an annual figure, so its prior quarter end carries the nine-month one.
        earlier = self.find("revenue.total", _previous_quarter_end(period), "ytd_9m")
        if annual and earlier:
            return annual["value"] - earlier["value"], [annual, earlier], True
        return None


def _gather(inputs: _Inputs, names: dict[str, str], wanted: list[tuple[str, date]]) -> list[dict]:
    """Each wanted instant value, or one _Missing carrying every gap at once."""
    found, missing = [], []
    for metric, period in wanted:
        item = inputs.find(metric, period)
        if item:
            found.append(item)
        else:
            missing.append({"metric": metric, "name": names[metric], "period": period.isoformat(),
                            "published_on_other_dates": inputs.instants(metric)})
    if missing:
        raise _Missing(missing)
    return found


def _revenue(inputs: _Inputs, names: dict[str, str], periods: list[date]) -> tuple[list[float], list[dict], bool]:
    values, used, derived, missing = [], [], False, []
    for period in periods:
        found = inputs.quarter_revenue(period)
        if found is None:
            missing.append({"metric": "revenue.total", "name": f"{names['revenue.total']}（单季）",
                            "period": period.isoformat()})
            continue
        values.append(found[0])
        used.extend(found[1])
        derived = derived or found[2]
    if missing:
        raise _Missing(missing)
    return values, used, derived


def _both(*steps) -> list:
    """Run every input step and report all gaps together, not only the first."""
    results, missing = [], []
    for step in steps:
        try:
            results.append(step())
        except _Missing as exc:
            missing.extend(exc.items)
    if missing:
        raise _Missing(missing)
    return results


def _marginal_intensity(inputs: _Inputs, names: dict, period: date) -> dict:
    previous = _previous_quarter_end(period)
    commitments, (revenue, used, derived) = _both(
        lambda: _gather(inputs, names, [("commitments.all_in_total", previous), ("commitments.all_in_total", period)]),
        lambda: _revenue(inputs, names, [previous, period]))
    commitment_change = commitments[1]["value"] - commitments[0]["value"]
    revenue_change = (revenue[1] - revenue[0]) * ANNUALIZE
    detail = {"commitment_change": commitment_change, "annualized_revenue_change": revenue_change}
    out = {"inputs": commitments + used, "derived_revenue": derived, "detail": detail}
    if revenue_change <= 0:
        # A flat or shrinking revenue makes the quotient meaningless (its sign would read backwards); the two changes
        # stay visible so a reader still sees commitments moving against revenue.
        return {**out, "value": None, "reason": "年化收入增量为零或负数，比值没有意义"}
    return {**out, "value": commitment_change / revenue_change}


def _rpo_coverage(inputs: _Inputs, names: dict, period: date) -> dict:
    total, share, due = _gather(inputs, names, [
        ("rpo.total", period), ("rpo.next_12m_share", period), ("commitments.next_12m", period)])
    covered = total["value"] * share["value"] / 100
    out = {"inputs": [total, share, due], "derived_revenue": False,
           "detail": {"rpo_next_12m": covered, "due_next_12m": due["value"]}}
    if due["value"] <= 0:
        return {**out, "value": None, "reason": "未来 12 个月到期的合同义务不是正数"}
    return {**out, "value": covered / due["value"]}


def _run_rate_gap(inputs: _Inputs, names: dict, period: date) -> dict:
    run_rate, (revenue, used, derived) = _both(
        lambda: _gather(inputs, names, [("revenue.run_rate", period)]),
        lambda: _revenue(inputs, names, [period]))
    annualized = revenue[0] * ANNUALIZE
    detail = {"run_rate": run_rate[0]["value"], "annualized_revenue": annualized,
              "gap_ratio": (run_rate[0]["value"] / annualized - 1) if annualized > 0 else None}
    return {"inputs": run_rate + used, "derived_revenue": derived, "detail": detail,
            "value": run_rate[0]["value"] - annualized}


_COMPUTE = {"commitments.marginal_intensity": _marginal_intensity, "rpo.coverage": _rpo_coverage,
            "revenue.run_rate_gap": _run_rate_gap}


def derived(repository: Any, entity: str, *, as_of: datetime | None = None) -> list[dict]:
    """Every ratio at every quarter end for which the issuer has published anything."""
    _, declared = disclosures.pipeline.issuer_metrics(entity)
    names = {metric.key: metric.name for metric in declared}
    for key in INPUT_METRICS:
        names.setdefault(key, key)          # an issuer that does not declare it can only be "missing" it
    rows = disclosures.series(repository, entity, as_of=as_of)
    inputs = _Inputs(rows)
    # Quarter ends with a balance or a quarter figure among the inputs; a year end known only through its annual figure
    # counts when its fourth quarter can be derived.
    periods = sorted({
        day for row in rows if row["metric"] in INPUT_METRICS and (day := _quarter_end(row["period"]))
        and (row["period_basis"] in {"instant", "quarter"}
             or (row["period_basis"] == "annual" and inputs.quarter_revenue(day) is not None))})
    out = []
    for period in periods:
        for ratio, name, indicator, unit in RATIOS:
            base = {"ratio": ratio, "name": name, "indicator": indicator, "unit": unit, "period": period.isoformat(),
                    "formula": FORMULAS[ratio], "rule_version": RULE_VERSION}
            try:
                result = _COMPUTE[ratio](inputs, names, period)
            except _Missing as exc:
                reason = "缺少" + "、".join(
                    f"{item['name']}（{item['period']}" + (
                        f"；只在 {'、'.join(item['published_on_other_dates'])} 有已发布值"
                        if item.get("published_on_other_dates") else "") + "）" for item in exc.items)
                out.append({**base, "status": "not_computable", "value": None, "reason": reason,
                            "missing": exc.items, "inputs": [], "input_observation_ids": [],
                            "derived_revenue": False, "detail": {}})
                continue
            out.append({**base, "status": "ok" if result["value"] is not None else "not_computable",
                        "reason": "", "missing": [], **result,
                        "input_observation_ids": [item["observation_id"] for item in result["inputs"]]})
    return out
