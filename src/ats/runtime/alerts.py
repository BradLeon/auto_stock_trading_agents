"""Failure alerts from the dispatcher to the Boss (2026-11-19).

Until now a failed or blocked Chief cycle was only a log line: 11-13 and 11-17 the daily decision stopped at
`assemble_context` and nobody heard of it. These alerts go through the configured Boss channel (the same Feishu
bot as the data-health alerts). An alert never raises: a broken channel must not turn a reported failure into a
second one.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime

log = logging.getLogger("ats.dispatcher")

_BODY_LIMIT = 1800


def _push(title: str, body: str) -> None:
    try:
        from ..workflow.consumer_health import feishu_notify

        feishu_notify(title, body[:_BODY_LIMIT])
    except Exception:  # noqa: BLE001 - alerting must not break the dispatcher
        log.exception("alert not delivered: %s", title)


def _when(planned: datetime | None) -> str:
    return planned.astimezone().strftime("%m-%d %H:%M") if planned else "?"


def trade_workflow_failed(workflow_id: str, planned: datetime | None, exc: BaseException) -> None:
    """A trade workflow (chief-daily, clerk-daily) raised: no cycle, no report."""
    last = traceback.format_exception_only(type(exc), exc)[-1].strip()
    _push(f"⚠️ {workflow_id} 失败（{_when(planned)}）",
          f"{workflow_id} 本轮异常中止，没有产出报告。\n原因：{last}\n详见 var/logs/dispatcher.err.log。")


def chief_blocked(planned: datetime | None, result: dict) -> None:
    """The Chief cycle refused to run on an incomplete research snapshot (a gap report, no cycle)."""
    report = str(result.get("gap_report") or "（无缺口报告文本）")
    _push(f"⚠️ chief-daily 被阻断（{_when(planned)}）",
          f"研究快照不完整，本轮没有建立决策周期。\n{report}")


def chief_manual_review(planned: datetime | None, result: dict) -> None:
    """The cycle ran but left for manual review: there is nothing in `ats approval pending`."""
    _push(f"⚠️ chief-daily 转人工复核（{_when(planned)}）",
          f"周期 {result.get('cycle_id', '?')} 已产出决策，但没有进入审批队列（`ats approval pending` 里看不到）。\n"
          f"原因：{result.get('reason') or '（未记录）'}")


def overnight_cancel_failed(*, result: dict | None = None, exc: BaseException | None = None) -> None:
    """The session-end cancel (03:40 New York) could not cancel every overnight order: it may fill on a stale basis."""
    if exc is not None:
        last = traceback.format_exception_only(type(exc), exc)[-1].strip()
        body = f"隔夜时段收盘前撤单没有完成，可能仍有隔夜挂单。\n原因：{last}\n请在 TWS 里核对并手动撤单。"
    else:
        left = "；".join(f"{r.get('symbol')} {r.get('order_ref')}（{r.get('status')}）"
                        for r in (result or {}).get("still_working") or [])
        body = f"以下隔夜订单撤单后仍显示在途：{left}\n请在 TWS 里核对并手动撤单。"
    _push("⚠️ 隔夜挂单未撤净", body)


def research_incomplete(workflow_id: str, planned: datetime | None, result: dict, *, attempts: int) -> None:
    """A research workflow stayed incomplete after its retries; the names are stated uncovered by the Chief."""
    missing = sorted({str(item.get("scope", "")).partition(":")[2] or str(item.get("scope", ""))
                      for item in result.get("missing_requirements") or []})
    details = "\n".join(f"- {item.get('scope')}: {item.get('detail', '')}"[:200]
                        for item in (result.get("missing_requirements") or [])[:12])
    _push(f"⚠️ {workflow_id} 不完整（{_when(planned)}，共 {attempts} 次）",
          f"缺：{'、'.join(missing) or '（未列出）'}\n{details}\n"
          "chief-daily 会把这些标的标为缺口照常运行，本轮不对它们出单。")
