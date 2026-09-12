"""Registry and dispatcher for independently runnable, read-only Evidence layers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .work_adoption import (
    PRODUCTION_CLAIM_ID,
    observe_ai_production_penetration,
    render_ai_production_markdown,
)

LayerObserver = Callable[..., dict[str, Any]]


def _production_observer(**kwargs: Any) -> dict[str, Any]:
    return observe_ai_production_penetration(**kwargs)


# Config owns which layer runs an Observer. This registry only resolves a reviewed
# runner key to its implementation; it must never encode a sector/layer scope.
OBSERVER_RUNNERS: dict[str, tuple[str, LayerObserver]] = {
    "ai_production_penetration": (PRODUCTION_CLAIM_ID, _production_observer),
}


def _configured_observers(
    observer_refs: list[Any],
) -> tuple[dict[str, LayerObserver], dict[str, Any] | None]:
    """Resolve enabled config declarations, rejecting ambiguous or unknown runners."""
    enabled = [ref for ref in observer_refs if getattr(ref, "enabled", True)]
    claim_ids = [str(getattr(ref, "claim_id", "")) for ref in enabled]
    duplicates = sorted({claim_id for claim_id in claim_ids if claim_ids.count(claim_id) > 1})
    if duplicates:
        return {}, {
            "status": "invalid_observer_configuration",
            "duplicate_claim_ids": duplicates,
            "reason": "同一 layer 的已启用 evidence_observers 不得重复声明 claim_id。",
        }
    resolved: dict[str, LayerObserver] = {}
    for ref in enabled:
        claim_id = str(getattr(ref, "claim_id", ""))
        runner = str(getattr(ref, "runner", ""))
        candidate = OBSERVER_RUNNERS.get(runner)
        if candidate is None:
            return {}, {
                "status": "unknown_observer_runner",
                "claim_id": claim_id,
                "runner": runner,
                "reason": "layer 声明了当前运行时不支持的 Evidence runner。",
            }
        expected_claim_id, observer = candidate
        if claim_id != expected_claim_id:
            return {}, {
                "status": "invalid_observer_configuration",
                "claim_id": claim_id,
                "runner": runner,
                "expected_claim_id": expected_claim_id,
                "reason": "claim_id 必须与 runner 的固定受治理命题一致。",
            }
        definition_version = str(getattr(ref, "claim_definition_version", "") or "v2")
        supplemental_sources = tuple(getattr(ref, "supplemental_sources", ()) or ())
        supplemental_claims = tuple(getattr(ref, "supplemental_claims", ()) or ())
        def configured_observer(*, _observer=observer, _version=definition_version,
                                _supplemental_sources=supplemental_sources,
                                _supplemental_claims=supplemental_claims, **kwargs):
            if _supplemental_sources or _supplemental_claims:
                scope = dict(kwargs.get("workflow_scope") or {})
                if _supplemental_sources:
                    scope["supplemental_sources"] = list(_supplemental_sources)
                if _supplemental_claims:
                    scope["supplemental_claims"] = [dict(item) for item in _supplemental_claims]
                kwargs["workflow_scope"] = scope
            return _observer(claim_definition_version=_version, **kwargs)
        resolved[claim_id] = configured_observer
    return resolved, None


def run_registered_layer_observers(
    *,
    sector: str,
    sector_label: str,
    layer: str,
    layer_label: str,
    observer_refs: list[Any] | None = None,
    periods: list[str] | None = None,
    as_of=None,
    chart_dir: str = "",
    claim_ids: list[str] | None = None,
    products=None,
) -> dict[str, Any]:
    """Run only the read-only Evidence observers registered for one layer."""
    scope = {
        "sector": sector,
        "sector_label": sector_label,
        "layer": layer,
        "layer_label": layer_label,
    }
    registered, configuration_error = _configured_observers(observer_refs or [])
    if configuration_error is not None:
        return {
            **configuration_error,
            "workflow_scope": scope,
            "packets": [],
        }
    requested = claim_ids or sorted(registered)
    unavailable = sorted(set(requested) - set(registered))
    if unavailable:
        return {
            "status": "claim_not_registered_for_scope",
            "workflow_scope": scope,
            "packets": [],
            "unavailable_claim_ids": unavailable,
            "reason": "请求的 claim 没有注册在该层。",
        }
    if not registered:
        return {
            "status": "no_registered_observers",
            "workflow_scope": scope,
            "packets": [],
            "reason": "该层已配置，但当前没有已注册的只读 Evidence Observer。",
        }
    packets = []
    for claim_id in requested:
        observer = registered[claim_id]
        child_chart_dir = ""
        if chart_dir:
            child_chart_dir = str(Path(chart_dir) / claim_id) if len(requested) > 1 else chart_dir
        packets.append(
            observer(
                periods=periods,
                as_of=as_of,
                chart_dir=child_chart_dir,
                workflow_scope=scope,
                products=products,
            )
        )
    return {
        "status": "ok",
        "workflow_scope": scope,
        "packets": packets,
        "registered_claim_ids": sorted(registered),
    }


def render_layer_evidence_markdown(result: dict[str, Any]) -> str:
    """Render a layer run; individual claim renderers remain domain-owned."""
    if result.get("status") != "ok":
        scope = result.get("workflow_scope", {})
        return (
            f"# Evidence：{scope.get('sector_label', scope.get('sector', ''))} / "
            f"{scope.get('layer_label', scope.get('layer', ''))}\n\n"
            f"状态：`{result.get('status', 'unavailable')}`\n\n"
            f"{result.get('reason', '')}\n"
        )
    packets = result.get("packets", [])
    if len(packets) == 1 and packets[0].get("claim_id") == PRODUCTION_CLAIM_ID:
        return render_ai_production_markdown(packets[0])
    scope = result.get("workflow_scope", {})
    parts = [
        (
            f"# Evidence：{scope.get('sector_label', scope.get('sector', ''))} / "
            f"{scope.get('layer_label', scope.get('layer', ''))}"
        ),
        "",
    ]
    for packet in packets:
        parts.extend([f"## {packet.get('claim_id', 'unknown_claim')}", ""])
        parts.extend(packet.get("facts", []) or ["无可展示事实。"])
        parts.append("")
    return "\n".join(parts)


def write_layer_evidence_markdown(result: dict[str, Any], output: str | Path) -> Path:
    """Persist the human review artifact outside the DataProducts query boundary."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_layer_evidence_markdown(result), encoding="utf-8")
    return path
