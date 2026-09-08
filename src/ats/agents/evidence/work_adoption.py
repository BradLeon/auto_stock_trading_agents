"""Governed L1 Evidence Observer consumer for Claude work-adoption data.

This module deliberately consumes only DataProducts. It does not expose repository
handles, provider URLs, or physical-table queries to the Observer.
"""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from typing import Any

CONSUMER = "evidence_observer"
PRODUCTION_CLAIM_ID = "ai_core_production_workflow_penetration"
PRODUCTION_CLAIM_TEXT = "以 Anthropic 1P API 作为前沿 AI 生产部署的代理，满足核心生产流程标准的职业和任务是否持续扩大，且这些单元所承载的 API 使用量是否持续提高？"
SEMANTIC_GUARDRAILS = {
    "usage_share": "职业或任务占对应 Claude 产品总使用量的份额，不是从业者采用率。",
    "industry": "官网 Industry 是 SOC occupational major group，不是企业所属行业。",
    "observed_exposure": "Observed Exposure 是研究快照，不是月度使用序列。",
    "allowed_claims": (
        "Claude 使用量在职业间的分布",
        "同口径月份间自动化占比的变化",
        "公开 task-cell 的可见覆盖",
    ),
    "prohibited_claims": (
        "员工采用率",
        "企业席位渗透率",
        "岗位替代数量",
        "交易信号或证据权重",
    ),
}


def _usage_fact(job: dict[str, Any], *, source_product: str, period: str) -> dict[str, Any] | None:
    value = (job.get("metrics") or {}).get("ai.work_adoption.usage_share")
    if value is None:
        return None
    name = job.get("name") or job.get("entity_id", "")
    return {
        "kind": "claude_usage_share",
        "entity_id": job.get("entity_id", ""),
        "period": period,
        "source_product": source_product,
        "value": value,
        "unit": "percent",
        "statement": f"{period}，{name} 占 {source_product} 总使用量的 {value:g}%（不是该职业从业者采用率）。",
        "lineage": job.get("lineage", {}),
    }


def observe_work_adoption(
    *,
    source_product: str,
    period: str = "",
    occupation: str = "",
    as_of: datetime | None = None,
    top_n: int = 10,
    products=None,
) -> dict[str, Any]:
    """Return a bounded, replayable L1 observation packet.

    The generated statements use closed deterministic templates so provider metrics
    cannot be relabelled as worker adoption or employer-industry statistics.
    """
    if top_n < 0:
        raise ValueError("top_n must be non-negative")
    if products is None:
        from ...data.products import get_platform_data_products

        products = get_platform_data_products()

    snapshot = products.ai_work_adoption_snapshot(
        source_product=source_product,
        period=period,
        as_of=as_of,
        snapshot_consumer=CONSUMER,
        snapshot_purpose=f"l1_work_adoption:{source_product}:{period or 'latest'}",
    )
    selected_period = snapshot.get("period", period)
    facts = [
        fact
        for job in snapshot.get("jobs", [])[:top_n]
        if (fact := _usage_fact(job, source_product=source_product, period=selected_period))
        is not None
    ]
    profile = None
    if occupation and snapshot.get("status") == "ok":
        profile = products.ai_job_profile(
            occupation, source_product=source_product, period=selected_period, as_of=as_of
        )
    return {
        "status": snapshot.get("status", "no_coverage"),
        "consumer": CONSUMER,
        "source_access": "data_products_only",
        "source_product": source_product,
        "period": selected_period,
        "as_of": as_of.isoformat() if as_of else None,
        "facts": facts,
        "snapshot": snapshot,
        "job_profile": profile,
        "manifest": snapshot.get("manifest"),
        "semantic_guardrails": SEMANTIC_GUARDRAILS,
    }


def _is_adjacent(previous: str, current: str) -> bool:
    try:
        year, month = map(int, current.split("-"))
        expected = f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"
        return previous == expected
    except ValueError:
        return False


