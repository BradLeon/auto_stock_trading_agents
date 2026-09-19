"""Registry and dispatcher for independently runnable, read-only Evidence layers."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

COMMERCIALIZATION_CLAIM_ID = "ai_frontier_labs_commercialization"
RAW_CAPABILITY_CLAIM_ID = "ai_frontier_raw_capability"

from .work_adoption import (
    PRODUCTION_CLAIM_ID,
    observe_ai_production_penetration,
    render_ai_production_markdown,
)

LayerObserver = Callable[..., dict[str, Any]]


def _production_observer(**kwargs: Any) -> dict[str, Any]:
    return observe_ai_production_penetration(**kwargs)


def _commercialization_observer(**kwargs: Any) -> dict[str, Any]:
    from .commercialization import observe_ai_commercialization

    return observe_ai_commercialization(**kwargs)


def _raw_capability_observer(**kwargs: Any) -> dict[str, Any]:
    from .raw_capability import observe_ai_raw_capability

    return observe_ai_raw_capability(**kwargs)


# Config owns which layer runs an Observer. This registry only resolves a reviewed
# runner key to its implementation; it must never encode a sector/layer scope.
OBSERVER_RUNNERS: dict[str, tuple[str, LayerObserver]] = {
    "ai_production_penetration": (PRODUCTION_CLAIM_ID, _production_observer),
    "ai_commercialization": (COMMERCIALIZATION_CLAIM_ID, _commercialization_observer),
}
# Extension runners are resolved by the same governed dispatcher but kept out of
# the legacy mapping's public key set for backward-compatible consumers that use
# it as a two-runner capability probe.
OBSERVER_RUNNER_EXTENSIONS: dict[str, tuple[str, LayerObserver]] = {
    "ai_raw_capability": (RAW_CAPABILITY_CLAIM_ID, _raw_capability_observer),
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
        candidate = OBSERVER_RUNNERS.get(runner) or OBSERVER_RUNNER_EXTENSIONS.get(runner)
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
        # The production observer historically defaulted to v2.  Every other
        # runner states its own version, so a new claim only needs its config to
        # be explicit rather than inheriting an unrelated default.
        definition_version = str(getattr(ref, "claim_definition_version", "") or "v2")
        primary_sources = tuple(getattr(ref, "primary_sources", ()) or ())
        primary_claims = tuple(getattr(ref, "primary_claims", ()) or ())
        supplemental_sources = tuple(getattr(ref, "supplemental_sources", ()) or ())
        supplemental_claims = tuple(getattr(ref, "supplemental_claims", ()) or ())
        evidence_sections = tuple(getattr(ref, "evidence_sections", ()) or ())
        def configured_observer(*, _observer=observer, _version=definition_version,
                                _primary_sources=primary_sources,
                                _primary_claims=primary_claims,
                                _supplemental_sources=supplemental_sources,
                                _supplemental_claims=supplemental_claims,
                                _evidence_sections=evidence_sections, **kwargs):
            if (_primary_sources or _primary_claims or _supplemental_sources
                    or _supplemental_claims or _evidence_sections):
                scope = dict(kwargs.get("workflow_scope") or {})
                if _primary_sources:
                    scope["primary_sources"] = list(_primary_sources)
                if _primary_claims:
                    scope["primary_claims"] = [dict(item) for item in _primary_claims]
                if _supplemental_sources:
                    scope["supplemental_sources"] = list(_supplemental_sources)
                if _supplemental_claims:
                    scope["supplemental_claims"] = [dict(item) for item in _supplemental_claims]
                if _evidence_sections:
                    scope["evidence_sections"] = [str(item) for item in _evidence_sections]
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


def _claim_renderer(claim_id: str) -> Callable[[dict[str, Any]], str] | None:
    if claim_id == PRODUCTION_CLAIM_ID:
        return render_ai_production_markdown
    if claim_id == COMMERCIALIZATION_CLAIM_ID:
        from .commercialization import render_ai_commercialization_markdown

        return render_ai_commercialization_markdown
    if claim_id == RAW_CAPABILITY_CLAIM_ID:
        from .raw_capability import render_ai_raw_capability_markdown

        return render_ai_raw_capability_markdown
    return None


def render_claim_markdown(packet: dict[str, Any]) -> str:
    """Render one claim packet with its own domain renderer."""
    renderer = _claim_renderer(str(packet.get("claim_id") or ""))
    if renderer is not None:
        return renderer(packet)
    parts = [f"# {packet.get('claim_id', 'unknown_claim')}", ""]
    parts.extend(str(item) for item in (packet.get("facts") or ["无可展示事实。"]))
    return "\n".join(parts)


def _observer_conclusion(packet: dict[str, Any]) -> tuple[str, str, str]:
    """Return a compact human conclusion without recomputing governed facts."""
    claim_id = str(packet.get("claim_id") or "")
    status = str(packet.get("overall_status") or packet.get("status") or "unavailable")
    if claim_id == PRODUCTION_CLAIM_ID:
        return ("生产化与应用扩散", status,
                str(packet.get("overall_interpretation") or "四个主证据轴分别报告，当前历史不足。"))
    if claim_id == COMMERCIALIZATION_CLAIM_ID:
        return ("商业化能力", status,
                str(packet.get("overall_interpretation") or packet.get("reason") or "当前证据不足。"))
    if claim_id == RAW_CAPABILITY_CLAIM_ID:
        a_items = list((packet.get("a") or {}).get("benchmarks") or [])
        b_items = list((packet.get("b") or {}).get("benchmarks") or [])
        expanded = sum(item.get("status") in {"confirmed_expansion", "provisional_expansion"}
                       for item in a_items)
        confirmed = sum(item.get("status") == "confirmed_crossing" for item in b_items)
        provisional = sum(item.get("status") == "provisional_crossing" for item in b_items)
        return ("原始能力边界", status,
                f"A：{expanded} 项出现可比前沿外扩；B：{confirmed} 项确认跨越、{provisional} 项仅点估计暂定跨越。")
    return (claim_id or "未知命题", status, str(packet.get("reason") or "无结论。"))


def _partition_claim_markdown(markdown: str, claim_id: str) -> tuple[str, str]:
    """Move diagnostic/method/reproduction sections behind the report spine."""
    appendix_terms = {
        PRODUCTION_CLAIM_ID: ("TOP10", "指标公式、限制与来源", "其他受治理图表", "可复算数据表"),
        COMMERCIALIZATION_CLAIM_ID: ("历史观察表", "来源、口径与冲突", "指标公式、统计范围与数据缺口",
                                     "尚待建设的留存", "附录：OpenRouter Top-10", "可复算数据表"),
        RAW_CAPABILITY_CLAIM_ID: ("事件型评测证据账本", "Benchmark 方法卡", "口径与限制",
                                  "附录：`†/‡` 最近被评测模型明细"),
    }.get(claim_id, ())
    blocks: list[tuple[str, list[str]]] = []
    heading = ""
    current: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            blocks.append((heading, current))
            heading, current = line[3:].strip(), [line]
        else:
            current.append(line)
    blocks.append((heading, current))
    main: list[str] = []
    appendix: list[str] = []
    for title, block in blocks:
        if not title and block and block[0].startswith("# "):
            block = block[1:]
        target = appendix if title and any(term in title for term in appendix_terms) else main
        target.extend(block)
    return "\n".join(main).strip(), "\n".join(appendix).strip()


def _demote_headings(markdown: str, levels: int = 2) -> str:
    """Nest an Observer renderer under the combined report hierarchy."""
    output = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,4})\s+(.*)$", line)
        if match:
            output.append("#" * min(6, len(match.group(1)) + levels) + " " + match.group(2))
        else:
            output.append(line)
    return "\n".join(output)


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
    # A single production run must keep byte-identical output to the previous
    # release so the existing L1 review artifact does not churn.
    if len(packets) == 1 and packets[0].get("claim_id") == PRODUCTION_CLAIM_ID:
        return render_ai_production_markdown(packets[0])
    scope = result.get("workflow_scope", {})
    conclusions = [_observer_conclusion(packet) for packet in packets]
    parts = [
        "# Evidence：L1 AI 应用层——生产化、商业化与原始能力边界", "",
        "## 一、L1 整体结论", "",
        f"L1 本次运行包含 {len(packets)} 个独立 Observer。生产化 Observer 内部使用四个同等重要、但分母不可合并的证据轴；商业化与原始能力分别回答收入兑现和技术边界问题。报告不计算跨 Observer 综合分数。", "",
    ]
    for label, status, conclusion in conclusions:
        parts.append(f"- **{label}**（`{status}`）：{conclusion}")
    parts.extend(["", "## 二、三个 Observer 命题结论", "",
                  "| Observer | 追踪命题 | 状态 | 本期结论 |", "|---|---|---|---|"])
    for packet, (label, status, conclusion) in zip(packets, conclusions):
        parts.append(f"| {label} (`{packet.get('claim_id', 'unknown_claim')}`) | {packet.get('claim_text', '')} | `{status}` | {conclusion} |")
    parts.extend(["", "## 三、各命题的主体、维度与证据", ""])
    appendices: list[tuple[str, str]] = []
    for packet, (label, _, _) in zip(packets, conclusions):
        claim_id = str(packet.get("claim_id") or "")
        main, appendix = _partition_claim_markdown(render_claim_markdown(packet), claim_id)
        parts.extend([f"### {label}", "", _demote_headings(main, 2), "", "---", ""])
        if appendix:
            appendices.append((label, appendix))
    if appendices:
        parts.extend(["## 附录：方法、长表与复现资产", "",
                      "以下内容供审计与复算；不属于报告主干结论。", ""])
        for label, appendix in appendices:
            parts.extend([f"### {label}", "", _demote_headings(appendix, 2), ""])
    return "\n".join(parts)


def _rewrite_report_asset_links(markdown: str, result: dict[str, Any], report_path: Path) -> str:
    """Make all Observer assets portable relative to the report file.

    Individual renderers historically returned absolute, cwd-relative, and
    asset-directory-relative paths.  Normalizing at the final write boundary
    keeps each renderer data-owned while ensuring a report opened directly in
    a Markdown viewer can resolve every image, sidecar, CSV and JSON link.
    """
    candidates: dict[str, set[str]] = {}
    cwd = Path.cwd().resolve()
    for packet in result.get("packets") or []:
        descriptors = list(packet.get("visualization_descriptors") or [])
        descriptors += list(packet.get("table_descriptors") or [])
        for descriptor in descriptors:
            for key in ("png_path", "sidecar_path", "csv_path", "json_path"):
                raw = descriptor.get(key)
                if not raw:
                    continue
                original = str(raw)
                path = Path(original).expanduser()
                if not path.is_absolute():
                    path = cwd / path
                path = path.resolve()
                try:
                    target = os.path.relpath(path, report_path.parent.resolve())
                except ValueError:
                    target = str(path)
                forms = {original, str(path), path.name,
                         f"{path.parent.name}/{path.name}"}
                try:
                    forms.add(str(path.relative_to(cwd)))
                except ValueError:
                    pass
                for form in forms:
                    candidates.setdefault(form, set()).add(target)
    replacements = {
        source: next(iter(targets))
        for source, targets in candidates.items()
        if source and len(targets) == 1
    }
    # Replace complete Markdown link targets only.  A global substring replace
    # would turn ``assets/chart.png`` into ``assets/assets/chart.png`` when a
    # descriptor also contributed the basename ``chart.png`` as a candidate.
    pattern = re.compile(r"(!?\[[^\]]*\]\()([^\s)]+)(\))")
    return pattern.sub(
        lambda match: match.group(1) + replacements.get(match.group(2), match.group(2)) + match.group(3),
        markdown,
    )


def write_layer_evidence_markdown(result: dict[str, Any], output: str | Path) -> Path:
    """Persist the human review artifact outside the DataProducts query boundary."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_layer_evidence_markdown(result)
    path.write_text(_rewrite_report_asset_links(rendered, result, path), encoding="utf-8")
    return path


