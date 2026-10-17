"""A foreign filer's newest quarter can lack cash flow at the vendors (found on ASML, 2026-10-17)."""
from __future__ import annotations

from ats.data.fundamentals import (_PACKAGE_CASH_FLOW_METRICS, _PACKAGE_INSTANT_METRICS,
                                   _PACKAGE_QUARTER_METRICS, _complete_report_package)

SRC = "defeatbeta_stock_statement"


class _Repo:
    def __init__(self, rows):
        self.rows = rows

    def observations(self, **kw):
        return [r for r in self.rows if r["source_id"] == kw.get("source_id") and r["entity"] == kw.get("entity_id")]


def _row(metric, period, basis, value=1.0):
    return {"source_id": SRC, "entity": "ASML", "metric_id": metric, "period": period, "period_basis": basis,
            "value": value, "currency": "EUR", "known_at": "2026-10-16T00:00:00+00:00",
            "fetched_at": "2026-10-16T00:00:00+00:00", "observation_id": f"{metric}@{period}", "unit": "EUR"}


def _quarter(period, *, cash_flow=True, income=True):
    rows = []
    for m in _PACKAGE_QUARTER_METRICS:
        if m in _PACKAGE_CASH_FLOW_METRICS and not cash_flow:
            continue
        if m not in _PACKAGE_CASH_FLOW_METRICS and not income:
            continue
        rows.append(_row(m, period, "quarter"))
    rows += [_row(m, period, "instant") for m in _PACKAGE_INSTANT_METRICS]
    rows += [_row("financial.eps.diluted.gaap", period, "quarter"), _row("financial.total_debt.provider_reported", period, "instant")]
    return rows


def test_a_newest_quarter_missing_only_cash_flow_is_accepted_with_the_gap_named():
    pkg = _complete_report_package(_Repo(_quarter("2026-03-31") + _quarter("2026-06-30", cash_flow=False)),
                                   source_id=SRC, symbol="ASML")
    assert pkg is not None and pkg["period"] == "2026-06-30"
    assert pkg["gaps"] == sorted(_PACKAGE_CASH_FLOW_METRICS)
    assert not any(r["metric_id"] in _PACKAGE_CASH_FLOW_METRICS for r in pkg["rows"])


def test_a_complete_quarter_has_no_gaps():
    pkg = _complete_report_package(_Repo(_quarter("2026-06-30")), source_id=SRC, symbol="ASML")
    assert pkg is not None and pkg["gaps"] == []


def test_a_source_that_never_supplied_cash_flow_is_not_excused():
    rows = _quarter("2026-03-31", cash_flow=False) + _quarter("2026-06-30", cash_flow=False)
    assert _complete_report_package(_Repo(rows), source_id=SRC, symbol="ASML") is None


def test_a_newest_quarter_missing_income_lines_still_blocks_the_package():
    # The AMZN failure mode: the newer quarter has little beyond EPS, so the older complete quarter must NOT be promoted.
    rows = _quarter("2026-03-31") + _quarter("2026-06-30", cash_flow=False, income=False)
    assert _complete_report_package(_Repo(rows), source_id=SRC, symbol="ASML") is None
