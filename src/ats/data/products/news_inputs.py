"""News / fiscal-label read entries (Phase D 8.4).

基本面监控的新闻取数经 `monitor_news`；财年标签解析是纯函数工具（不访问
Provider），经 `fiscal_tools` 归入数据产品工具层。
"""

from __future__ import annotations


def monitor_news(symbol: str, since, until=None, *, consumer: str = "pead_monitor"):
    """一个标的的新闻流（PEAD 监控路径）。"""
    from .. import news

    return news.fetch_news(symbol, since, until, consumer=consumer)
