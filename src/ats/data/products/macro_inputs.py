"""Macro analyst read entry (Phase D 8.2).

宏观评审的取数只经这里：区域月度产品、FactSet 盈利材料与主题新闻检索。
Provider 模块（factset / regional / websearch）不再是 agents 的合法导入面——
这一层才是稳定契约，底层取数可以换实现而不动评审代码。
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


def search_news(query: str, *, max_results: int = 4, days: int = 14,
                max_chars: int = 2000) -> list[dict]:
    """主题新闻检索（宏观定性块的时效输入）。"""
    from .. import websearch

    return websearch.search_news(query, max_results=max_results, days=days,
                                 max_chars=max_chars)