def _trend_status(rows: list[dict[str, Any]], field: str) -> str:
    ordered = sorted(rows, key=lambda row: row["period"])
    if len(ordered) < 3 or any(
        not _is_adjacent(left["period"], right["period"]) for left, right in pairwise(ordered)
    ):
        return "insufficient_history"
    values = [row.get(field) for row in ordered]
    if any(value is None for value in values):
        return "insufficient_history"
    changes = [right - left for left, right in pairwise(values)]
    if all(change == 0 for change in changes):
        return "flat"
    if all(change >= 0 for change in changes):
        return "directional_up"
    if all(change <= 0 for change in changes):
        return "directional_down"
    return "mixed"


def _combined_status(statuses: list[str], *, positive: str, mixed: str) -> str:
    if any(status == "insufficient_history" for status in statuses):
        return "insufficient_history"
    return positive if all(status == "directional_up" for status in statuses) else mixed


def _production_methodology_card(
    result: dict[str, Any], *, history_status: str, quality_status: str
) -> dict[str, Any]:
    coverage = next(
        (
            row
            for row in result.get("coverage_diagnostics", [])
            if row.get("period") == result.get("latest_period")
        ),
        {},
    )
    summary = {
        row["grain"]: row
        for row in result.get("period_rows", [])
        if row.get("period") == result.get("latest_period")
    }
    releases = sorted(
        {row.get("release_date") for row in summary.values() if row.get("release_date")}
    )
    published = sorted(
        {row.get("published_at") for row in summary.values() if row.get("published_at")}
    )
    return {
        "title": "AI 应用层生产化渗透：方法卡",
        "provider": "Anthropic Economic Index",
        "source_product": "1p_api",
        "geography": "GLOBAL",
        "periods": result.get("periods", []),
        "latest_period": result.get("latest_period", ""),
        "release_and_published_at": {
            "release_date": releases[-1] if releases else None,
            "published_at": published[-1] if published else None,
        },
        "visible_samples": {
            grain: summary.get(grain, {}).get("visible_count") for grain in ("occupation", "task")
        },
        "qualified_samples": {
            grain: summary.get(grain, {}).get("qualified_count") for grain in ("occupation", "task")
        },
        "taxonomy": {
            "version": coverage.get("taxonomy_version"),
            "mapped_tasks": coverage.get("mapped_task_count"),
            "unmapped_tasks": coverage.get("unmapped_task_count"),
            "mapping_coverage_pct": coverage.get("mapping_coverage_pct"),
        },
        "threshold": result.get("proxy", {}),
        "threshold_version": result.get("threshold_version"),
        "methodology_versions": sorted(
            {row.get("methodology_version", "") for row in result.get("period_rows", [])}
        ),
        "derivation_version": result.get("derivation_version"),
        "history_status": history_status,
        "quality_status": quality_status,
        "manifest_id": (result.get("manifest") or {}).get("snapshot_id"),
        "limitations": [
            "Usage Share 是 Anthropic 1P API 产品流量份额，不是员工或企业采用率。",
            "qualified 是用户定义的生产化代理，不能确认持续运行的工作流。",
            "未发布或隐私过滤的 cell 不等于零；职业内任务覆盖是 taxonomy-dependent 下限。",
            "不能由此推断岗位替代、生产率、ROI 或交易结论。",
        ],
    }


