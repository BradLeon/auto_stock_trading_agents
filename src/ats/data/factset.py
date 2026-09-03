"""FactSet Earnings Insight — weekly S&P 500 earnings/valuation backdrop.

The stable landing URL (factset.com/earningsinsight) 302-redirects to the current
week's date-coded PDF, so auto-download is a single redirect-following GET. Falls
back to the newest local PDF in the folder if the download fails (user-dropped
copies). Feeds the macro strategist's earnings/valuation regime. Never raises.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from ..schemas.macro_strategy import EarningsBackdrop
from .base import safe_fetch

log = logging.getLogger("ats.data.factset")

name = "factset"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def fetch_earnings_insight(cfg: dict) -> tuple[str, str]:
    """Return (commentary_text, source_label). ('', 'none') if unavailable."""
    if not cfg.get("enabled", True):
        return "", "disabled"
    folder = Path(cfg.get("folder", "") or "")

    path = None
    if cfg.get("download", True) and cfg.get("url"):
        path = safe_fetch(lambda: _download(cfg["url"], folder),
                          source="factset:download", attempts=2)
    if path is None:
        path = _newest_local(folder)
        if path is not None:
            log.info("factset: using local PDF %s (download unavailable)", path.name)
    if path is None or not path.is_file():
        return "", "none"

    text = safe_fetch(lambda: _extract(path, int(cfg.get("max_pages", 16))),
                      source=f"factset:read:{path.name}", attempts=1) or ""
    return text[:int(cfg.get("max_chars", 14000))], f"factset:{path.name}"


def _platform_snapshot(products=None):
    owned = products is None
    if products is None:
        from .products import get_platform_data_products

        products = get_platform_data_products()
    try:
        return products.earnings_insight_snapshot()
    finally:
        if owned:
            for repository in (getattr(products, "_structured_repository", None),
                               getattr(products, "_unstructured_repository", None)):
                close = getattr(repository, "close", None)
                if close:
                    close()


def _platform_analysis_packet(products=None):
    owned = products is None
    if products is None:
        from .products import get_platform_data_products

        products = get_platform_data_products()
    try:
        return products.earnings_insight_analysis_packet()
    finally:
        if owned:
            for repository in (getattr(products, "_structured_repository", None),
                               getattr(products, "_unstructured_repository", None)):
                close = getattr(repository, "close", None)
                if close:
                    close()


def _snapshot_signature(snapshot) -> dict:
    from .products import to_earnings_backdrop

    backdrop = to_earnings_backdrop(snapshot)
    return {
        "report_date": str(snapshot.report.report_date or ""),
        "version_id": snapshot.report.version_id,
        "state": snapshot.status.state,
        "freshness": snapshot.status.freshness,
        "warnings": list(snapshot.status.warnings),
        "quarter": backdrop.quarter,
        "growth_pct": backdrop.growth_pct,
        "growth_basis": backdrop.growth_basis,
        "sectors_higher": backdrop.sectors_higher,
        "fwd_pe": backdrop.fwd_pe,
        "rendered_review_text": backdrop.to_context(),
    }


def _record_factset_shadow(*, legacy: EarningsBackdrop, platform_snapshot) -> None:
    """Persist value/period/state/freshness/render comparisons without source text."""
    try:
        from .cutover import record_consumer_comparison
        from .runtime import platform_data_db_path

        legacy_signature = {
            "report_date": str(legacy.report_date or ""),
            "quarter": legacy.quarter, "growth_pct": legacy.growth_pct,
            "growth_basis": legacy.growth_basis,
            "sectors_higher": legacy.sectors_higher, "fwd_pe": legacy.fwd_pe,
            "rendered_review_text": legacy.to_context(),
        }
        platform_signature = _snapshot_signature(platform_snapshot)
        comparable = {key: platform_signature.get(key) for key in legacy_signature}
        matched = legacy_signature == comparable
        record_consumer_comparison(
            consumer="macro_factset", entity="SP500",
            data_db=platform_data_db_path(),
            status="reconciled" if matched else "mismatch",
            details={"input": "factset_earnings_insight",
                     "reason": "identical_snapshot" if matched else "snapshot_mismatch",
                     "legacy": legacy_signature, "platform": platform_signature})
    except Exception as exc:  # comparison telemetry must not stop weekly review
        log.warning("factset: failed to record macro shadow comparison: %s", exc)


def _platform_macro(snapshot) -> tuple[str, str, EarningsBackdrop]:
    from .products import to_earnings_backdrop

    backdrop = to_earnings_backdrop(snapshot)
    if backdrop.degraded:
        return "", f"factset:{snapshot.status.state}", backdrop
    lines = [backdrop.to_context()]
    if snapshot.report.report_date:
        lines.append(f"报告日期: {snapshot.report.report_date.isoformat()}")
    lines.append(
        f"数据状态: {snapshot.status.state}; freshness={snapshot.status.freshness}; "
        f"estimate_state={backdrop.growth_basis or 'n/a'}")
    if snapshot.status.warnings:
        lines.append("质量警告: " + "; ".join(snapshot.status.warnings))
    return "\n".join(lines), f"factset:{snapshot.report.version_id}", backdrop


_GROUP_LABELS = {
    "reporting_progress": "财报披露进度",
    "earnings_revenue_surprises": "盈利和营收超预期情况",
    "earnings_revenue_growth": "盈利和营收增长",
    "profit_margin": "利润率",
    "company_guidance": "公司指引",
    "estimate_revision_breadth": "盈利预测上调范围",
    "valuation": "市盈率",
    "ratings_and_target_price": "分析师评级和目标价",
    "other": "其他已发布指标",
}


def _display_value(observation) -> str:
    value = observation.value
    if observation.unit == "ratio":
        return f"{value * 100:.1f}%"
    if observation.unit == "count":
        return f"{value:g} 家/个"
    if observation.unit == "multiple":
        return f"{value:g} 倍"
    return f"{value:g} {observation.unit}"


def render_macro_analysis_packet(packet) -> str:
    """Render every released index metric plus bounded page-cited prose."""
    if not packet.report.version_id:
        return "FactSet 分析材料不可用：" + packet.status.state
    lines = [
        f"报告日期: {packet.report.report_date}",
        f"数据状态: {packet.status.state}; freshness={packet.status.freshness}; "
        f"age_days={packet.status.age_days}",
        f"结构化指标: {packet.observation_count} 项（以下全部提供，不得只挑少数复述）",
    ]
    if packet.status.warnings:
        lines.append("质量警告: " + "; ".join(packet.status.warnings))
    for group_name, observations in packet.observation_groups.items():
        lines.append(f"\n### {_GROUP_LABELS.get(group_name, group_name)}")
        if not observations:
            lines.append("- 无已发布数据")
            continue
        for item in observations:
            pages = sorted({anchor.page_number for anchor in item.evidence
                            if anchor.page_number})
            page_ref = f"；原始证据页 {','.join(map(str, pages))}" if pages else ""
            state = (f"；口径 {item.estimate_state}"
                     if item.estimate_state != "not_applicable" else "")
            lines.append(
                f"- `{item.metric_id}` = {_display_value(item)}；期间 {item.period}"
                f"{state}{page_ref}；数据编号 `{item.observation_id}`")
    if packet.diagnostics:
        lines += ["", "### 程序直接计算的关系（非模型推断）"]
        for item in packet.diagnostics:
            unit = {"percentage_point": "个百分点", "percent": "%",
                    "ratio": "倍", "count": "家"}.get(item.unit, item.unit)
            lines.append(
                f"- `{item.diagnostic_id}` {item.label} = {item.value:g}{unit}；"
                f"输入数据编号 {', '.join(f'`{value}`' for value in item.input_observation_ids)}")
    lines += ["", "### 报告正文证据（最多六段；仅作分析素材）"]
    if not packet.narrative_evidence:
        lines.append("- 未找到符合预设分析问题的正文段落；不得自行补充。")
    else:
        for item in packet.narrative_evidence:
            lines.append(
                f"- **{item.topic_label}** [第 {item.page_number} 页，"
                f"{item.section_title or '未标章节'}]：{item.text}")
    lines += ["", "分析纪律：结构化数字是数据事实；对增长质量、集中度、驱动因素、"
              "估值和市场预期的文字属于模型解释。两者必须明确区分。"]
    return "\n".join(lines)


def _platform_macro_material(packet) -> tuple[str, str, EarningsBackdrop, object]:
    from .products import to_earnings_backdrop

    snapshot_like = packet
    backdrop = to_earnings_backdrop(snapshot_like)
    text = render_macro_analysis_packet(packet)
    if backdrop.degraded and not packet.report.version_id:
        text = ""
    return text, f"factset:{packet.report.version_id or packet.status.state}", backdrop, packet


def fetch_macro_material(cfg: dict, *, products=None):
    """Resolve Macro's complete FactSet material without hidden acquisition."""
    from .rollout_modes import read_mode

    mode = read_mode("macro_factset")
    if mode == "off":
        return "", "disabled", None, None
    packet = None
    if mode in {"platform", "fallback", "shadow"}:
        try:
            packet = _platform_analysis_packet(products)
            platform = _platform_macro_material(packet)
        except Exception as exc:
            log.warning("factset: platform Macro analysis material unavailable: %s", exc)
            platform = ("", "factset:unavailable", None, None)
        if mode == "platform":
            return platform
        if mode == "fallback" and platform[0]:
            return platform
    text, source = fetch_earnings_insight(cfg)
    legacy = parse_key_metrics(text, source=source) if text else None
    if mode == "shadow" and packet is not None and legacy is not None:
        _record_factset_shadow(legacy=legacy, platform_snapshot=packet)
    return text, source, legacy, None


