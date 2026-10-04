"""Fail-closed structural checks for Target Dataflow/Agent contracts."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
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
EXPECTED_INPUT_MODES = {
    "HIER_DATA": "persistent", "DOC_DATA": "persistent", "COMPANY_DATA": "persistent",
    "MACRO_DATA": "persistent", "MARKET_DATA": "runtime", "PORTFOLIO_DATA": "internal",
    "HISTORY_DATA": "internal", "RISK_RULES": "configuration",
    "APPROVED_EXECUTION_AUTHORIZATION": "authorization", "BROKER_STATE": "runtime",
    "DECISION_APPROVAL_CONTEXT": "internal",
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
                elif isinstance(node, ast.ImportFrom):
                    package = ".".join(path.relative_to(root / "src").parent.parts)
                    module = node.module or ""
                    if node.level:
                        try:
                            module = resolve_name("." * node.level + module, package)
                        except (ImportError, ValueError):
                            violations.append(f"invalid_relative_import:{path.relative_to(root)}:{node.lineno}")
                            continue
                    modules.append(module)
                    modules.extend(f"{module}.{alias.name}" for alias in node.names)
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
    policy = raw.get("qualification_policy") or {}
    optional = policy.get("optional_inputs") or {}
    if set(optional) != {"sec_edgar_filing_body"}:
        errors.append("qualification_only_sec_body_may_be_optional")
    sec = optional.get("sec_edgar_filing_body") or {}
    registry = yaml.safe_load((REPO_ROOT / "config/data/unstructured.yaml").read_text())
    source_policy = registry["sources"]["sec_edgar_filing_body"]["policy"]
    if (sec.get("dataset") != "sec_filing_documents" or sec.get("optional") is not True
            or sec.get("blocking") is not False or not sec.get("policy_version")
            or source_policy.get("input_optional") is not True
            or source_policy.get("cutover_blocking") is not False
            or source_policy.get("optional_policy_version") != sec.get("policy_version")):
        errors.append("sec_optional_registry_and_qualification_policy_mismatch")
    if set(sec.get("required_checks", [])) != {
            "empty_input_safe", "error_visible", "no_risk_inference", "invalid_material_rejected"}:
        errors.append("sec_optional_input_checks_must_not_be_waived")
    if set(policy.get("required_inputs", {}).get("fundamental", [])) != {
            "company_financials", "defeatbeta_sec_filing_index", "defeatbeta_earnings_transcript"}:
        errors.append("fundamental_required_sources_must_not_be_waived")
    if set(policy.get("rollback_routes", {})) != set(EXPECTED_INPUT_MODES):
        errors.append("rollback_routes_must_cover_all_products")
    for api in policy.get("rollback_routes", {}).values():
        if not _api_exists(api, REPO_ROOT):
            errors.append(f"nonexistent_rollback_api:{api}")
    consumers = {str(row.get("id")): row for row in raw.get("consumers", [])}
    input_contracts = raw.get("data_input_contracts") or {}
    expected_input_ids = set().union(*EXPECTED_PRODUCTS.values())
    if set(input_contracts) != expected_input_ids:
        errors.append("data_input_contracts_must_cover_all_target_products")
    for product in sorted(expected_input_ids):
        edge = input_contracts.get(product) or {}
        if edge.get("input_mode") != EXPECTED_INPUT_MODES[product]:
            errors.append(f"{product}:input_mode_mismatch")
        if edge.get("schema_version") != "target-consumer-input-v1":
            errors.append(f"{product}:schema_version_mismatch")
        allowed = {role for role, products in EXPECTED_PRODUCTS.items() if product in products}
        if set(edge.get("allowed_consumers") or []) != allowed:
            errors.append(f"{product}:allowed_consumers_mismatch")
        if any(not edge.get(field) for field in
               ("schema_version", "owner", "input_mode", "read_api", "native_api")):
            errors.append(f"{product}:incomplete_edge_contract")
        for api in str(edge.get("read_api", "") + " + " + edge.get("native_api", "")).split("+"):
            if api.strip() and not _api_exists(api.strip(), REPO_ROOT):
                errors.append(f"{product}:nonexistent_read_api:{api.strip()}")
    retirement = (raw.get("retired_consumers") or {}).get("evidence_observer") or {}
    if (retirement.get("status") != "retired" or retirement.get("replaced_by") != "layer"
            or retirement.get("standalone_role") != "forbidden"
            or retirement.get("preserve_historical_lineage") is not True):
        errors.append("observer_retirement_contract_missing_or_invalid")
    registry_datasets: set[str] = set()
    for name in ("structured.yaml", "unstructured.yaml"):
        registry = yaml.safe_load((REPO_ROOT / "config/data" / name).read_text(encoding="utf-8")) or {}
        registry_datasets.update(registry.get("datasets") or {})
    for disposition in retirement.get("dispositions") or []:
        for dataset in disposition.get("datasets") or []:
            if dataset not in registry_datasets:
                errors.append(f"observer_retirement_unknown_dataset:{dataset}")
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


def _api_exists(api: str, root: Path) -> bool:
    """Resolve declarations statically: do not import modules or connect providers."""
    parts = api.split(".")
    for cut in range(len(parts), 0, -1):
        base = root / "src" / Path(*parts[:cut])
        path = base.with_suffix(".py")
        if not path.is_file():
            path = base / "__init__.py"
        if not path.is_file():
            continue
        if cut == len(parts):
            return True
        tree = ast.parse(path.read_text(encoding="utf-8"))
        body = tree.body
        for name in parts[cut:]:
            matched = next((node for node in body if
                            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                            and node.name == name), None)
            if matched is None:
                # Re-exported public APIs are bindings, not runtime introspection.
                return any(isinstance(node, (ast.ImportFrom, ast.Import)) and
                           any((alias.asname or alias.name) == name for alias in node.names)
                           for node in body) and name == parts[-1]
            body = matched.body
        return True
    return False


def main() -> int:
    import json

    result = validate_target_contract()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
