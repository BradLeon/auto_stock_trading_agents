"""L1 Evidence Observer contract for governed work-adoption data."""

from __future__ import annotations

import ast
from pathlib import Path

from ats.agents.evidence import observe_work_adoption


class _Products:
    def __init__(self):
        self.calls = []

    def ai_work_adoption_snapshot(self, **kwargs):
        self.calls.append(("snapshot", kwargs))
        return {
            "status": "ok",
            "period": "2026-05",
            "jobs": [{
                "entity_id": "SOC:15-2031.00",
                "name": "Operations Research Analysts",
                "metrics": {"ai.work_adoption.usage_share": 0.34},
                "lineage": {"input_observation_ids": ["obs-1"]},
            }],
            "manifest": {"snapshot_id": "snapshot-1"},
        }

    def ai_job_profile(self, occupation, **kwargs):
        self.calls.append(("profile", {"occupation": occupation, **kwargs}))
        return {"status": "ok", "occupation": {"entity_id": f"SOC:{occupation}"}}


def test_observer_uses_snapshot_and_profile_contracts_and_keeps_manifest():
    products = _Products()
    packet = observe_work_adoption(
        source_product="claude_ai", occupation="15-2031.00", products=products)

    assert [name for name, _ in products.calls] == ["snapshot", "profile"]
    assert products.calls[0][1]["snapshot_consumer"] == "evidence_observer"
    assert products.calls[1][1]["period"] == "2026-05"
    assert packet["manifest"] == {"snapshot_id": "snapshot-1"}
    assert packet["source_access"] == "data_products_only"


def test_observer_fact_templates_preserve_semantic_boundaries():
    packet = observe_work_adoption(source_product="1p_api", products=_Products())
    statement = packet["facts"][0]["statement"]

    assert "总使用量" in statement
    assert "不是该职业从业者采用率" in statement
    assert "企业席位渗透率" in packet["semantic_guardrails"]["prohibited_claims"]
    assert "交易信号或证据权重" in packet["semantic_guardrails"]["prohibited_claims"]


def test_observer_consumer_has_no_provider_or_physical_store_dependency():
    path = Path(__file__).resolve().parents[1] / "src" / "ats" / "agents" / "evidence" / "work_adoption.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not any("sources.anthropic" in module or "stores.structured" in module
                   for module in imports)
    assert ".structured" not in source
    assert "sqlite3" not in source and "httpx" not in source
