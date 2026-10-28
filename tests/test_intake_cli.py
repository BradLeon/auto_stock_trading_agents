"""Phase F 7.8 — the intake CLI, and the runbook chapter it is checked against.

Three things are being pinned here, and the third is the one that matters most:

1. The output is machine-parseable and the exit code is meaningful — a command
   whose exit code always says 0 cannot gate anything.
2. `not-tradable` REFUSES. Exposing the rule as a command means an operator can
   check it without writing a test, and the refusal has to be the answer.
3. Every evidence id the runbook cites is derivable from `evidence-index`. A
   runbook that cites an id nothing produces is documentation of a system that
   does not exist — and task 5.13 already established that a runbook naming a
   switch the code lacks gets trusted rather than obeyed.
"""

from __future__ import annotations

import json
import re

import pytest

from ats.config import REPO_ROOT
from ats.runtime import cli
from ats.workflow import consumer_disposition as cd
from ats.workflow import intake_verification as iv

def _run(capsys, argv):
    code = cli.main(argv)
    out = capsys.readouterr().out
    try:
        return code, json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return code, out


# --------------------------------------------------------------------------- #
# the entry point
# --------------------------------------------------------------------------- #

def test_intake_is_its_own_command():
    with pytest.raises(SystemExit):
        cli.main(["intake", "--help"])


def test_verify_emits_one_machine_readable_record_per_consumer(capsys):
    code, payload = _run(capsys, ["intake", "verify"])
    assert code == 1, "non-compliance must be a non-zero exit"
    assert len(payload) == 10
    assert {row["consumer_id"] for row in payload} == set(iv.TEN_CONSUMERS)
    for row in payload:
        assert set(row) >= {"consumer_id", "entry_point", "call_path",
                            "violations", "compliant", "gaps"}


def test_verify_can_narrow_to_one_consumer(capsys):
    code, payload = _run(capsys, ["intake", "verify", "--consumer-id", "macro"])
    assert len(payload) == 1
    assert payload[0]["consumer_id"] == "macro"


def test_verify_refuses_an_unknown_consumer(capsys):
    with pytest.raises(SystemExit):
        cli.main(["intake", "verify", "--consumer-id", "not_a_consumer"])
    capsys.readouterr()


def test_verify_reports_the_retained_read_with_its_line(capsys):
    """Task 7.9 stopped the read but kept the code for the plan-A restore.

    The CLI must still show it, with a location — a disabled path reported without
    one cannot be found again when plan A restores it.
    """
    _, payload = _run(capsys, ["intake", "verify", "--consumer-id", "trader"])
    kinds = {v["kind"] for v in payload[0]["violations"]}
    assert "provider_bypass" not in kinds, (
        "the reference-price read is live again; only the plan-A restore, which "
        "must declare MARKET_DATA for trader, may make it reachable")
    assert "disabled_bypass" in kinds
    disabled = next(v for v in payload[0]["violations"]
                    if v["kind"] == "disabled_bypass")
    assert ":" in disabled["location"]
    assert disabled["detail"]


def test_verify_accepts_a_declared_opinion_input(capsys):
    _, clean = _run(capsys, ["intake", "verify", "--consumer-id", "sector",
                             "--opinion-input", "layer"])
    assert clean[0]["compliant"] is True, clean[0]["violations"]

    _, leaked = _run(capsys, ["intake", "verify", "--consumer-id", "sector",
                              "--opinion-input", "macro"])
    kinds = {v["kind"] for v in leaked[0]["violations"]}
    assert "opinion_leakage" in kinds


def test_report_renders_a_table_and_names_the_failures(capsys):
    code, text = _run(capsys, ["intake", "report"])
    assert code == 1
    assert "# 十角色接入核验" in text
    for consumer_id in iv.TEN_CONSUMERS:
        assert f"`{consumer_id}`" in text
    assert "不合规明细" in text


def test_digest_is_stable_and_names_the_evidence(capsys):
    _, first = _run(capsys, ["intake", "digest"])
    _, second = _run(capsys, ["intake", "digest"])
    assert first["digest"] == second["digest"]
    assert first["evidence_id"].startswith("intake-verification:")
    assert first["consumers"] == 10


# --------------------------------------------------------------------------- #
# the refusal, exposed as a command
# --------------------------------------------------------------------------- #

def test_not_tradable_refuses_and_explains_why(capsys):
    """`7.1` third case. The command exists so the rule is checkable without a
    test — and it has to answer "no"."""
    code, payload = _run(capsys, ["intake", "not-tradable", "--consumer-id",
                                  "trader"])
    assert code == 1
    assert payload["tradable"] is False
    assert "production ledger" in payload["reason"]
    assert "qualification()" in payload["reason"]


def test_not_tradable_is_the_only_answer_it_can_give(capsys):
    _, payload = _run(capsys, ["intake", "not-tradable"])
    assert payload["tradable"] is False


# --------------------------------------------------------------------------- #
# disposition
# --------------------------------------------------------------------------- #

def test_disposition_renders_for_all_ten_by_default(capsys):
    code, text = _run(capsys, ["intake", "disposition"])
    assert code == 0
    for consumer_id in iv.TEN_CONSUMERS:
        assert f"`{consumer_id}`" in text
    assert "缺项只阻断受影响范围" in text


def test_disposition_accepts_a_missing_optional_input_without_downgrading(capsys):
    _, text = _run(capsys, ["intake", "disposition", "--optional-input",
                            "sec_edgar_filing_body"])
    assert "`fundamental` | ok" in text, (
        "an accepted optional input must not be reclassified by a run that did "
        "not see it")


def test_disposition_refuses_an_age_downgrade_of_a_final_version_source(capsys):
    _, text = _run(capsys, ["intake", "disposition", "--stale-input",
                            "company_financials"])
    assert "refused the age-based downgrade" in text


# --------------------------------------------------------------------------- #
# the evidence index the runbook is checked against
# --------------------------------------------------------------------------- #

def test_evidence_index_covers_every_consumer_with_a_resolvable_id(capsys):
    code, payload = _run(capsys, ["intake", "evidence-index"])
    assert code == 0
    assert set(payload["consumers"]) == set(iv.TEN_CONSUMERS)
    for consumer_id, entry in payload["consumers"].items():
        assert entry["evidence_id"].startswith("intake-verification:")
        assert consumer_id in entry["evidence_id"]
        assert "entry_point" in entry
