"""2026-10-30 report wording the index extractor must read, plus the unreadable-chart-cell rule.

The licensed PDF is only in the local artifact store, so the real-report test skips elsewhere; the valuation
wording is also checked on a synthetic page."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ats.data.sources.factset_earnings_insight import FactSetFetch, STABLE_URL, inspect_pdf
from ats.data.sources.factset_earnings_text import (
    FactSetExtractionRun, extract_index_text_semantic, extract_valuation, merge_candidate_evidence,
    validate_index_candidates,
)
from ats.data.sources.factset_index_charts import extract_index_chart_candidates
from ats.data.sources.factset_report_layout import discover_charts, load_layout_policy
from ats.data.sources.factset_earnings_insight import FactSetPDF, PDFPage

HASH = 'a3baf00b580afc76a4e95bd2699bf70ad2287002b281f603825a40ba46baeb34'
PDF = Path(f'var/data_artifacts/{HASH[:2]}/{HASH}.bin')
NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def report():
    if not PDF.exists():
        pytest.skip('licensed FactSet PDF is only available in the local artifact store')
    return inspect_pdf(FactSetFetch(
        STABLE_URL, STABLE_URL, 200, '', '', 'application/pdf', PDF.read_bytes(), NOW))


def test_valuation_averages_accept_below_both_horizons():
    text = ("Valuation: The forward 12 -month P/E ratio for the S&P 500 is 18.7. This P/E ratio is below the "
            "5-year average (19.8) and below the 10-year average (19.1).")
    document = FactSetPDF(report_date=date(2026, 10, 30), title='t', page_count=1,
                          pages=(PDFPage(1, text, 0, len(text), 'Key Metrics'),), images=(),
                          text=text, text_hash='h', pdf_hash='p')
    run = FactSetExtractionRun(run_id='r', document_id='d', version_id='v', known_at=NOW,
                               report_date=date(2026, 10, 30), extractor_version='x',
                               phase='in_progress', template_status='ok')
    result = extract_valuation(document, run)
    values = {c.metric_id: c.value for c in result.candidates}
    assert values['valuation.forward_pe.average_5y'] == 19.8
    assert values['valuation.forward_pe.average_10y'] == 19.1


def test_report_wording_gives_every_required_index_metric(report):
    run = extract_index_text_semantic(report, document_id='d', version_id='v', known_at=NOW)
    metrics = {c.metric_id: c.value for c in run.candidates}
    assert metrics['earnings.revision.improved_sector_count'] == 8
    assert metrics['valuation.forward_pe.average_5y'] == 19.8
    assert metrics['valuation.forward_pe.average_10y'] == 19.1


def test_a_chart_cell_read_without_its_percent_sign_does_not_contradict_the_paragraph(report):
    policy = load_layout_policy()
    run = extract_index_text_semantic(report, document_id='d', version_id='v', known_at=NOW)
    run.candidates.extend(extract_index_chart_candidates(
        report, run, policy=policy, chart_inventory=discover_charts(report, policy=policy)))
    merged = merge_candidate_evidence(run.candidates)
    assert not merged.conflict_identities
    below = [c for c in merged.candidates if c.metric_id == 'earnings.eps.below_estimate_share']
    assert [c.value for c in below] == [0.14]
    assert below[0].dimensions['unreadable_chart_cells'] == ['1496']
    run.candidates = merged.candidates
    assert not validate_index_candidates(run).quarantined
