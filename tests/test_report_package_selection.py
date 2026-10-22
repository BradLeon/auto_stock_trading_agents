"""Which stored source a consumer's report package comes from: the newest complete reporting period wins."""
from __future__ import annotations

from ats.data.fundamentals import (
    _PACKAGE_CASH_FLOW_METRICS,
    _PACKAGE_INSTANT_METRICS,
    _PACKAGE_QUARTER_METRICS,
    _complete_report_package,
    _select_report_package,
)
from ats.schemas.fundamentals import StatementMetric


class _Repo:
    def __init__(self, rows):
        self.rows = rows

    def observations(self, **kw):
        return [r for r in self.rows if r["source_id"] == kw.get("source_id") and r["entity"] == kw.get("entity_id")]


def _row(source, metric, period, basis, *, currency="USD"):
    return {"source_id": source, "entity": "XYZ", "metric_id": metric, "period": period, "period_basis": basis,
            "value": 1.0, "currency": currency, "known_at": "2026-10-16T00:00:00+00:00",
            "fetched_at": "2026-10-16T00:00:00+00:00", "observation_id": f"{source}:{metric}@{period}", "unit": currency}


def _package(source, period, *, eps="financial.eps.diluted.gaap", currency="USD"):
    rows = [_row(source, m, period, "quarter", currency=currency) for m in _PACKAGE_QUARTER_METRICS]
    rows += [_row(source, m, period, "instant", currency=currency) for m in _PACKAGE_INSTANT_METRICS]
    rows += [_row(source, eps, period, "quarter", currency=currency),
             _row(source, "financial.total_debt.provider_reported", period, "instant", currency=currency)]
    return rows


def test_a_newer_issuer_quarter_beats_a_vendor_that_is_one_quarter_behind():
    rows = _package("defeatbeta_stock_statement", "2026-06-30") + _package("issuer_ir_files", "2026-09-30")
    package = _select_report_package(_Repo(rows), symbol="XYZ")
    assert (package["source_id"], package["period"]) == ("issuer_ir_files", "2026-09-30")


def test_on_the_same_period_the_established_source_order_still_decides():
    rows = _package("issuer_ir_files", "2026-09-30") + _package("defeatbeta_stock_statement", "2026-09-30") \
        + _package("sec_companyfacts", "2026-09-30")
    assert _select_report_package(_Repo(rows), symbol="XYZ")["source_id"] == "defeatbeta_stock_statement"


def test_a_source_with_only_an_incomplete_newer_quarter_does_not_hide_an_older_complete_one():
    newer = [r for r in _package("issuer_ir_files", "2026-09-30") if r["metric_id"] != "financial.inventory.gaap"
             and r["metric_id"] not in _PACKAGE_CASH_FLOW_METRICS]
    rows = _package("defeatbeta_stock_statement", "2026-06-30") + newer
    package = _select_report_package(_Repo(rows), symbol="XYZ")
    assert (package["source_id"], package["period"]) == ("defeatbeta_stock_statement", "2026-06-30")
    assert _select_report_package(_Repo([]), symbol="XYZ") is None


def test_basic_eps_completes_a_package_that_has_no_diluted_eps_and_diluted_is_preferred_when_both_exist():
    only_basic = _package("issuer_ir_files", "2026-06-30", eps="financial.eps.basic.gaap", currency="KRW")
    package = _complete_report_package(_Repo(only_basic), source_id="issuer_ir_files", symbol="XYZ")
    assert package is not None and package["eps_metric"] == "financial.eps.basic.gaap"
    both = only_basic + [_row("issuer_ir_files", "financial.eps.diluted.gaap", "2026-06-30", "quarter", currency="KRW")]
    assert _complete_report_package(_Repo(both), source_id="issuer_ir_files", symbol="XYZ")["eps_metric"] \
        == "financial.eps.diluted.gaap"


def test_statement_lines_render_amounts_with_their_currency():
    amount = StatementMetric(label="Revenue", value=1494243.0, unit="TWD M", qoq=17.6, yoy=50.9)
    assert amount.render() == "Revenue: 1,494,243 TWD M (QoQ +17.6%, YoY +50.9%)"
    per_share = StatementMetric(label="Diluted EPS", value=147.13, unit="TWD/ADR", qoq=8.0)
    assert per_share.render().startswith("Diluted EPS: 147.13 TWD/ADR")
    usd = StatementMetric(label="Revenue", value=41456.0, unit="$M")
    assert usd.render().startswith("Revenue: 41,456$M")
