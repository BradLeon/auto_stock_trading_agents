"""Contracts for independently runnable, read-only Evidence layers."""

from __future__ import annotations

import json

from ats.agents.evidence.layer_runner import run_registered_layer_observers
from ats.config import load_sector_config
from ats.runtime.cli import run_evidence
from ats.schemas.sector import EvidenceObserverRef


class _Layer:
    def __init__(self, key: str, label: str, evidence_observers=None):
        self.key = key
        self.label = label
        self.evidence_observers = evidence_observers or []


class _Config:
    name = "ai_hardware"
    label = "AI硬件"

    def __init__(self, output_dir: str):
        self.output_dir = output_dir

    @staticmethod
    def layer_by_key(key):
        if key == "L1_app":
            return _Layer(
                "L1_app",
                "L1 AI应用层（Token经济）",
                [
                    EvidenceObserverRef(
                        claim_id="ai_core_production_workflow_penetration",
                        runner="ai_production_penetration",
                    )
                ],
            )
        if key == "L2_compute":
            return _Layer("L2_compute", "L2 算力层")
        return None


def test_layer_dispatcher_runs_only_requested_scope(monkeypatch):
    called = []

    def _l1(**kwargs):
        called.append(("L1_app", kwargs["workflow_scope"]))
        return {"claim_id": "l1_claim", "status": "ok"}

    def _other(**kwargs):
        called.append(("other", kwargs["workflow_scope"]))
        return {"claim_id": "other_claim", "status": "ok"}

    monkeypatch.setattr(
        "ats.agents.evidence.layer_runner.OBSERVER_RUNNERS",
        {"l1_runner": ("l1_claim", _l1), "other_runner": ("other_claim", _other)},
    )
    result = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L1_app",
        layer_label="L1 AI应用层",
        observer_refs=[EvidenceObserverRef(claim_id="l1_claim", runner="l1_runner")],
    )
    assert result["status"] == "ok"
    assert [item[0] for item in called] == ["L1_app"]
    assert called[0][1]["layer"] == "L1_app"


def test_layer_dispatcher_distinguishes_no_observer_from_wrong_claim(monkeypatch):
    monkeypatch.setattr("ats.agents.evidence.layer_runner.OBSERVER_RUNNERS", {})
    empty = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L2_compute",
        layer_label="L2 算力层",
    )
    wrong_claim = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L2_compute",
        layer_label="L2 算力层",
        claim_ids=["ai_core_production_workflow_penetration"],
    )
    assert empty["status"] == "no_registered_observers"
    assert wrong_claim["status"] == "claim_not_registered_for_scope"


def test_layer_dispatcher_rejects_unknown_or_duplicate_configured_runner(monkeypatch):
    monkeypatch.setattr("ats.agents.evidence.layer_runner.OBSERVER_RUNNERS", {})
    unknown = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L1_app",
        layer_label="L1 AI应用层",
        observer_refs=[EvidenceObserverRef(claim_id="x", runner="unknown")],
    )
    duplicate = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L1_app",
        layer_label="L1 AI应用层",
        observer_refs=[
            EvidenceObserverRef(claim_id="same", runner="unknown"),
            EvidenceObserverRef(claim_id="same", runner="unknown"),
        ],
    )
    assert unknown["status"] == "unknown_observer_runner"
    assert duplicate["status"] == "invalid_observer_configuration"


def test_disabled_configured_observer_is_not_registered(monkeypatch):
    monkeypatch.setattr(
        "ats.agents.evidence.layer_runner.OBSERVER_RUNNERS",
        {"known": ("claim", lambda **_kwargs: {"claim_id": "claim", "status": "ok"})},
    )
    result = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L1_app",
        layer_label="L1 AI应用层",
        observer_refs=[EvidenceObserverRef(claim_id="claim", runner="known", enabled=False)],
    )
    assert result["status"] == "no_registered_observers"


def test_ai_hardware_l1_declares_data_observer_outside_chain_claims():
    l1 = load_sector_config("ai_hardware").layer_by_key("L1_app")
    assert l1 is not None
    assert [(item.claim_id, item.runner) for item in l1.evidence_observers] == [
        ("ai_core_production_workflow_penetration", "ai_production_penetration")
    ]
    assert "ai_core_production_workflow_penetration" not in {claim.id for claim in l1.claims}


def test_layer_workflow_writes_default_markdown_and_shortcut_uses_same_dispatcher(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(
        "ats.memory.get_store",
        lambda: (_ for _ in ()).throw(
            AssertionError("Layer workflow must not read legacy Evidence memory")
        ),
    )
    monkeypatch.setattr("ats.config.load_sector_config", lambda _sector: _Config(str(tmp_path)))
    captured = []

    def _run(**kwargs):
        captured.append(kwargs)
        return {
            "status": "no_registered_observers",
            "workflow_scope": {
                "sector": "ai_hardware",
                "sector_label": "AI硬件",
                "layer": "L2_compute",
                "layer_label": "L2 算力层",
            },
            "packets": [],
            "reason": "fixture",
        }

    monkeypatch.setattr("ats.agents.evidence.run_registered_layer_observers", _run)
    rc = run_evidence("layer", sector="ai_hardware", layer="L2_compute")
    output = capsys.readouterr().out
    written = next(tmp_path.glob("Evidence-ai_hardware-L2_compute-*.md"))
    assert rc == 0
    assert "已写入层级 Evidence 审阅文档" in output
    assert written.exists() and "no_registered_observers" in written.read_text()
    assert captured[0]["claim_ids"] is None

    rc = run_evidence(
        "ai-production",
        sector="ai_hardware",
        layer="L2_compute",
        output=str(tmp_path / "shortcut.md"),
    )
    assert rc == 0
    assert captured[1]["claim_ids"] == ["ai_core_production_workflow_penetration"]


def test_layer_workflow_fails_for_invalid_scope_and_keeps_json_machine_interface(capsys):
    rc = run_evidence("layer", sector="", layer="L1_app")
    output = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert output["status"] == "invalid_scope"
