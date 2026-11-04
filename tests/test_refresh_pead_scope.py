"""Refresh jobs take their company lists from config/pead.yaml (hermetic: no network).

2026-11-04: the consensus, financials and chain-evidence jobs carried hand copies of the PEAD names; names added to
`targets` got no data and names that had left PEAD (CRDO, KLAC) kept being collected. The user's rule: collection
stays inside the PEAD lists, so one company is collected once and every analysis reads the same data.
"""

import pytest

from ats.config import load_pead_global
from ats.data import refresh
from ats.data.refresh import load_plan, stale_exclusions


def _entities(job: str) -> list[str]:
    return [j.entity for j in load_plan().jobs if j.id.startswith(f"{job}.")]


def test_a_selection_expands_the_pead_lists_in_order_without_the_excluded(monkeypatch):
    monkeypatch.setattr("ats.config.load_pead_global",
                        lambda: {"targets": ["GOOG", "TSLA", "MU"], "observe": ["MU", "META"]})
    assert refresh._resolve_entities("j", {"from_pead": ["targets", "observe"], "exclude": ["tsla"]}) == [
        "GOOG", "MU", "META"]
    assert refresh._resolve_entities("j", ["A", "B"]) == ["A", "B"]


def test_a_selection_must_name_a_pead_list():
    with pytest.raises(ValueError, match="from_pead"):
        refresh._resolve_entities("j", {"from_pead": ["holdings"]})


def test_an_exclusion_that_left_the_lists_is_reported_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr("ats.config.load_pead_global", lambda: {"targets": ["GOOG"], "observe": []})
    schedule = tmp_path / "schedules.yaml"
    schedule.write_text("version: 1\nderived:\n  jobs:\n    chain:\n"
                        "      entities: {from_pead: [targets], exclude: [KLAC]}\n")
    assert refresh._resolve_entities("chain", {"from_pead": ["targets"], "exclude": ["KLAC"]}) == ["GOOG"]
    assert stale_exclusions(schedule) == {"chain": ["KLAC"]}


def test_the_shipped_schedule_has_no_stale_exclusion():
    assert stale_exclusions() == {}


def test_every_target_gets_consensus_and_financials():
    targets = [str(s).upper() for s in load_pead_global()["targets"]]
    assert _entities("consensus_daily") == targets
    assert _entities("company_financials_weekly") == targets
    foreign = set(_entities("company_financials_issuer_ir_daily"))
    assert set(_entities("company_financials_sec_daily")) == set(targets) - foreign


def test_chain_evidence_stays_inside_the_pead_lists():
    pead = load_pead_global()
    covered = {str(s).upper() for s in pead["targets"] + pead["observe"]}
    chain = set(_entities("chain_evidence_daily"))
    assert chain and chain <= covered
    assert not chain & {"TSLA", "AAPL"}          # followed outside the AI-hardware chain
