"""Target role input contracts. Reads only; no collection or Agent reasoning.

Research reads are point-in-time. Runtime and ledger reads explicitly remain
current-only, never pretending they can reconstruct a historical live quote.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..config import REPO_ROOT
from .contract_validation import EXPECTED_INPUT_MODES, EXPECTED_PRODUCTS

PRODUCT_MODES = EXPECTED_INPUT_MODES


class ConsumerInput(BaseModel):
    schema_version: Literal["target-consumer-input-v1"] = "target-consumer-input-v1"
    contract_version: str
    consumer: str
    product: str
    owner: str
    input_mode: Literal["persistent", "runtime", "internal", "configuration", "authorization"]
    scope: dict = Field(default_factory=dict)
    as_of: datetime
    queried_at: datetime
    source_as_of: list[str] = Field(default_factory=list)
    status: Literal["complete", "partial", "no_coverage", "unavailable", "rejected"]
    completeness: str
    input_refs: list[str] = Field(default_factory=list)
    fallback: str
    gaps: list[str] = Field(default_factory=list)
    payload: Any = None


def input_contract(consumer: str, product: str) -> dict:
    if product not in EXPECTED_PRODUCTS.get(consumer, set()):
        raise PermissionError(f"undeclared_data_input:{consumer}:{product}")
    manifest = yaml.safe_load((REPO_ROOT / "config/data/target_dataflow_coverage.yaml").read_text())
    role = next(row for row in manifest["consumers"] if row["id"] == consumer)
    edge = manifest["data_input_contracts"][product]
    if edge.get("input_mode") != PRODUCT_MODES[product] or edge.get("schema_version") != "target-consumer-input-v1":
        raise ValueError(f"invalid_data_edge_contract:{product}")
    if consumer not in edge["allowed_consumers"]:
        raise PermissionError(f"consumer_not_authorized:{consumer}:{product}")
    return {**edge, "consumer": consumer, "product": product,
            "contract_version": role["contract_version"],
            "fallback": role["fallback"], "legacy": role["legacy"],
            "as_of_semantics": role["vintage"], "completeness_semantics": role["completeness"]}


def _json(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _json(asdict(value))
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def assert_research_payload(payload) -> None:
    """Refuse opinion/ledger/runtime envelopes in persistent research packets."""
    if isinstance(payload, dict):
        if payload.get("input_mode") in {"runtime", "internal", "authorization"} or any(
                key in payload for key in ("agent_role", "decision_hash", "approval_id", "cycle_id")):
            raise ValueError("non_research_input_in_persistent_packet")
        for value in payload.values():
            assert_research_payload(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            assert_research_payload(value)


def read_input(consumer: str, product: str, *, scope: dict, as_of: datetime | None = None,
               products=None, store=None, broker=None, audit=None, risk_config=None) -> ConsumerInput:
    """Load one declared edge from its governed API, without hidden refresh.

    Execution dependencies must be supplied explicitly. No broker connection,
    order submission, decision revision, model call or scheduler action occurs.
    """
    contract = input_contract(consumer, product)
    queried_at = datetime.now(timezone.utc)
    point = as_of or queried_at
    if point.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    payload, refs, stamps, gaps = None, [], [], []
    status = "unavailable"
    mode = contract["input_mode"]
    if mode in {"runtime", "internal", "authorization", "configuration"} and as_of is not None:
        # Do not reinterpret a historical cutoff as today's broker/account state.
        raise ValueError("current_only_input_does_not_support_historical_as_of")
    try:
        if product in {"HIER_DATA", "COMPANY_DATA", "MACRO_DATA", "DOC_DATA"}:
            if products is None:
                from .products import get_platform_data_products
                products = get_platform_data_products(readonly=True)
                owned_products = True
            else:
                owned_products = False
            try:
                if product != "DOC_DATA" and scope.get("kind") == "evidence":
                    payload = products.neutral_evidence(entity=scope["entity"], as_of=point,
                                                        limit=scope.get("limit", 500))
                    rows = payload["rows"]
                    refs = [row["fact_id"] for row in rows]
                    stamps = [row["observed_at"] for row in rows]
                    status = "partial" if payload["rejected"] else "complete" if rows else "no_coverage"
                elif product == "DOC_DATA" or scope.get("kind") == "documents":
                    from .products.unstructured import admitted_documents
                    diagnostics = []
                    payload = _json(admitted_documents(
                        entities=scope.get("entities") or ([scope["entity"]] if scope.get("entity") else None),
                        document_types=scope.get("document_types"), as_of=point,
                        repository=products.unstructured, limit=scope.get("limit", 200), diagnostics=diagnostics))
                    refs = [f"{row['document_id']}@{row['version_id']}" for row in payload]
                    refs.extend(row["publication_id"] for row in payload if row.get("publication_id"))
                    stamps = [row["known_at"] or row["fetched_at"] for row in payload]
                    status = ("complete" if payload and all(row["completeness"] == "full" for row in payload)
                              else "partial" if payload else "no_coverage")
                    if diagnostics:
                        status = "partial" if payload else "unavailable"
                        gaps.extend(f"{item['reason']}:{item['document_id']}" for item in diagnostics)
                elif scope.get("kind") == "consensus":
                    payload = products.consensus_snapshot(entity=scope["entity"], as_of=point)
                    status = "complete" if payload["rows"] else "no_coverage"
                    refs = [row["observation_id"] for row in payload["rows"]]
                    stamps = [row["known_at"] for row in payload["rows"]]
                else:
                    payload = products.metric_series(entity=scope["entity"], metric=scope["metric"],
                                                      dataset=scope["dataset"], as_of=point,
                                                      max_age_hours=scope.get("max_age_hours"))
                    rows = payload.get("rows") or []
                    status = "complete" if rows and payload.get("status") == "ok" else "partial" if rows else "no_coverage"
                    refs = [row["observation_id"] for row in rows]
                    stamps = [row["known_at"] for row in rows]
                assert_research_payload(payload)
            finally:
                if owned_products:
                    products.structured.close()
                    products.unstructured.close()
        elif product in {"PORTFOLIO_DATA", "HISTORY_DATA"}:
            from ..execution.state_api import get_internal_state
            if store is None:
                raise ValueError("internal_store_required")
            state = get_internal_state(store, symbol=scope.get("entity", ""))
            if product == "PORTFOLIO_DATA":
                payload = state.portfolio
                stamp = state.section_as_of["portfolio"]
                status = state.section_status["portfolio"]
            else:
                payload = {"trades": state.trades, "fills": state.fills,
                           "performance": state.performance, "attribution": state.attribution}
                stamp = state.completeness.last_reconcile_at
                status = "complete" if stamp and state.completeness.status == "complete" else "partial"
            stamps = [stamp] if stamp else []
            if state.completeness.status != "complete":
                status = "partial"
                gaps.append("ledger_degraded:" + state.completeness.model_dump_json())
            refs = [f"internal-snapshot:{product}:{stamp}"] if stamp else []
            if not stamp:
                gaps.append("source_timestamp_or_reconciliation_missing")
        elif product == "MARKET_DATA":
            if scope.get("kind") == "options":
                from .runtime.options import fetch_runtime
                result = fetch_runtime(scope["entity"], scope.get("earnings_date"))
                payload, status = result["payload"], result["status"]
                stamps = [result["source_as_of"]] if result["source_as_of"] else []
                if result["reason"]:
                    gaps.append(result["reason"])
            else:
                from .runtime.market_data import fetch_close_history_many
                payload = _json(fetch_close_history_many(scope.get("entities") or [scope["entity"]]))
                good = [row for row in payload.values() if row["status"] == "succeeded"
                        and row.get("bar_as_of") and row.get("closes")]
                stamps = [row["bar_as_of"] for row in good if row["bar_as_of"]]
                status = "complete" if good and len(good) == len(payload) else "partial" if good else "unavailable"
        elif product == "RISK_RULES":
            from ..config import get_config
            cfg = risk_config if risk_config is not None else get_config().app.risk
            payload = _json(cfg)
            import json
            refs = ["ruleset:" + sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()]
            status = "complete"
        elif product == "BROKER_STATE":
            from .runtime.broker import broker_state
            payload = broker_state(broker)
            status = payload["status"]
            stamps = [payload["source_as_of"]] if payload["source_as_of"] else []
            refs = [f"broker-snapshot:{payload['source_as_of']}"] if stamps else []
            if payload["reason"]:
                gaps.append(payload["reason"])
        elif product == "DECISION_APPROVAL_CONTEXT":
            if audit is None:
                raise ValueError("decision_audit_required")
            payload = (audit.read_chain(scope["cycle_id"]) if audit.get_cycle(scope["cycle_id"])
                       else {"cycle": None})
            status = "complete" if payload["cycle"] else "no_coverage"
            refs = [scope["cycle_id"]] if payload["cycle"] else []
            stamps = [str(payload["cycle"]["updated_at"])] if payload["cycle"] else []
        elif product == "APPROVED_EXECUTION_AUTHORIZATION":
            from ..execution.authorization import build_authorization, validate_authorization
            if audit is None:
                raise ValueError("decision_audit_required")
            auth = build_authorization(audit, scope["cycle_id"])
            snapshot_as_of = scope.get("snapshot_as_of")
            if isinstance(snapshot_as_of, str):
                snapshot_as_of = datetime.fromisoformat(snapshot_as_of.replace("Z", "+00:00"))
            if snapshot_as_of is not None and snapshot_as_of.tzinfo is None:
                raise ValueError("snapshot_as_of must be timezone-aware")
            reasons = validate_authorization(audit, auth, snapshot_as_of=snapshot_as_of,
                                             now=queried_at)
            if reasons:
                gaps.extend(reasons)
                status = "rejected"
            else:
                payload, status = _json(auth), "complete"
                refs = [auth.decision_hash, auth.review_id, auth.approval_id]
                stamps = [auth.market_as_of, auth.approval_at]
    except Exception as exc:
        # Data read failures never leave a previously assembled payload approved.
        payload, refs, stamps, status = None, [], [], "unavailable"
        gaps.append(f"input_unavailable:{type(exc).__name__}:{exc}")
    if status not in {"complete"} and not gaps:
        gaps.append("missing_or_partial_input")
    return ConsumerInput(consumer=consumer, product=product, owner=contract["owner"],
                         contract_version=contract["contract_version"], input_mode=mode,
                         scope=_json(scope), as_of=point, queried_at=queried_at,
                         source_as_of=sorted(set(stamps)), status=status,
                         completeness=status, input_refs=refs, fallback=contract["fallback"],
                         gaps=gaps, payload=_json(payload))