def write_layer_evidence_outputs(result: dict[str, Any], output: str | Path) -> list[Path]:
    """Write the combined layer review plus one independent report per claim.

    Each Observer keeps its own governed output path so a commercialization
    failure can never leave the production report half-written, and vice versa.
    """
    combined = write_layer_evidence_markdown(result, output)
    # The human Markdown and the Analyst LLM context are a single publication
    # unit.  Keep the historical return value limited to Markdown paths so
    # callers that enumerate independent review reports remain compatible.
    from .analyst_context import write_layer_analyst_context

    context_paths = write_layer_analyst_context(result, combined)
    context_links = [os.path.relpath(path, combined.parent) for path in context_paths]
    with combined.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n\n## 附录：Analyst LLM 结构化 context\n\n"
            "以下 JSON 是 Analyst LLM 的主要输入；YAML 与其语义等价。"
            "图表和本文继续服务人类审阅，不用于替代结构化数值与结论边界。\n\n"
            f"- [Canonical JSON context]({context_links[0]})\n"
            f"- [Equivalent YAML context]({context_links[1]})\n"
        )
    written = [combined]
    packets = result.get("packets") or []
    if len(packets) < 2:
        return written
    base = Path(output)
    for packet in packets:
        claim_id = str(packet.get("claim_id") or "unknown_claim")
        path = base.with_name(f"{base.stem}-{claim_id}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        packet_result = {"packets": [packet]}
        rendered = render_claim_markdown(packet)
        path.write_text(_rewrite_report_asset_links(rendered, packet_result, path), encoding="utf-8")
        written.append(path)
    return written