def fetch_macro_context(cfg: dict, *, products=None) -> tuple[str, str, EarningsBackdrop | None]:
    """Resolve the independently controlled Macro consumer without hidden refresh."""
    text, source, backdrop, _packet = fetch_macro_material(cfg, products=products)
    return text, source, backdrop


def _render_sector_snapshot(snapshot) -> str:
    if not snapshot.sectors:
        return ""
    lines = [
        "## FactSet GICS 行业矩阵（top-down 市场背景）",
        "> 仅用于行业盈利/估值背景；不是 AI Hardware L1-L8、个股基本面或 Chain 独立证据。",
        f"> 报告日期 {snapshot.report.report_date}; 状态 {snapshot.status.state}; "
        f"freshness={snapshot.status.freshness}",
    ]
    if snapshot.status.warnings:
        lines.append("> 质量警告: " + "; ".join(snapshot.status.warnings))
    for entity, periods in sorted(snapshot.sectors.items()):
        readings = []
        for period, metrics in sorted(periods.items()):
            for metric, observation in sorted(metrics.items()):
                readings.append(
                    f"{period}/{metric}={observation.value:g} {observation.unit}"
                    f"[{observation.estimate_state}]")
        lines.append(f"- {entity}: " + "; ".join(readings))
    return "\n".join(lines)


