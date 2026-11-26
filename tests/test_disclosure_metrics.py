"""Company disclosure metrics: declared list, locating, code checks, cache, human release, version conflicts."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from ats.data.catalog.loader import DataCatalog
from ats.data.catalog.structured import StructuredCatalog
from ats.data.persistent_queue import _SAFE_COMMANDS
from ats.data.pipelines.structured import disclosure_metrics as pipeline
from ats.data.pipelines.unstructured import registration
from ats.data.products import company_disclosure_metrics as product
from ats.data.stores.structured import disclosure_review as review
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository
from ats.runtime import disclosure_cli

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
RPO = ("As of September 30, 2026 (unaudited), remaining performance obligations amounted to $13.8 billion. "
       "The Company expects to recognize approximately 75% (unaudited) of this amount as revenue over the next "
       "12 months.")
QUARTERS = "Three Months Ended (in millions) March 31, 2025 June 30, 2025 September 30, 2025"
QUARTER_ROW = "Compute and infrastructure 944 1,390 2,047"
DUE_HEADER = "Payments Due by Year (in millions) Total 2026 2027 Thereafter"
DUE_ROW = "Total(4)(5) $319,993 $5,231 $29,165 $285,597"
DUE_INTRO = "The following table summarizes our contractual obligations as of September 30, 2026:"
COST = ("operating expenses grouped by function (in millions): Year Ended December 31, 2024 2025 "
        "Cost of revenue(1) $752 $2,773 Substantially all of cost of revenue consists of compute.")


def _body(rpo: str = RPO) -> str:
    return (f"Remaining Performance Obligations {rpo} Quarterly Results of Operations {QUARTERS} "
            f"Operating Expenses Compute and Infrastructure {QUARTER_ROW} {DUE_INTRO} {DUE_HEADER} {DUE_ROW} "
            f"The following presents {COST}")


def _html(form: str, filed: str, body: str) -> str:
    filler = "<p>Our business depends on compute and customers.</p>" * 600
    if form == "424B4":
        return ("<html><body><p>Filed Pursuant to Rule 424(b)(4)</p><h1>Anthropic, PBC</h1>"
                f"{filler}<p>{body}</p></body></html>")
    return (f"<html><body><p>As filed with the Securities and Exchange Commission on {filed}.</p><div>FORM S-1</div>"
            "<div>REGISTRATION STATEMENT UNDER THE SECURITIES ACT OF 1933</div><h1>Anthropic, PBC</h1>"
            f"<div>(Exact name of registrant as specified in its charter)</div>{filler}<p>{body}</p></body></html>")


@pytest.fixture
def documents(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_DOCS_ROOT", str(tmp_path / "documents"))
    repository = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    first = RPO.replace("$13.8 billion", "$10.8 billion").replace("September 30, 2026", "September 30, 2026")
    for form, filed, iso, body in (("S-1", "October 26, 2026", "2026-10-26", _body(first)),
                                   ("424B4", "", "2026-11-19", _body())):
        path = tmp_path / f"{form}.html"
        path.write_text(_html(form, filed, body), encoding="utf-8")
        result = registration.ingest_file("ANT", form, path, filing_date=iso, store=repository, now=NOW)
        assert result["status"] == "succeeded", result
    yield repository
    repository.close()


@pytest.fixture
def repo(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    yield repository
    repository.close()


def _metric(key):
    return pipeline.issuer_metrics("ANT", [key])[1][0]


def _reading(**values):
    return {"passage": 1, "scale": "none", "scale_quote": "", "period_quote": "", "header_quote": "",
            "column_index": -1, "bucket": "", "qualifier": "", **values}


def _rpo_reading(amount="13.8"):
    return _reading(
        quote=f"As of September 30, 2026 (unaudited), remaining performance obligations amounted to ${amount} billion.",
        value_text=amount, scale="billion", period_end="2026-09-30", period_basis="instant")


def _document(documents, form="424B4"):
    return pipeline.load_document(documents, "ANT", form=form)


def _check(documents, key, reading):
    settings, metrics = pipeline.issuer_metrics("ANT", [key])
    document = _document(documents)
    windows = pipeline.locate(document["text"], metrics[0], settings)
    assert windows, key
    return pipeline.verify(reading, metrics[0], document, windows)


# --------------------------------------------------------------------------- #
# Declared list
# --------------------------------------------------------------------------- #
def test_ant_list_covers_the_required_metrics_and_matches_the_catalog():
    _, metrics = pipeline.issuer_metrics("ANT")
    by_key = {metric.key: metric for metric in metrics}
    assert {"compute.inference_expense", "compute.training_expense", "commitments.total", "commitments.by_year",
            "customers.over_100k", "margin.inference_gross", "revenue.csp_marketplace_share",
            "retention.net_revenue", "retention.gross_revenue", "revenue.product", "revenue.consumption",
            "revenue.subscription", "concentration.top10_share", "rpo.total"} <= set(by_key)
    assert all(metric.unit and metric.period_type for metric in metrics)
    catalog = StructuredCatalog.load()
    definitions = catalog.raw["metric_definitions"]
    families = {"USD": "currency", "count": "count", "percent": "percent"}
    assert set(catalog.raw["datasets"]["company_disclosure_metrics"]["core_metrics"]) == {
        metric.metric_id for metric in metrics}
    assert all(definitions[m.metric_id]["unit_family"] == families[m.unit] for m in metrics)
    assert DataCatalog.load().validate().valid
    assert {"disclosure-extract", "disclosure-confirm", "disclosure-reject", "disclosure-correct"} <= _SAFE_COMMANDS


def test_undeclared_issuer_and_metric_are_refused():
    with pytest.raises(ValueError, match="disclosure_issuer_not_declared"):
        pipeline.issuer_metrics("NVDA")
    with pytest.raises(ValueError, match="disclosure_metric_not_declared"):
        pipeline.issuer_metrics("ANT", ["revenue.magic"])


# --------------------------------------------------------------------------- #
# Code checks
# --------------------------------------------------------------------------- #
def test_a_quoted_figure_is_scaled_by_code(documents):
    checked = _check(documents, "rpo.total", _rpo_reading())
    assert checked["reasons"] == [] and checked["value"] == 13_800_000_000
    text = _document(documents)["text"]
    assert text[checked["char_start"]:checked["char_end"]] == checked["quote"]


@pytest.mark.parametrize("change,reason", [
    ({"value_text": "10.8"}, "value_not_in_quote"),
    ({"quote": "remaining performance obligations amounted to $99 billion", "value_text": "99"}, "quote_not_found"),
    ({"scale": "million"}, "scale_unverified"),
    ({"scale": "none"}, "scale_unverified"),
    ({"period_end": "2026-06-30"}, "period_unverified"),
    ({"period_end": "2026-12-31"}, "period_after_filing"),
    ({"period_basis": "annual"}, "period_type_mismatch"),
])
def test_readings_that_the_passage_does_not_support_are_incomplete(documents, change, reason):
    checked = _check(documents, "rpo.total", {**_rpo_reading(), **change})
    assert reason in checked["reasons"]
    if reason in {"value_not_in_quote", "quote_not_found"}:
        assert checked["reasons"][0] == "evidence_incomplete"


def test_table_columns_are_checked_against_the_header(documents):
    reading = _reading(quote=QUARTER_ROW, value_text="1,390", scale="million", header_quote=QUARTERS,
                       column_index=1, period_end="2025-06-30", period_basis="quarter")
    checked = _check(documents, "compute.total_expense", reading)
    assert checked["reasons"] == [] and checked["value"] == 1_390_000_000
    wrong = _check(documents, "compute.total_expense", {**reading, "period_end": "2025-09-30"})
    assert "table_column_unverified" in wrong["reasons"]
    bare = _check(documents, "compute.total_expense",
                  {**reading, "header_quote": "", "column_index": -1, "period_quote": QUARTERS})
    assert "table_column_unverified" in bare["reasons"]


def test_due_by_year_buckets_and_total_column(documents):
    shared = {"quote": DUE_ROW, "scale": "million", "header_quote": DUE_HEADER, "period_quote": DUE_INTRO,
              "period_end": "2026-09-30", "period_basis": "instant"}
    by_year = _check(documents, "commitments.by_year",
                     _reading(**shared, value_text="29,165", column_index=2, bucket="2027"))
    assert by_year["reasons"] == [] and by_year["value"] == 29_165_000_000
    total = _check(documents, "commitments.total", _reading(**shared, value_text="319,993", column_index=0))
    assert total["reasons"] == []
    assert "table_column_unverified" in _check(
        documents, "commitments.by_year",
        _reading(**shared, value_text="319,993", column_index=0, bucket="Total"))["reasons"]


def test_a_neighbouring_line_is_not_accepted_as_the_metric(documents):
    reading = _reading(quote="Cost of revenue(1) $752 $2,773", value_text="2,773", scale="million",
                       header_quote="(in millions): Year Ended December 31, 2024 2025", column_index=1,
                       period_end="2025-12-31", period_basis="annual")
    assert _check(documents, "expense.cost_of_revenue", reading)["reasons"] == []
    settings, metrics = pipeline.issuer_metrics("ANT", ["compute.inference_expense"])
    document = _document(documents)
    windows = pipeline.locate(document["text"], _metric("expense.cost_of_revenue"), settings)
    assert "metric_label_mismatch" in pipeline.verify(reading, metrics[0], document, windows)["reasons"]


# --------------------------------------------------------------------------- #
# Extraction, cache and release
# --------------------------------------------------------------------------- #
class _Model:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, context):
        key = context.split("指标键：")[1].split("\n")[0]
        self.calls.append(key)
        readings = self.answers.get(key, [])
        return {"disclosed": bool(readings), "readings": readings, "note": ""}


def _run(repo, documents, form, model, keys):
    settings, metrics = pipeline.issuer_metrics("ANT", keys)
    return pipeline.extract_document(repo, _document(documents, form), settings, metrics, extractor=model, now=NOW)


KEYS = ["rpo.total", "compute.inference_expense", "retention.gross_revenue"]


def test_undisclosed_metrics_create_nothing_and_a_rerun_asks_nothing(repo, documents):
    model = _Model({"rpo.total": [_rpo_reading()]})
    first = _run(repo, documents, "424B4", model, KEYS)
    states = {item["metric"]: item["state"] for item in first["metrics"]}
    assert states == {"rpo.total": "extracted", "compute.inference_expense": "not_disclosed",
                      "retention.gross_revenue": "not_located"}
    assert first["model_requests"] == 2 and first["pending_confirmation"] == 1
    candidates = repo.evidence_candidates()
    assert [row["metric_id"] for row in candidates] == ["disclosure.rpo.total"]
    assert candidates[0]["entity_id"] == "ANTHROPIC"
    assert repo.observations(dataset_id=pipeline.DATASET_ID) == []              # nothing is published by a model
    second = _run(repo, documents, "424B4", model, KEYS)
    assert second["model_requests"] == 0 and len(model.calls) == 2
    assert len(repo.evidence_candidates()) == 1
    coverage = {row["metric"]: row["state"] for row in product.coverage(repo, "ANT")}
    assert coverage["rpo.total"] == "waiting" and coverage["compute.inference_expense"] == "not_disclosed"


def test_a_failed_request_is_retried_and_keeps_the_other_metrics(repo, documents):
    def broken(context):
        raise RuntimeError("provider down")

    result = _run(repo, documents, "424B4", broken, ["rpo.total"])
    assert result["status"] == "failed" and result["failed_metrics"] == ["rpo.total"]
    retried = _run(repo, documents, "424B4", _Model({"rpo.total": [_rpo_reading()]}), ["rpo.total"])
    assert retried["model_requests"] == 1 and retried["pending_confirmation"] == 1


def _candidate_of(result):
    return result["metrics"][0]["candidates"][0]["candidate_id"]


def test_newer_filing_wins_whatever_the_confirmation_order_and_withdrawal_removes_it(repo, documents):
    old = _candidate_of(_run(repo, documents, "S-1", _Model({"rpo.total": [_rpo_reading("10.8")]}), ["rpo.total"]))
    new = _candidate_of(_run(repo, documents, "424B4", _Model({"rpo.total": [_rpo_reading()]}), ["rpo.total"]))
    review.confirm(repo, new, reviewer="owner", at=NOW)
    review.confirm(repo, old, reviewer="owner", at=NOW + timedelta(hours=1))
    [row] = product.series(repo, "ANT", metric="rpo.total")
    assert (row["value"], row["form_type"], row["conflict"]) == (13_800_000_000, "424B4", True)
    assert [(item["value"], item["form_type"]) for item in row["superseded"]] == [(10_800_000_000, "S-1")]
    assert row["reviewer"] == "owner" and row["quote"].startswith("As of September 30, 2026")
    assert repo.evidence_reviews(new)[0]["reviewer"] == "owner"
    [before] = product.series(repo, "ANT", metric="rpo.total", as_of=NOW + timedelta(minutes=1))
    assert before["superseded"] == []
    review.reject(repo, new, reviewer="owner", note="读错了", at=NOW + timedelta(hours=2))
    [row] = product.series(repo, "ANT", metric="rpo.total")
    assert row["value"] == 10_800_000_000 and not row["conflict"]


def test_incomplete_candidates_cannot_be_confirmed_and_corrections_keep_history(repo, documents):
    wrong = {**_rpo_reading(), "period_end": "2026-06-30"}
    candidate = _candidate_of(_run(repo, documents, "424B4", _Model({"rpo.total": [wrong]}), ["rpo.total"]))
    with pytest.raises(ValueError, match="disclosure_candidate_evidence_incomplete"):
        review.confirm(repo, candidate, reviewer="owner")
    with pytest.raises(ValueError, match="reviewer_and_note_required"):
        review.correct(repo, documents, candidate, reviewer="owner", note="", changes={"period_end": "2026-09-30"})
    fixed = review.correct(repo, documents, candidate, reviewer="owner", note="期间应为 9 月 30 日",
                           changes={"period_end": "2026-09-30"}, at=NOW)
    assert fixed["candidate_id"] != candidate and json.loads(fixed["reason_codes_json"]) == []
    assert fixed["extraction_method"] == "manual_correction"
    assert repo.evidence_candidate(candidate)["status"] == "rejected"
    assert fixed["candidate_id"] in repo.evidence_reviews(candidate)[0]["note"]
    with pytest.raises(ValueError, match="reviewer_required"):
        review.confirm(repo, fixed["candidate_id"], reviewer=" ")
    review.confirm(repo, fixed["candidate_id"], reviewer="owner", basis_note="首次发布", at=NOW)
    assert product.series(repo, "ANT", metric="rpo.total")[0]["basis_note"] == "首次发布"


def test_two_values_from_one_filing_cannot_both_be_released(repo, documents):
    other = _reading(quote="remaining performance obligations amounted to $13.8 billion", value_text="13.8",
                     scale="billion", period_end="2026-09-30", period_basis="instant",
                     period_quote="As of September 30, 2026")
    result = _run(repo, documents, "424B4", _Model({"rpo.total": [_rpo_reading(), other]}), ["rpo.total"])
    assert len(result["metrics"][0]["candidates"]) == 1                      # the same fact is one candidate
    candidate = _candidate_of(result)
    review.confirm(repo, candidate, reviewer="owner")
    held = repo.evidence_candidate(candidate)
    clash = {**held, "candidate_id": "other-value", "value": 1.0, "status": "needs_evidence", "observation_id": "",
             "raw": json.loads(held["raw_payload"]), "reason_codes": []}
    repo.save_evidence_candidate(clash)
    with pytest.raises(ValueError, match="disclosure_same_filing_value_conflict"):
        review.confirm(repo, "other-value", reviewer="owner")


def test_dry_run_and_review_listing(documents, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(tmp_path / "documents.sqlite"))
    repository = SQLiteStructuredRepository(tmp_path / "documents.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    monkeypatch.setenv("ATS_DATA_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    assert disclosure_cli.run("disclosure-extract", entity="ANT", form="424B4", metric="rpo.total", dry_run=True) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["model_requests_needed"] == 1 and repository.evidence_candidates() == []
    _run(repository, documents, "424B4", _Model({"rpo.total": [_rpo_reading()]}), ["rpo.total"])
    assert disclosure_cli.run("disclosure-candidates", entity="ANT", output_format="markdown") == 0
    listing = capsys.readouterr().out
    assert "13,800,000,000" in listing and "待确认" in listing and "原文：" in listing
    assert disclosure_cli.run("disclosure-confirm", entity="ANT", candidate_ids=["nope"], reviewer="owner") == 1
    assert disclosure_cli.run("disclosure-extract", entity="ANT", form="424B4", isolated=True) == 1
    repository.close()
