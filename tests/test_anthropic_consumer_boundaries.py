"""Consumer-boundary guardrails for Anthropic Economic Index v1.

The first release is research evidence for the L1 Evidence Observer only. A later
change must explicitly define evidence weights before any trading workflow can
consume it.
"""

from __future__ import annotations

from pathlib import Path


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
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                violations.append(f"{path.relative_to(ROOT)} contains {token}")
    assert violations == []
