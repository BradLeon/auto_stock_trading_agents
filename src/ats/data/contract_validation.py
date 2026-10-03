"""Fail-closed structural checks for Target Dataflow/Agent contracts."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

from ..config import REPO_ROOT


EXPECTED_PRODUCTS = {
    "layer": {"HIER_DATA", "DOC_DATA"},
    "information": {"DOC_DATA"},
    "sector": {"HIER_DATA"},
    "fundamental": {"COMPANY_DATA", "HIER_DATA"},
    "macro": {"MACRO_DATA"},
    "technical": {"MARKET_DATA"},
    "chief": {"PORTFOLIO_DATA", "HISTORY_DATA"},
    "risk": {"PORTFOLIO_DATA", "MARKET_DATA", "RISK_RULES"},
    "trader": {"APPROVED_EXECUTION_AUTHORIZATION"},
    "clerk": {"BROKER_STATE", "DECISION_APPROVAL_CONTEXT"},
}
EXPECTED_PROJECTIONS = {
    "layer": set(), "information": set(), "sector": {"layer"},
    "fundamental": {"information"}, "macro": set(), "technical": set(),
    "chief": {"layer", "information", "sector", "fundamental", "macro", "technical"},
    "risk": set(), "trader": set(),
    "clerk": {"chief_decision_revision", "risk_review", "boss_approval"},
}
FORBIDDEN_AGENT_IMPORTS = (
    "ats.data.pipelines.structured.ingestion",
    "ats.data.sources",
    "yfinance",
    "requests",
    "httpx",
    "urllib.request",
)


def _agent_import_violations(root: Path) -> list[str]:
    """Prevent Agent/Workflow modules from owning providers or ingest writers."""
    violations: list[str] = []
    for area in (root / "src/ats/agents", root / "src/ats/workflow"):
        if not area.exists():
            continue
        for path in sorted(area.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError) as exc:
                violations.append(f"unreadable_agent_module:{path.relative_to(root)}:{type(exc).__name__}")
                continue
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                for module in modules:
                    if any(module == forbidden or module.startswith(forbidden + ".")
                           for forbidden in FORBIDDEN_AGENT_IMPORTS):
                        violations.append(
                            f"agent_provider_or_ingestion_import:{path.relative_to(root)}:{module}")
    return violations


def validate_target_contract(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path else REPO_ROOT / "config/data/target_dataflow_coverage.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    errors: list[str] = _agent_import_violations(REPO_ROOT)
    consumers = {str(row.get("id")): row for row in raw.get("consumers", [])}
    if set(consumers) != set(EXPECTED_PRODUCTS):
        errors.append("consumer_roles_must_match_target_ten_roles")
    for consumer_id, required in EXPECTED_PRODUCTS.items():
        row = consumers.get(consumer_id)
        if row is None:
            continue
        products = set(row.get("products") or [])
        if products != required:
            errors.append(f"{consumer_id}:product_contract_mismatch")
        if set(row.get("allowed_projection_inputs") or []) != EXPECTED_PROJECTIONS[consumer_id]:
            errors.append(f"{consumer_id}:projection_contract_mismatch")
        read_api = str(row.get("read_api") or "").lower()
        if any(token in read_api for token in ("ats.memory", "sqlite", ".tables.", "provider")):
            errors.append(f"{consumer_id}:forbidden_data_read_api")
        required_fields = ("read_api", "owner", "vintage", "completeness", "fallback", "legacy", "verify")
        if any(not row.get(field) for field in required_fields):
            errors.append(f"{consumer_id}:incomplete_read_contract")

    nodes = {str(row.get("id")): row for row in raw.get("nodes", [])}
    for obsolete in ("WORKFLOW", "EVIDENCE_OBSERVER", "evidence_observer"):
        if obsolete in nodes:
            errors.append(f"obsolete_or_generic_node:{obsolete}")
    if nodes.get("UNSTRUCT_SRC", {}).get("config") != "config/data/unstructured.yaml":
        errors.append("unstructured_source_registry_pointer_mismatch")
    required_projection_edges = {
        ("AGENT_LAYER", "AGENT_SECTOR"),
        ("AGENT_INFORMATION", "AGENT_FUNDAMENTAL"),
    }
    projection_edges = {
        (str(edge.get("from")), str(edge.get("to")))
        for edge in raw.get("edges", [])
        if str(edge.get("id", "")).startswith("analysis_projection_")
    }
    if projection_edges != required_projection_edges:
        errors.append("analysis_projection_edges_must_be_layer_sector_and_information_fundamental")
    return {"valid": not errors, "manifest": str(manifest_path), "errors": errors,
            "consumer_count": len(consumers), "projection_edges": sorted(projection_edges)}


def main() -> int:
    import json

    result = validate_target_contract()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