def fetch_sector_context(*, products=None) -> str:
    """Return a released GICS overlay only; never acquire or parse a PDF."""
    return fetch_sector_material(products=products)["text"]


def fetch_sector_material(*, products=None) -> dict:
    """Return final-synthesis context plus an explicit availability reason."""
    from .rollout_modes import read_mode

    mode = read_mode("sector_factset")
    if mode == "off":
        return {"text": "", "mode": mode, "state": "unavailable",
                "reason": "FactSet 行业数据消费者已关闭。", "report_date": "",
                "version_id": "", "freshness": "unavailable"}
    if mode == "legacy":
        return {"text": "", "mode": mode, "state": "unavailable",
                "reason": "旧读取路径没有可供最终对照使用的正式十一行业数据。",
                "report_date": "", "version_id": "", "freshness": "unavailable"}
    try:
        snapshot = _platform_snapshot(products)
        rendered = _render_sector_snapshot(snapshot)
    except Exception as exc:
        log.warning("factset: platform Sector product unavailable: %s", exc)
        return {"text": "", "mode": mode, "state": "unavailable",
                "reason": f"读取本地 FactSet 行业数据失败：{exc}",
                "report_date": "", "version_id": "", "freshness": "unavailable"}
    base = {
        "mode": mode, "report_date": str(snapshot.report.report_date or ""),
        "version_id": snapshot.report.version_id,
        "freshness": snapshot.status.freshness,
    }
    if mode == "shadow":
        log.info("factset Sector shadow: version=%s sectors=%d status=%s",
                 snapshot.report.version_id, len(snapshot.sectors), snapshot.status.state)
        return {**base, "text": "", "state": "shadow",
                "reason": "FactSet 十一行业数据仍处于影子验证阶段，未作为正式分析输入。"}
    if not rendered:
        reason = ("所选报告尚未发布可用的十一行业分区。"
                  if not snapshot.sectors else "FactSet 十一行业材料为空。")
        return {**base, "text": "", "state": snapshot.status.sector_release.state,
                "reason": reason}
    reason = ""
    if snapshot.status.freshness == "stale":
        reason = f"FactSet 行业报告已过期，原报告日期为 {snapshot.report.report_date}。"
    return {**base, "text": rendered, "state": snapshot.status.sector_release.state,
            "reason": reason}