def observe_ai_production_penetration(
    *,
    periods: list[str] | None = None,
    as_of: datetime | None = None,
    top_n: int = 10,
    chart_dir: str = "",
    workflow_scope: dict[str, str] | None = None,
    products=None,
) -> dict[str, Any]:
    """Dedicated, read-only L1 packet for the production-workflow proxy.

    This is intentionally separate from the historical Usage observer and from every
    other Observer contract; only this packet contains a methodology card.
    """
    if products is None:
        from ...data.products import get_platform_data_products

        products = get_platform_data_products()
    scope_suffix = ""
    if workflow_scope:
        scope_suffix = ":" + ":".join(
            str(workflow_scope.get(key, "")) for key in ("sector", "layer")
        )
    result = products.ai_production_penetration(
        periods=periods,
        as_of=as_of,
        top_n=top_n,
        snapshot_consumer=CONSUMER,
        snapshot_purpose=(f"{PRODUCTION_CLAIM_ID}{scope_suffix}:{','.join(periods or ['latest'])}"),
        as_frame=bool(chart_dir),
    )
    if result.get("status") != "ok":
        return {
            "status": result.get("status", "no_coverage"),
            "claim_id": PRODUCTION_CLAIM_ID,
            "claim_text": PRODUCTION_CLAIM_TEXT,
            "source_access": "data_products_only",
            "warnings": [result.get("reason", "受治理生产化数据不可用。")],
        }
    summary = result.get("period_rows", [])
    by_grain = {
        grain: [row for row in summary if row["grain"] == grain] for grain in ("occupation", "task")
    }
    series_statuses = {
        "occupation_visible_production_rate": _trend_status(
            by_grain["occupation"], "visible_production_rate_pct"
        ),
        "task_visible_production_rate": _trend_status(
            by_grain["task"], "visible_production_rate_pct"
        ),
        "occupation_production_traffic_share": _trend_status(
            by_grain["occupation"], "production_traffic_share_pct"
        ),
        "task_production_traffic_share": _trend_status(
            by_grain["task"], "production_traffic_share_pct"
        ),
    }
    breadth = _combined_status(
        [
            series_statuses["occupation_visible_production_rate"],
            series_statuses["task_visible_production_rate"],
        ],
        positive="breadth_expanding",
        mixed="mixed",
    )
    depth = _combined_status(
        [
            series_statuses["occupation_production_traffic_share"],
            series_statuses["task_production_traffic_share"],
        ],
        positive="depth_deepening",
        mixed="mixed",
    )
    overall = (
        "penetration_expanding"
        if breadth == "breadth_expanding" and depth == "depth_deepening"
        else ("insufficient_history" if "insufficient_history" in {breadth, depth} else "mixed")
    )
    latest = result.get("latest_period", "")
    coverage_rows = [
        row for row in result.get("occupation_task_coverage", []) if row.get("period") == latest
    ]
    high_coverage_occupations = sorted(
        (
            row
            for row in coverage_rows
            if (
                row.get("confirmed_production_task_coverage_pct")
                if row.get("confirmed_production_task_coverage_pct") is not None
                else row.get("coverage_lower_bound_pct", 0)
            )
            >= 50
        ),
        key=lambda row: (
            -(
                row.get("confirmed_production_task_coverage_pct")
                if row.get("confirmed_production_task_coverage_pct") is not None
                else row.get("coverage_lower_bound_pct", 0)
            ),
            row.get("occupation_id", ""),
        ),
    )
    comparison = {}
    latest_rows = {row["grain"]: row for row in summary if row["period"] == latest}
    prior = sorted({row["period"] for row in summary if row["period"] < latest})
    facts, warnings = [], list(result.get("cell_diagnostics", []))
    if prior and all(grain in latest_rows for grain in ("occupation", "task")):
        prior_rows = {row["grain"]: row for row in summary if row["period"] == prior[-1]}
        occupation_breadth = (
            latest_rows["occupation"]["visible_production_rate_pct"]
            - prior_rows["occupation"]["visible_production_rate_pct"]
        )
        task_breadth = (
            latest_rows["task"]["visible_production_rate_pct"]
            - prior_rows["task"]["visible_production_rate_pct"]
        )
        occupation_depth = (
            latest_rows["occupation"]["production_traffic_share_pct"]
            - prior_rows["occupation"]["production_traffic_share_pct"]
        )
        task_depth = (
            latest_rows["task"]["production_traffic_share_pct"]
            - prior_rows["task"]["production_traffic_share_pct"]
        )
        comparison = {
            "from_period": prior[-1],
            "to_period": latest,
            "occupation_breadth_change_pp": occupation_breadth,
            "task_breadth_change_pp": task_breadth,
            "occupation_depth_change_pp": occupation_depth,
            "task_depth_change_pp": task_depth,
            "breadth": "mixed" if occupation_breadth * task_breadth < 0 else "aligned",
            "depth": "increased" if occupation_depth > 0 and task_depth > 0 else "mixed",
        }
        facts.append(
            {
                "kind": "monthly_comparison",
                "period": latest,
                "statement": (
                    f"{prior[-1]} 至 {latest} 的月度比较中，生产化广度表现混合；"
                    f"职业和任务的生产化流量份额均提高。"
                ),
            }
        )
    history_status = (
        "sufficient"
        if all(status != "insufficient_history" for status in series_statuses.values())
        else "insufficient_history"
    )
    if history_status == "insufficient_history":
        warnings.append("只有不足三个连续可比月：可展示月度比较，但不能判断持续趋势。")
    unnamed_high_coverage = [
        row
        for row in high_coverage_occupations
        if row.get("occupation_name_status") == "stable_id_only"
    ]
    if unnamed_high_coverage:
        warnings.append(
            "当前 Anthropic taxonomy artifact 未为 "
            f"{len(unnamed_high_coverage)} 个高覆盖旧 O*NET-SOC 代码提供职业标题；报告保留稳定代码，"
            "不擅自补写外部名称。"
        )
    warnings.extend(
        [
            "Usage Share 是产品流量份额，不是员工或企业采用率。",
            "生产化资格是代理，不证明持续工作流、生产率、ROI 或岗位替代。",
        ]
    )
    quality_status = (
        "warning"
        if any(
            row.get("quality_status") == "warning" for row in result.get("coverage_diagnostics", [])
        )
        else "accepted"
    )
    visualizations = {"descriptors": []}
    if chart_dir:
        from .production_visualization import render_production_charts

        visualizations = render_production_charts(
            frames=result["frames"],
            output_dir=chart_dir,
            manifest=result.get("manifest"),
            metadata={
                "source_product": "1p_api",
                "threshold_version": result.get("threshold_version"),
                "derivation_version": result.get("derivation_version"),
            },
        )
        if visualizations.get("visualization_warning"):
            warnings.append(visualizations["visualization_warning"])
    packet = {
        "status": "ok",
        "consumer": CONSUMER,
        "source_access": "data_products_only",
        "claim_id": PRODUCTION_CLAIM_ID,
        "claim_text": PRODUCTION_CLAIM_TEXT,
        "source_scope": {
            "provider": "Anthropic Economic Index",
            "source_product": "1p_api",
            "geography": "GLOBAL",
        },
        "threshold_definition": result.get("proxy"),
        "period_range": result.get("periods", []),
        "latest_period": latest,
        "history_status": history_status,
        "four_series_statuses": series_statuses,
        "breadth_status": breadth,
        "depth_status": depth,
        "overall_status": overall,
        "monthly_comparison": comparison,
        "summary_table": summary,
        "top_occupations": result.get("top_occupations", []),
        "top_tasks": result.get("top_tasks", []),
        "coverage_diagnostics": result.get("coverage_diagnostics", []),
        "occupation_task_coverage": result.get("occupation_task_coverage", []),
        "high_coverage_occupations": high_coverage_occupations,
        "occupation_coverage_distribution": result.get("occupation_coverage_distribution", {}),
        "methodology_card": _production_methodology_card(
            result, history_status=history_status, quality_status=quality_status
        ),
        "facts": facts,
        "warnings": warnings,
        "snapshot_manifest": result.get("manifest"),
        "visualization_descriptors": visualizations.get("descriptors", []),
        "semantic_guardrails": {
            **SEMANTIC_GUARDRAILS,
            "production_proxy": result.get("semantic_boundary"),
        },
    }
    if workflow_scope:
        packet["workflow_scope"] = dict(workflow_scope)
        packet["methodology_card"]["workflow_scope"] = dict(workflow_scope)
    return packet


