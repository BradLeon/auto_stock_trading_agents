"""Technical analyst read entry (Phase D 8.5).

技术面取数只经这里：行情代码归一与价格批量拉取。Provider 模块（data.base /
sector_snapshot）不再是 agents 的合法导入面。
"""

from __future__ import annotations


def yf_symbol(symbol: str) -> str:
    """归一化为行情源代码（HY9H -> SKHY 这类别名折叠的第一步）。"""
    from ..base import yf_symbol as _yf_symbol

    return _yf_symbol(symbol)