def _download(url: str, folder: Path) -> Path:
    import httpx

    r = httpx.get(url, headers={"User-Agent": _UA}, timeout=45, follow_redirects=True)
    r.raise_for_status()
    if "application/pdf" not in r.headers.get("content-type", "").lower():
        raise ValueError(f"not a pdf: {r.headers.get('content-type')}")
    fname = Path(str(r.url).split("?")[0]).name or "EarningsInsight_latest.pdf"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / fname
    p.write_bytes(r.content)
    log.info("factset: downloaded %s (%d KB)", fname, len(r.content) // 1024)
    return p


def _newest_local(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    pdfs = sorted(folder.glob("EarningsInsight_*.pdf"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return pdfs[0] if pdfs else None


def _extract(path: Path, max_pages: int) -> str:
    """Extract the commentary pages (clean prose; chart/table pages garble in pypdf)."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = reader.pages[:max_pages]
    return "\n".join((pg.extract_text() or "") for pg in pages).strip()


# --------------------------------------------------------------------------- #
# Key Metrics parsing
# --------------------------------------------------------------------------- #
# The aggregate numbers the macro framework needs live in the page-1 "Key Metrics"
# bullets as clean prose — NOT in the chart/table pages that pypdf garbles. So no
# table-extraction or vision dependency is needed; a validated regex pass is enough.
#
# Two things make this less trivial than it looks, both observed in real files:
#   · wording shifts with the earnings season — "estimated" before the quarter
#     reports vs "blended" after; the Scorecard bullet is absent until companies
#     start reporting; guidance counts reset at the quarter roll.
#   · pypdf sometimes injects a space INSIDE a number ("was 1 8.8%"). A naive
#     `[\d.]+` then silently captures "8.8" — a wrong number, which is worse than
#     a missing one. `_num` strips internal spaces and every field is range-checked.

_PLAUSIBLE = {                       # field -> (low, high), inclusive
    "growth_pct": (-100.0, 300.0),
    "prior_growth_pct": (-100.0, 300.0),
    "pct_reported": (0.0, 100.0),
    "pct_eps_beat": (0.0, 100.0),
    "pct_revenue_beat": (0.0, 100.0),
    "fwd_pe": (5.0, 60.0),
    "fwd_pe_5y_avg": (5.0, 60.0),
    "fwd_pe_10y_avg": (5.0, 60.0),
}


def _num(raw: str | None) -> float | None:
    """Parse a number that may contain pypdf's stray internal spaces."""
    if raw is None:
        return None
    try:
        return float(re.sub(r"\s+", "", raw))
    except ValueError:
        return None


def _quarter(raw: str | None) -> str:
    """"Q 2 2026" (pypdf splits it) -> "Q2 2026"."""
    if not raw:
        return ""
    m = re.match(r"Q\s*(\d)\s*(\d{4})", raw)
    return f"Q{m.group(1)} {m.group(2)}" if m else re.sub(r"\s+", " ", raw).strip()


def _find(flat: str, pattern: str) -> str | None:
    m = re.search(pattern, flat, re.I)
    return m.group(1) if m else None


# Digits possibly broken up by pypdf, e.g. "1 8.8" or "20.1".
_D = r"(-?[\d\s]*\d(?:\s*\.\s*\d+)?)"


def parse_key_metrics(text: str, *, source: str = "") -> EarningsBackdrop:
    """Extract the page-1 Key Metrics into a validated EarningsBackdrop.

    Per-field degradation: a field that is absent or implausible is dropped with a
    note, and the fields that did parse are kept. Only a total failure to find the
    anchor valuation figure marks the whole thing `degraded`.
    """
    out = EarningsBackdrop(source=source)
    if not text:
        out.degraded = True
        out.notes.append("no text")
        return out
    flat = re.sub(r"\s+", " ", text)

    raw_date = _find(flat, r"([A-Z][a-z]+ \d{1,2}, \d{4})")
    if raw_date:
        try:
            out.report_date = datetime.strptime(raw_date, "%B %d, %Y").date()
        except ValueError:
            pass

    out.quarter = _quarter(_find(flat, r"Earnings Growth: For (Q\s*\d\s*\d{4})"))

    basis = _find(flat, r"the (estimated|blended) \(year-over-year\) earnings growth rate")
    out.growth_basis = (basis or "").lower()
    out.growth_pct = _num(_find(
        flat, r"(?:estimated|blended) \(year-over-year\) earnings growth rate "
              rf"for the S&P 500 is {_D}\s*%"))

    out.prior_growth_pct = _num(_find(
        flat, r"estimated \(year-over-year\) earnings growth rate for the S&P 500 "
              rf"for Q\s*\d\s*\d{{4}} was {_D}\s*%"))
    out.prior_as_of = _find(flat, r"Earnings Revisions: On ([A-Z][a-z]+ \d{1,2})") or ""

    sectors = _find(flat, r"(\w+) sectors are (?:expected to report|reporting) higher earnings")
    out.sectors_higher = _WORD_NUM.get((sectors or "").lower())
    if out.sectors_higher is None and (sectors or "").isdigit():
        out.sectors_higher = int(sectors)
    out.revision_direction = (_find(flat, r"due to (upward|downward) revisions") or "").lower()

    out.guidance_quarter = _quarter(_find(flat, r"Earnings Guidance: For (Q\s*\d\s*\d{4})"))
    neg = _find(flat, r"(\d+) S&P 500 companies have issued negative EPS guidance")
    pos = _find(flat, r"(\d+) S&P 500 companies have issued positive EPS guidance")
    out.guidance_negative = int(neg) if neg else None
    out.guidance_positive = int(pos) if pos else None

    out.pct_reported = _num(_find(flat, rf"with {_D}\s*% of S&P 500 companies reporting"))
    out.pct_eps_beat = _num(_find(
        flat, rf"{_D}\s*% of S&P 500 companies have reported a positive EPS surprise"))
    out.pct_revenue_beat = _num(_find(
        flat, rf"{_D}\s*% of S&P 500 companies has? reported a positive revenue surprise"))

    out.fwd_pe = _num(_find(
        flat, rf"forward 12\s*-?\s*month P/E ratio for the S&P 500 is {_D}"))
    out.fwd_pe_5y_avg = _num(_find(flat, rf"the 5-year average \({_D}\)"))
    out.fwd_pe_10y_avg = _num(_find(flat, rf"the 10-year average \({_D}\)"))

    _validate(out)
    if out.fwd_pe is None:
        out.degraded = True
        out.notes.append("forward P/E not found — 版式可能已变，退回散文模式")
    return out


_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11}


def _validate(bd: EarningsBackdrop) -> None:
    """Drop implausible values. A wrong number is worse than a missing one — a
    garbled parse must never reach the model looking like a fact."""
    for field, (lo, hi) in _PLAUSIBLE.items():
        val = getattr(bd, field)
        if val is not None and not (lo <= val <= hi):
            bd.notes.append(f"{field}={val} 超出合理区间 [{lo}, {hi}]，已丢弃")
            setattr(bd, field, None)
