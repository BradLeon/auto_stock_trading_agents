"""Consumer-boundary guardrails for Anthropic Economic Index v1.

The first release is research evidence for the L1 Evidence Observer only. A later
change must explicitly define evidence weights before any trading workflow can
consume it.
"""

from __future__ import annotations

from pathlib import Path
import copy
import yaml


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    "anthropic_economic_index",
    "ai_work_adoption",
    "ai_job_profile",
    "AnthropicEconomicIndexAdapter",
)

WORKFLOW_PATHS = (
    ROOT / "src" / "ats" / "agents" / "pead",
    ROOT / "src" / "ats" / "agents" / "macro",
    ROOT / "src" / "ats" / "agents" / "sector",
    ROOT / "src" / "ats" / "agents" / "chief",
    ROOT / "src" / "ats" / "chain",
    ROOT / "src" / "ats" / "graph" / "pead.py",
    ROOT / "src" / "ats" / "graph" / "pead_state.py",
    ROOT / "src" / "ats" / "graph" / "chief.py",
    ROOT / "src" / "ats" / "graph" / "chief_state.py",
    ROOT / "src" / "ats" / "trader",
)


def _python_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.py")) if path.exists() else []


def test_anthropic_job_adoption_is_not_a_trading_workflow_dependency():
    violations: list[str] = []
    for scope in WORKFLOW_PATHS:
        for path in _python_files(scope):
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)} contains {token}")
    assert violations == []


def test_anthropic_job_adoption_has_no_default_trading_weight_or_route():
    violations: list[str] = []
    structured_catalog = ROOT / "config" / "data" / "structured.yaml"
    for path in sorted((ROOT / "config").rglob("*.yaml")):
        if path == structured_catalog:
            continue
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        if path == ROOT / "config/data/target_dataflow_coverage.yaml":
            body = _active_manifest(body)
        text = yaml.safe_dump(body)
        for token in FORBIDDEN:
            if token in text:
                violations.append(f"{path.relative_to(ROOT)} contains {token}")
    assert violations == []


def _active_manifest(body):
    body = copy.deepcopy(body)
    # Retirement dispositions and hashed source paths do not activate a route.
    retired = body.pop("retired_consumers", {})
    assert all(row["status"] == "retired" and row["standalone_role"] == "forbidden"
               for row in retired.values())
    policy = body.get("qualification_policy", {})
    policy.pop("required_fingerprint_paths", None)
    policy.pop("consumer_fingerprint_paths", None)
    return body


def test_active_trading_weight_is_not_hidden_by_manifest_metadata():
    body = yaml.safe_load((ROOT / "config/data/target_dataflow_coverage.yaml").read_text())
    body["trading_weights"] = {"ai_work_adoption": 1}
    assert "ai_work_adoption" in yaml.safe_dump(_active_manifest(body))
