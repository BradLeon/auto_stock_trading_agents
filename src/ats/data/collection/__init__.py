"""Data-layer acquisition entries (Phase D 8.1).

采集侧确定性组件：取数、原始资产写入与来源分级都住在这里，`agents/` 只保留
观点/抽取逻辑并经本包读取。此前这些函数住在 `agents/evidence/observer.py`，
让「证据观察器」同时是采集器与抽取器；守卫不得不为它登记一批「Phase D 例外」。
采集迁入数据层后，`agents/` 树内不再出现任何 Provider 直连或原始资产写入。
"""

from .evidence import (
    RANK_KEYED,
    RANK_MANUAL,
    RANK_PUBLIC,
    RANK_SEARCH,
    fetch_document,
    fetch_release,
    identify_asset,
    source_rank,
)

__all__ = [
    "RANK_KEYED",
    "RANK_MANUAL",
    "RANK_PUBLIC",
    "RANK_SEARCH",
    "fetch_document",
    "fetch_release",
    "identify_asset",
    "source_rank",
]
