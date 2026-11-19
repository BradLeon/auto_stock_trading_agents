"""Source and time stamps for what reports cite (openspec: report-source-timestamps).

Every time a report shows next to a citation comes from here, resolved from the
stores — never from model-written text — so the Chief report, the Layer report and
the Fundamental report speak one convention:

- An observation's *information time* is when the information became public:
  the document's publish time, else its fetch time (it was public no later than
  that), else a date carried in the document id. The data period it describes is
  shown beside it, never instead of it — an article's "period" is often a forecast
  horizon such as 2022-2028.
- `observed_at` is the extraction time and is never shown as information time.
- A projection is cited with its `as_of` and its age at decision time, flagged when
  older than its task's freshness window (the window the snapshot gate uses).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

ROLE_CN = {
    "layer_analysis": "层分析",
    "sector_allocation": "行业配置",
    "fundamental_expectation_update": "基本面例行",
    "fundamental_event_review": "基本面事件",
    "fundamental_material_review": "基本面重大事项",
    "macro_review": "宏观",
    "technical_review": "技术面",
    "information_brief": "信息简报",
}

_DATE_IN_ID = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
_DATE_PERIOD = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


@dataclass(frozen=True)
class InfoTime:
    """When a piece of information became public, and how we know."""

    kind: str                    # 发布 / 采集 / 文档日期 / 数据期 / ""
    at: datetime | None = None
    period: str = ""             # the data period it describes, when date-like

    def text(self, ref: datetime | None = None) -> str:
        if self.at is None:
            return f"数据期 {self.period}" if self.period else "信息时间未知"
        out = f"{self.kind} {_short(self.at)}"
        if self.kind == "采集":
            out += "（发布时间未登记）"
        if ref is not None:
            out += f" · {age_text(self.at, ref)}"
        if self.period:
            out += f" · 数据期 {self.period}"
        return out


@dataclass(frozen=True)
class SourceStamp:
    """One observation's source document label and information time."""

    label: str
    time: InfoTime


def parse_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _short(at: datetime) -> str:
    at = at.astimezone(timezone.utc)
    if (at.hour, at.minute, at.second) == (0, 0, 0):
        return f"{at:%Y-%m-%d}"
    return f"{at:%m-%d %H:%M}"


def age_text(at: datetime, ref: datetime) -> str:
    seconds = (ref - at).total_seconds()
    if seconds < 0:
        return "晚于本轮"
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86_400:
        return f"{seconds / 3600:.1f} 小时前"
    return f"{seconds / 86_400:.1f} 天前"


def _date_period(*candidates) -> str:
    for c in candidates:
        c = str(c or "").strip()
        if _DATE_PERIOD.match(c):
            return c
    return ""


def info_time(observation: dict | None, document: dict | None) -> InfoTime:
    """The information time of one observation (see the module docstring for the order)."""
    observation = observation or {}
    period = _date_period((document or {}).get("period"), observation.get("period"))
    if document:
        published = parse_time(document.get("published_at"))
        if published is not None:
            return InfoTime("发布", published, period)
        fetched = parse_time(document.get("fetched_at"))
        if fetched is not None:
            return InfoTime("采集", fetched, period)
    m = _DATE_IN_ID.search(str(observation.get("document_id") or ""))
    if m:
        try:
            at = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
            return InfoTime("文档日期", at, period)
        except ValueError:
            pass
    return InfoTime("", None, period)


_DOC_TYPE_CN = {
    "earnings_transcript": "电话会纪要", "company_release": "公司公告", "release": "财报",
    "research_article": "文章", "article": "文章", "series_reading": "序列读数",
}


def _source_label(observation: dict, document: dict | None) -> str:
    if document:
        entity = document.get("entity") or observation.get("source_entity") or ""
        kind = _DOC_TYPE_CN.get(document.get("doc_type") or "", document.get("doc_type") or "文档")
        period = document.get("period") or ""
        label = f"{entity} {kind}"
        if period and len(period) <= 16:
            label += f" {period}"
        return label.strip()
    # No registered document (legacy local transcript files, observer series): show what the
    # id names — "AMD AMD-Q2-FY2026-transcript.md" says more than "AMD".
    doc_id = str(observation.get("document_id") or "").replace(":", " ", 1).strip()
    if len(doc_id) > 48:
        doc_id = doc_id[:47] + "…"
    return doc_id or observation.get("source_entity") or "未知来源"


def observation_sources(ids, reader) -> dict[str, SourceStamp]:
    """Resolve observations to their source document and information time, in one batch.

    `reader` is anything with `observations_by_id` / `documents_by_id` (the released
    unstructured read router). Observations that do not resolve are simply absent.
    """
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    observations = reader.observations_by_id(ids)
    doc_ids = sorted({o["document_id"] for o in observations.values() if o.get("document_id")})
    documents = reader.documents_by_id(doc_ids) if doc_ids else {}
    out: dict[str, SourceStamp] = {}
    for oid, obs in observations.items():
        doc = documents.get(obs.get("document_id") or "")
        out[oid] = SourceStamp(_source_label(obs, doc), info_time(obs, doc))
    return out


