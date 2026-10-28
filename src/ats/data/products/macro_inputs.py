"""Macro analyst read entry (Phase D 8.2).

宏观评审的取数只经这里：宏观指标序列、区域月度产品、FactSet 盈利材料与已准入的主题新闻。
Provider 模块（factset / regional / websearch）不再是 agents 的合法导入面——
这一层才是稳定契约，底层取数可以换实现而不动评审代码。

主题新闻只读数据平台已准入的文档（`data/collection/macro_news.py` 按刷新控制器的
日程采集），评审过程中不再检索外部（2026-10-16，ARCHITECTURE §12.1）。
"""

from __future__ import annotations


def regional_monthly(consumer: str):
    """已发布的区域月度数据快照（governed local product；offline 仍可读）。"""
    from .. import regional

    return regional.fetch(consumer=consumer)


def factset_macro_material():
    """S&P500 盈利/估值完整分析材料（发布于本地 DataProducts 快照，不联网）。"""
    from .. import factset

    return factset.fetch_macro_material()


def macro_snapshot(*, as_of=None):
    """最新宏观指标快照（`MacroData`）。读已采集的序列，不联网；缺口与过期写进 `notes`。"""
    from . import macro_series

    return macro_series.snapshot(macro_series.load(as_of=as_of))


def macro_series_histories(*, as_of=None) -> dict:
    """指标框架用的完整历史 `{key: pandas.Series}`；没有数据的 key 不出现。"""
    from . import macro_series

    return macro_series.load(as_of=as_of).series


def series_spec() -> dict:
    """key -> (provider code, 中文标签, unit, freq)，即评审读数的口径定义。"""
    from .. import macro

    return macro.series_spec()


def admitted_news(theme_key: str, *, days: int = 21, limit: int = 8, now=None, repository=None) -> list:
    """某主题近 `days` 天已准入的新闻文档（不联网，不触发采集）。

    返回 `AdmittedDocument`：带文档版本、来源 URL、发布时间与完整度标记
    （搜索服务的正文摘录为 `partial`）。没有材料就是空列表。
    """
    from datetime import datetime, timedelta, timezone

    from ..collection.macro_news import SOURCE_ID, theme_entity
    from ..document_types import compatible_type_values
    from .unstructured import admitted_documents

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    docs = admitted_documents(
        entities=[theme_entity(theme_key)], document_types=compatible_type_values("news_item"),
        published_since=since, limit=limit, repository=repository)
    return [doc for doc in docs if doc.source == SOURCE_ID]


# --------------------------------------------------------------------------- #
# Stored-input state: what the Macro review would read right now (2026-10-17)
# --------------------------------------------------------------------------- #
# One read-only pass over the four stored inputs of the review. The consumer health check judges it
# and the Dispatcher turns it into a data-vintage reference (so an unchanged input set is reused,
# ARCHITECTURE §8.2-5). It reads only through `products`, never calls a provider and never writes: the
# regional derivations (`RegionalProducts.snapshot`) register a derivation row, so levels are read
# straight from `metric_series` here instead.
def stored_input_state(products, *, as_of=None, news_days: int | None = None, themes=None) -> dict:
    """`{series|news|regional|factset: {"value": ..., "error": "" | message}}`; a failed input does not stop the others."""
    from datetime import datetime, timezone

    as_of = as_of or datetime.now(timezone.utc)
    state: dict = {}

    def attempt(name, read):
        try:
            state[name] = {"value": read(), "error": ""}
        except Exception as exc:  # noqa: BLE001 - the caller decides whether an unreadable input is fatal
            state[name] = {"value": None, "error": f"{type(exc).__name__}: {exc}"}

    from . import macro_series

    attempt("series", lambda: macro_series.load(as_of=as_of, repository=products.structured, now=as_of))

    def news():
        cfg_themes, days = themes, news_days
        if cfg_themes is None or days is None:
            from ...config import load_macro_config

            cfg = load_macro_config("macro")
            cfg_themes = [t.key for t in cfg.themes if t.queries] if cfg_themes is None else cfg_themes
            days = int(cfg.search.get("recency_days", 21)) if days is None else days
        return {"days": days, "themes": {key: admitted_news(key, days=days, limit=50, now=as_of,
                                                              repository=products.unstructured)
                                         for key in cfg_themes}}

    attempt("news", news)

    def regional():
        from .regional import _SERIES

        points = []
        for identifier, label, metric, entity, dataset, source in _SERIES:
            result = products.metric_series(metric=metric, entity=entity, dataset=dataset,
                                            source_id=source, quality="loose")
            rows = sorted(result["rows"], key=lambda row: row["period"])
            points.append({"id": identifier, "label": label,
                           "period": rows[-1]["period"] if rows else "",
                           "known_at": str(rows[-1]["known_at"]) if rows else ""})
        return points

    attempt("regional", regional)

    def factset():
        packet = products.earnings_insight_analysis_packet(as_of=as_of)
        report_date = getattr(packet.report, "report_date", None)
        return {"version_id": packet.report.version_id or "", "report_date": str(report_date) if report_date else "",
                "age_days": packet.status.age_days, "state": packet.status.state,
                "freshness": packet.status.freshness}

    attempt("factset", factset)
    return state


def input_vintage_refs(state: dict) -> list[str]:
    """Data-vintage references of the stored inputs: they change exactly when an input the review reads changes.

    Series: the newest `known_at` per series (a new print or a revision both move it). News: the admitted
    document versions inside the lookback. Regional and FactSet: the latest period/version. An input that
    could not be read contributes nothing, so a broken input never masquerades as an unchanged one: with no
    readable input at all the list is empty and the task recomputes.
    """
    import hashlib
    import json

    def digest(values) -> str:
        return hashlib.sha256(json.dumps(sorted(values), ensure_ascii=False).encode()).hexdigest()[:16]

    refs: list[str] = []
    series = state.get("series", {}).get("value")
    if series is not None and series.series:
        pairs = [f"{key}@{series.known_at.get(key, '')}" for key in series.series]
        refs.append(f"macro-series:{len(pairs)}@{digest(pairs)}")
    news = state.get("news", {}).get("value")
    if news is not None:
        versions = [f"{doc.document_id}@{doc.version_id}" for docs in news["themes"].values() for doc in docs]
        refs.append(f"macro-news:{len(versions)}@{digest(versions)}")
    regional = state.get("regional", {}).get("value")
    if regional is not None:
        refs.extend(f"macro-regional:{p['id']}:{p['period'] or 'none'}@{p['known_at'] or 'none'}" for p in regional)
    factset = state.get("factset", {}).get("value")
    if factset is not None:
        refs.append(f"macro-factset:{factset['version_id'] or 'none'}")
    return refs
