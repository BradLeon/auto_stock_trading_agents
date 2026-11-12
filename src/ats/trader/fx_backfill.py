"""Fill in `fills.fx_to_usd` / `fx_source` (and a blank `currency`) on rows recorded before the rate was kept.

`ats clerk fx-backfill [--dry-run]`. Writes only those columns, only where they are empty; never touches price,
shares, realized P&L or commission. Order of preference for a non-USD row:
1. the Flex statement's own rate for that exec id (`flex`, exact),
2. today's account ExchangeRate (`estimate_now`, flagged as an estimate in the report),
3. nothing — the row stays `missing` and the report says so.
A blank currency is filled only when every other fill of the same symbol agrees on one currency.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from . import fx

log = logging.getLogger("ats.fx")


def _blank_currencies(conn, *, dry_run: bool) -> tuple[dict[str, str], list[str]]:
    fixed, unresolved = {}, []
    for symbol, in conn.execute("SELECT DISTINCT symbol FROM fills WHERE COALESCE(currency, '') = ''").fetchall():
        known = {r[0].upper() for r in conn.execute(
            "SELECT DISTINCT currency FROM fills WHERE symbol = ? AND COALESCE(currency, '') != ''", (symbol,))}
        if len(known) == 1:
            currency = known.pop()
            if not dry_run:
                conn.execute("UPDATE fills SET currency = ? WHERE symbol = ? AND COALESCE(currency, '') = ''",
                             (currency, symbol))
            fixed[symbol] = currency
        else:
            unresolved.append(symbol)
    return fixed, unresolved


def _statement_rate(statement: list[dict], key: str, side: str, shares, price, time) -> float | None:
    """The statement's rate for the same execution. TWS and Flex use different exec ids, so match as the Flex
    back-fill does: same instrument, side, quantity and price, within `_DUP_SECONDS` of each other."""
    from .flex_statement import _DUP_SECONDS

    try:
        when = datetime.fromisoformat((time or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    for f in statement:
        if (f["instrument_key"] == key and f["side"] == side and abs(float(shares or 0) - f["shares"]) < 1e-9
                and abs(float(price or 0) - f["price"]) < 1e-6
                and abs((datetime.fromisoformat(f["time"]) - when).total_seconds()) <= _DUP_SECONDS):
            return f["fx_to_usd"]
    return None


def backfill(store=None, *, dry_run: bool = False, flex_fills: list[dict] | None = None,
             live_rates: Callable[[], tuple[str, dict[str, float]]] | None = None) -> dict:
    """`flex_fills`: parsed statement rows (`flex_statement.parse_trades`). `live_rates`: () -> (base, rates)."""
    from ..memory import get_store

    store = store or get_store()
    conn = store.conn
    fixed, unresolved = _blank_currencies(conn, dry_run=dry_run)
    statement = [f for f in (flex_fills or []) if f.get("fx_to_usd")]
    base, rates = "USD", {}
    if live_rates is not None:
        try:
            base, rates = live_rates()
        except Exception as exc:  # noqa: BLE001 - without rates the rows simply stay missing
            log.warning("fx back-fill: live rates unavailable: %s", exc)
    counts = {"usd": 0, "flex": 0, "estimate_now": 0, "missing": 0}
    still_missing: list[str] = []
    rows = conn.execute("SELECT exec_id, symbol, instrument_key, side, shares, price, time, currency "
                        "FROM fills WHERE fx_to_usd IS NULL").fetchall()
    for exec_id, symbol, key, side, shares, price, time, currency in rows:
        currency = (currency or "").upper() or fixed.get(symbol, "")          # what a real run would have just filled in
        flex_rate = _statement_rate(statement, key or symbol, side, shares, price, time)
        if currency == fx.USD:
            rate, source = 1.0, "usd"
        elif flex_rate:
            rate, source = flex_rate, "flex"
        elif currency and (live := fx.usd_rate(currency, base, rates)):
            rate, source = live, "estimate_now"
        else:
            counts["missing"] += 1
            still_missing.append(f"{symbol} {currency or '币种未记'}")
            continue
        counts[source] += 1
        if not dry_run:
            conn.execute("UPDATE fills SET fx_to_usd = ?, fx_source = ? WHERE exec_id = ? AND fx_to_usd IS NULL",
                         (rate, source, exec_id))
    if not dry_run:
        conn.commit()
    return {"dry_run": dry_run, "rows_without_rate": len(rows), "set": counts, "currency_filled": [f"{k}→{v}" for k, v in fixed.items()],
            "currency_unresolved": unresolved, "still_missing": sorted(set(still_missing))}


def render(out: dict) -> str:
    c = out["set"]
    lines = [f"成交汇率补全{'（演练，未写入）' if out['dry_run'] else ''}：缺汇率的成交 {out['rows_without_rate']} 笔——"
             f"美元 {c['usd']}、Flex 对账单汇率 {c['flex']}、按当前汇率估算 {c['estimate_now']}、仍缺 {c['missing']}"]
    if out["currency_filled"]:
        lines.append("  币种补全：" + "、".join(out["currency_filled"]))
    if out["currency_unresolved"]:
        lines.append("  币种仍无法判定：" + "、".join(out["currency_unresolved"]))
    if out["still_missing"]:
        lines.append("  仍缺汇率：" + "、".join(out["still_missing"]))
    return "\n".join(lines)