def stamp_cell(observation_ids, stamps: dict[str, SourceStamp] | None) -> str:
    """One table cell for a row citing several observations: the newest source, plus how many."""
    if stamps is None:
        return "—"
    found = [stamps[i] for i in observation_ids or [] if i in stamps]
    if not found:
        return "—（未找到来源）" if observation_ids else "—"
    labels: dict[str, SourceStamp] = {}
    for s in found:
        current = labels.get(s.label)
        if current is None or _sort_key(s) > _sort_key(current):
            labels[s.label] = s
    newest = max(labels.values(), key=_sort_key)
    cell = f"{newest.label} · {newest.time.text()}"
    if len(labels) > 1:
        cell += f"（等 {len(labels)} 份）"
    return cell


def _sort_key(s: SourceStamp):
    return s.time.at or datetime.min.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# projections and their data vintages
# --------------------------------------------------------------------------- #
def freshness_seconds(role: str) -> int | None:
    from ..workflow.planning import FRESHNESS_SECONDS, TASK_ROLE

    for task, r in TASK_ROLE.items():
        if r == role:
            return FRESHNESS_SECONDS.get(task)
    return None


def cite_projection(item: dict, decided_at: datetime) -> str:
    """`层分析·L5_chip_design · 截至 11-14 01:30 · 4.5 天前 ⚠️` for one snapshot item."""
    role = item.get("agent_role") or item.get("role") or item.get("task_id") or ""
    scope = item.get("scope_id") or item.get("scope") or ""
    head = ROLE_CN.get(role, role) + (f"·{scope}" if scope else "")
    at = parse_time(item.get("as_of"))
    if at is None:
        return f"{head} · 截至时间未知"
    out = f"{head} · 截至 {_short(at)} · {age_text(at, decided_at)}"
    window = freshness_seconds(role)
    if window is not None and (decided_at - at).total_seconds() > window:
        out += (f" ⚠️超出 {window / 86_400:g} 天窗口" if window >= 2 * 86_400
                else f" ⚠️超出 {window / 3600:g} 小时窗口")
    return out


_SESSION_CN = {"before-close": "收盘前", "after-close": "收盘后"}
_FACTSET_NOTE = {"registered_no_data": "已登记、无数据", "partial_sectors": "部分行业"}
# Bookkeeping markers in data_vintage_refs, not information sources.
_BOOKKEEPING = ("layer-budget-bound", "forced-recompute", "baseline-refresh")


def _split_at(rest: str) -> tuple[str, str]:
    head, _, tail = rest.partition("@")
    return head, tail


def _stamp(tail: str) -> str:
    at = parse_time(tail.split(":", 1)[0] if "T" not in tail else tail)
    return f" · 采集 {_short(at)}" if at is not None else ""


def describe_vintage_ref(ref: str) -> str | None:
    """A data-vintage ref in words, with its time when the ref carries one.

    Returns None for bookkeeping markers. Unknown formats are shown as they are,
    marked, never raised on: new producers add formats without notice.
    """
    prefix, _, rest = str(ref).partition(":")
    if prefix in _BOOKKEEPING:
        return None
    head, tail = _split_at(rest)
    parts = head.split(":")
    if prefix == "consensus":
        return f"一致预期 {parts[0]}" + (_stamp(tail) or " · 采集时间未带")
    if prefix == "market-session" and len(parts) >= 3:
        return f"行情 {parts[0]} · {parts[1]} {_SESSION_CN.get(parts[2], parts[2])}"
    if prefix == "financials" and len(parts) >= 2:
        return f"财务数据 {parts[0]} · {parts[1]} 行（未带时间）"
    if prefix == "evidence" and len(parts) >= 2:
        return f"产业链观测 {parts[0]} · {parts[1]} 条（逐条时间见层分析日报）"
    if prefix == "documents" and len(parts) >= 2:
        return f"文档 {parts[0]} · {parts[1]} 份（未带时间）"
    if prefix == "knowledge":
        return f"判据笔记 {head.rsplit('/', 1)[-1]}"
    if prefix == "macro-regional" and len(parts) >= 2:
        return f"区域出口 {parts[0]} · 数据期 {parts[1]}" + _stamp(tail)
    if prefix == "macro-series":
        return f"宏观序列 · {parts[0]} 条（逐条截至见宏观日报）"
    if prefix == "macro-news":
        return f"宏观新闻 · {parts[0]} 篇（未带时间）"
    if prefix in ("macro-factset", "factset") and len(parts) >= 2:
        note = tail.partition(":")[2]
        return (f"FactSet {parts[0]} · {parts[1]} 期"
                + (f"（{_FACTSET_NOTE.get(note, note)}）" if note else ""))
    if prefix == "baseline-window" and len(parts) >= 2:
        state = "已关闭" if (parts[2:] or [""])[0] == "closed" else (parts[2:] or [""])[0]
        return f"财报前基线窗口 {parts[0]} {parts[1]}" + (f"（{state}）" if state else "")
    if prefix == "observer-data" and parts:
        return f"观测器数据 {parts[0]}" + (_stamp(tail) or "")
    return f"{prefix} {head}（未登记格式）"