def _pct(value: Any, digits: int = 2) -> str:
    """Format nullable percentage values without changing the governed value."""
    return "—" if value is None else f"{float(value):.{digits}f}%"


def _pp(value: Any, digits: int = 2) -> str:
    """Format a percentage-point change; this is not itself a percent sign."""
    return "—" if value is None else f"{float(value):.{digits}f}"


def _occupation_label(row: dict[str, Any]) -> str:
    name = str(row.get("occupation_name") or row.get("occupation_id", ""))
    if row.get("occupation_name_status") == "stable_id_only":
        return f"{name}（当前 taxonomy 未提供名称）"
    return name


def render_ai_production_markdown(packet: dict[str, Any]) -> str:
    """Render the dedicated L1 review packet without querying or deriving data.

    The reader-facing order deliberately separates the four core series from the
    taxonomy-dependent occupational task-combination evidence.
    """
    if packet.get("status") != "ok":
        reason = "; ".join(str(item) for item in packet.get("warnings", []))
        return f"# AI 应用层：生产化与应用扩散\n\n状态：{packet.get('status', 'unavailable')}\n\n{reason}\n"
    card = packet.get("methodology_card", {})
    scope = packet.get("workflow_scope", {})
    grain_name = {"occupation": "职业", "task": "任务"}
    lines = ["# AI 应用层：生产化与应用扩散", ""]
    if scope:
        lines.extend(
            [
                (
                    f"- 运行范围：{scope.get('sector_label', scope.get('sector', ''))} / "
                    f"{scope.get('layer_label', scope.get('layer', ''))}"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## 方法卡",
            "",
            f"- 命题：{packet.get('claim_text', '')}",
            f"- 数据范围：{card.get('provider', '')} / {card.get('source_product', '')} / GLOBAL",
            f"- 数据期间：{'、'.join(card.get('periods', []))}；最新月：{card.get('latest_period', '')}",
            f"- 历史状态：{packet.get('history_status', '')}（不足三个连续可比月时，只报告月度比较）",
            "- 生产化代理：Usage Share > 0、Work Use Share ≥ 80%、Automation Share ≥ 80%、Directive Share ≥ 50%。",
            f"- 可见/达标单元（最新月）：职业 {card.get('visible_samples', {}).get('occupation', '—')} / {card.get('qualified_samples', {}).get('occupation', '—')}；任务 {card.get('visible_samples', {}).get('task', '—')} / {card.get('qualified_samples', {}).get('task', '—')}。",
            "",
            "## 命题状态与四项核心指标",
            "",
            f"- 总体状态：`{packet.get('overall_status', '')}`；广度：`{packet.get('breadth_status', '')}`；深度：`{packet.get('depth_status', '')}`。",
            "- 当前仅有两个连续月，不能据此判断持续趋势。",
            "",
            "|期间|统计维度|可见单元生产化率|生产化流量份额|",
            "|---|---|---:|---:|",
        ]
    )
    for row in packet.get("summary_table", []):
        lines.append(
            f"|{row.get('period', '')}|{grain_name.get(row.get('grain'), row.get('grain', ''))}|"
            f"{_pct(row.get('visible_production_rate_pct'))}|"
            f"{_pct(row.get('production_traffic_share_pct'))}|"
        )
    comparison = packet.get("monthly_comparison", {})
    if comparison:
        lines.extend(
            [
                "",
                (
                    f"月度比较（{comparison.get('from_period')} → {comparison.get('to_period')}）："
                    f"职业可见单元生产化率 {_pp(comparison.get('occupation_breadth_change_pp'))} 个百分点，"
                    f"任务可见单元生产化率 {_pp(comparison.get('task_breadth_change_pp'))} 个百分点；"
                    f"职业生产化流量份额 {_pp(comparison.get('occupation_depth_change_pp'))} 个百分点，"
                    f"任务生产化流量份额 {_pp(comparison.get('task_depth_change_pp'))} 个百分点。"
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## 职业任务组合的已确认生产化覆盖分布",
            "",
            "在一个职业的 O*NET 任务组合中，分子是当月公开 1P API cell 已满足生产化代理的任务数；分母是该职业全部去重 O*NET 任务数。未公开、隐私过滤或不能映射的任务仍留在分母，因此这里只陈述公开数据能够确认的覆盖，不声称存在可观测的“上限”。这属于 taxonomy-dependent 补充证据，不改变上面的四项核心指标或命题状态。",
            "",
            "|至少达到的已确认覆盖|职业数|有任务组合职业中的占比|",
            "|---:|---:|---:|",
        ]
    )
    for row in packet.get("occupation_coverage_distribution", {}).get("landmarks", []):
        lines.append(
            f"|{_pct(row.get('minimum_coverage_pct'), 0)}|{row.get('occupation_count', '—')}|"
            f"{_pct(row.get('occupation_share_pct'))}|"
        )
    lines.extend(
        [
            "",
            "### 分布尾部的完整披露：已确认覆盖至少 50% 的职业（最新月）",
            "",
            "这不是典型职业样本，也不表示该职业有一半员工、工作时间或完整工作流已经 AI 生产化。它只是上表分布右尾的完整列示：在该职业的 O*NET 任务清单中，公开 1P API 数据能够同时确认满足本代理的任务达到一半以上。典型使用单位请看随后按 Usage Share 排列的高流量 TOP10。",
            "",
            "|职业|已确认生产化任务|O*NET 任务总数|已确认生产化覆盖|",
            "|---|---:|---:|---:|",
        ]
    )
    high_coverage = packet.get("high_coverage_occupations", [])
    if high_coverage:
        for row in high_coverage:
            coverage = row.get(
                "confirmed_production_task_coverage_pct", row.get("coverage_lower_bound_pct")
            )
            lines.append(
                f"|{_occupation_label(row)}|"
                f"{row.get('qualified_mapped_task_count', '—')}|{row.get('taxonomy_task_count', '—')}|{_pct(coverage)}|"
            )
    else:
        lines.append("|无|—|—|—|")
    for title, rows in (
        ("高流量生产化职业 TOP10（典型使用单元）", packet.get("top_occupations", [])),
        ("高流量生产化任务 TOP10（典型使用单元）", packet.get("top_tasks", [])),
    ):
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "以下仅在满足生产化代理的公开 cell 中，按最新月 1P API Usage Share 降序排列；它们是高流量使用的例子，并不是职业任务覆盖分布的代表性抽样。",
                "",
                "|排名|对象|Usage Share|Work Use Share|Automation Share|Directive Share|",
                "|---:|---|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                f"|{row.get('rank', '')}|{row.get('entity_name', row.get('entity_id', ''))}|"
                f"{_pct(row.get('usage_share_pct'))}|{_pct(row.get('work_use_share_pct'), 1)}|"
                f"{_pct(row.get('automation_share_pct'), 1)}|{_pct(row.get('directive_share_pct'), 1)}|"
            )
    descriptors = packet.get("visualization_descriptors", [])
    if descriptors:
        lines.extend(["", "## 图表与可复算 sidecar", ""])
        lines.extend(
            f"- {item.get('chart_type')}: {item.get('png_path')}（sidecar: {item.get('sidecar_path')}）"
            for item in descriptors
        )
    lines.extend(
        [
            "",
            "## 指标注释与公式",
            "",
            "1. **职业可见单元生产化率** = `100 × 当月达到生产化代理的公开详细职业 cell 数 / 当月具有公开 Usage Share 的详细职业 cell 数`。它测量公开职业单元中符合代理标准的比例，不是职业从业者采用率。",
            "2. **任务可见单元生产化率** = `100 × 当月达到生产化代理的公开 O*NET task cell 数 / 当月具有公开 Usage Share 的 O*NET task cell 数`。它测量公开任务单元中符合代理标准的比例，不是所有经济任务的自动化率。",
            "3. **职业生产化流量份额** = `Σ(达到生产化代理的详细职业 cell 的 Provider Usage Share)`。它表示这些职业分类下的合格公开 cell 承载了 1P API 总产品流量的多少份额。",
            "4. **任务生产化流量份额** = `Σ(达到生产化代理的 O*NET task cell 的 Provider Usage Share)`。它表示这些任务分类下的合格公开 cell 承载了 1P API 总产品流量的多少份额。",
            "5. 职业与任务是同一 1P API 产品流量的两种分类视角；四个值均不得跨粒度相加、平均或解释成员工/企业采用率。",
            "",
            "## 语义限制",
            "",
        ]
    )
    lines.extend(
        f"- {warning}" for warning in packet.get("warnings", []) if isinstance(warning, str)
    )
    return "\n".join(lines) + "\n"
