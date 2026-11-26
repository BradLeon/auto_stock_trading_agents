"""Ratios derived from confirmed disclosures: computed from published values only, gaps named, nothing filled in."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from ats.data.pipelines.structured import disclosure_metrics as pipeline
from ats.data.products import company_disclosure_ratios as ratios
from ats.data.stores.structured import disclosure_review as review
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.runtime import disclosure_cli

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
BN = 1_000_000_000
Q2, Q3 = "2026-06-30", "2026-09-30"


@pytest.fixture
def repo(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    yield repository
    repository.close()


def _publish(repo, key, period, value, *, basis="instant", filed="2026-11-19", form="424B4"):
    """One confirmed candidate, the same path a reviewed filing takes."""
    metric = pipeline.issuer_metrics("ANT", [key])[1][0]
    document = {"entity": "ANT", "document_id": f"doc-{form}", "version_id": "v1", "filing_date": filed,
                "form_type": form, "accession_number": "0000000000-26-000001"}
    checked = {"period_end": period, "period_basis": basis, "bucket": "", "value": value, "quote": f"{key} {value}",
               "char_start": 0, "char_end": 10, "reasons": []}
    candidate = pipeline.save_candidate(repo, document, metric, checked, extraction_id="e", method="test", now=NOW)
    review.confirm(repo, candidate, reviewer="owner", at=NOW)
    return candidate


def _rows(repo, ratio):
    return {row["period"]: row for row in ratios.derived(repo, "ANT") if row["ratio"] == ratio}


def _two_quarters(repo, *, revenue=(11.555, 16.956)):
    for period, value in zip((Q2, Q3), revenue):
        _publish(repo, "revenue.total", period, value * BN, basis="quarter")


def test_rpo_coverage_uses_the_share_due_within_twelve_months(repo):
    _publish(repo, "rpo.total", Q3, 13.8 * BN)
    _publish(repo, "rpo.next_12m_share", Q3, 75)
    _publish(repo, "commitments.next_12m", Q3, 70.7 * BN)
    row = _rows(repo, "rpo.coverage")[Q3]
    assert row["status"] == "ok" and row["value"] == pytest.approx(13.8 * 0.75 / 70.7)
    assert [item["metric"] for item in row["inputs"]] == ["rpo.total", "rpo.next_12m_share", "commitments.next_12m"]
    assert len(set(row["input_observation_ids"])) == 3
    assert all(repo.evidence_links(observation_id=item) for item in row["input_observation_ids"])


def test_rpo_coverage_without_due_commitments_is_not_computable(repo):
    _publish(repo, "rpo.total", Q3, 13.8 * BN)
    _publish(repo, "rpo.next_12m_share", Q3, 75)
    _publish(repo, "commitments.next_12m", Q2, 60 * BN)          # another date is not carried forward
    row = _rows(repo, "rpo.coverage")[Q3]
    assert row["status"] == "not_computable" and row["value"] is None and row["input_observation_ids"] == []
    assert [(item["metric"], item["period"]) for item in row["missing"]] == [("commitments.next_12m", Q3)]
    assert "未来 12 个月到期的合同义务" in row["reason"] and Q2 in row["reason"]


def test_marginal_intensity_is_commitment_change_over_annualized_revenue_change(repo):
    _two_quarters(repo)
    _publish(repo, "commitments.all_in_total", Q2, 800 * BN)
    _publish(repo, "commitments.all_in_total", Q3, 900.9 * BN)
    row = _rows(repo, "commitments.marginal_intensity")[Q3]
    assert row["status"] == "ok"
    assert row["value"] == pytest.approx(100.9 / ((16.956 - 11.555) * 4))
    assert row["detail"]["commitment_change"] == pytest.approx(100.9 * BN)
    assert len(row["input_observation_ids"]) == 4
    first = _rows(repo, "commitments.marginal_intensity")[Q2]
    assert first["status"] == "not_computable"
    assert {item["period"] for item in first["missing"]} == {"2026-03-31"}


def test_marginal_intensity_never_borrows_the_contractual_table_total(repo):
    _two_quarters(repo)
    _publish(repo, "commitments.total", Q2, 250 * BN)
    _publish(repo, "commitments.total", Q3, 319.993 * BN)
    _publish(repo, "commitments.all_in_total", Q3, 900.9 * BN)
    row = _rows(repo, "commitments.marginal_intensity")[Q3]
    assert row["status"] == "not_computable"
    assert [(item["metric"], item["period"]) for item in row["missing"]] == [("commitments.all_in_total", Q2)]


@pytest.mark.parametrize("revenue", [(16.956, 16.956), (16.956, 11.555)])
def test_marginal_intensity_has_no_ratio_when_revenue_does_not_grow(repo, revenue):
    _two_quarters(repo, revenue=revenue)
    _publish(repo, "commitments.all_in_total", Q2, 800 * BN)
    _publish(repo, "commitments.all_in_total", Q3, 900 * BN)
    row = _rows(repo, "commitments.marginal_intensity")[Q3]
    assert row["status"] == "not_computable" and row["value"] is None and "年化收入增量" in row["reason"]
    assert row["detail"]["commitment_change"] == pytest.approx(100 * BN) and len(row["input_observation_ids"]) == 4


def test_run_rate_gap_needs_a_run_rate_on_the_quarter_end(repo):
    _two_quarters(repo)
    _publish(repo, "revenue.run_rate", "2026-10-31", 81 * BN)
    row = _rows(repo, "revenue.run_rate_gap")[Q3]
    assert row["status"] == "not_computable" and "2026-10-31" in row["reason"]
    assert "2026-10-31" not in _rows(repo, "revenue.run_rate_gap")
    _publish(repo, "revenue.run_rate", Q3, 71 * BN)
    row = _rows(repo, "revenue.run_rate_gap")[Q3]
    assert row["status"] == "ok" and row["value"] == pytest.approx(71 * BN - 16.956 * BN * 4)
    assert row["detail"]["gap_ratio"] == pytest.approx(71 / (16.956 * 4) - 1)
    assert not row["derived_revenue"]


def test_missing_inputs_are_all_named_at_once(repo):
    _publish(repo, "rpo.total", Q3, 13.8 * BN)
    row = _rows(repo, "commitments.marginal_intensity")[Q3]
    assert {(item["metric"], item["period"]) for item in row["missing"]} == {
        ("commitments.all_in_total", Q2), ("commitments.all_in_total", Q3),
        ("revenue.total", Q2), ("revenue.total", Q3)}


def test_fourth_quarter_revenue_is_the_annual_figure_minus_nine_months(repo):
    _publish(repo, "revenue.total", "2026-12-31", 55 * BN, basis="annual", form="S-1/A")
    _publish(repo, "revenue.run_rate", "2026-12-31", 100 * BN)
    alone = _rows(repo, "revenue.run_rate_gap")["2026-12-31"]          # an annual figure alone is not a quarter
    assert alone["status"] == "not_computable" and [item["metric"] for item in alone["missing"]] == ["revenue.total"]
    _publish(repo, "revenue.total", Q3, 33.242 * BN, basis="ytd_9m")
    row = _rows(repo, "revenue.run_rate_gap")["2026-12-31"]
    assert row["status"] == "ok" and row["derived_revenue"]
    assert row["detail"]["annualized_revenue"] == pytest.approx((55 - 33.242) * 4 * BN)
    assert [item["period_basis"] for item in row["inputs"]] == ["instant", "annual", "ytd_9m"]


def test_withdrawn_input_makes_the_ratio_not_computable(repo):
    _publish(repo, "rpo.total", Q3, 13.8 * BN)
    _publish(repo, "rpo.next_12m_share", Q3, 75)
    due = _publish(repo, "commitments.next_12m", Q3, 70.7 * BN)
    review.reject(repo, due, reviewer="owner", note="读错了", at=NOW)
    assert _rows(repo, "rpo.coverage")[Q3]["status"] == "not_computable"


def test_cli_prints_the_ratios_without_writing(repo, tmp_path, monkeypatch, capsys):
    _publish(repo, "rpo.total", Q3, 13.8 * BN)
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(tmp_path / "structured.sqlite"))
    monkeypatch.setenv("ATS_DATA_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    assert disclosure_cli.run("disclosure-derived", entity="ANT") == 0
    result = json.loads(capsys.readouterr().out)
    assert {row["status"] for row in result["ratios"]} == {"not_computable"}
    assert len(result["ratios"]) == 3
