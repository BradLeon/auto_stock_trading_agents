"""Sector/layer analyst read entry (Phase D 8.3).

行业与层级分析的取数只经这里：产业链知识库、FactSet 行业背景、一致预期、
成分股财务、区域月度与价格快照。Provider 模块（industry / factset / consensus /
fundamentals / regional / sector_snapshot）不再是 agents 的合法导入面。
"""

from __future__ import annotations


# --- 产业链知识库（策展笔记，只读） ---------------------------------------- #

def industry_notes() -> list[tuple[str, str]]:
    from .. import industry

    return industry.fetch_notes()


def industry_named(paths: list[str], cap: int = 16000) -> list[tuple[str, str]]:
    from .. import industry

    return industry.fetch_named(paths, cap=cap)


def industry_context(notes: list[tuple[str, str]]) -> str:
    from .. import industry

    return industry.as_context(notes)


def industry_criteria_spans(text: str) -> list[tuple[int, int]]:
    """知识库评分标准段落的定位（kb_perturb 用，纯文本工具）。"""
    from .. import industry

    return industry.criteria_spans(text)


# --- FactSet 行业材料（本地 DataProducts 快照） ----------------------------- #

def factset_sector_context() -> str:
    from .. import factset

    return factset.fetch_sector_context()


def factset_sector_material() -> dict:
    from .. import factset

    return factset.fetch_sector_material()


# --- 区域月度与一致预期/财务 ------------------------------------------------ #

def regional_monthly(consumer: str):
    from .. import regional

    return regional.fetch(consumer=consumer)


def consensus_for(symbol: str, *, consumer: str = "sector_consensus") -> dict:
    from .. import consensus

    return consensus.fetch(symbol, consumer=consumer)


def constituent_financials(symbol: str, **kwargs):
    from .. import fundamentals

    return fundamentals.fetch_constituent_financials(symbol, **kwargs)


# --- 价格快照（行情） -------------------------------------------------------- #

def sector_prices(symbols: list[str], period: str = "1y") -> dict[str, list[float]]:
    from .. import sector_snapshot

    return sector_snapshot.fetch_prices(symbols, period=period)


def price_momentum(closes: list[float], days: int) -> float | None:
    from .. import sector_snapshot

    return sector_snapshot.momentum(closes, days)


def distance_to_high(closes: list[float]) -> float | None:
    from .. import sector_snapshot

    return sector_snapshot.dist_to_high(closes)
