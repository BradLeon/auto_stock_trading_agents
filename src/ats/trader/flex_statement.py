"""The broker's own execution record, from an IBKR Flex Query: it fills what the live API read missed.

Why it exists (blockers B11, tasks 11.12): `reqExecutions` returns only the account's executions since midnight
(TWS local time; IBKR's API documentation: "by default, only those executions occurring since midnight"). The
Clerk reads at 07:00 Taipei, so every fill made the evening before is gone by then. A Flex Query is the broker's
own statement of executions; it lags by one business day (a request made on the evening of 11-04 ET returns
through 11-03), so it is a back-fill source, not a live one. The live read before midnight
(`trader/fills_capture.py`) covers the current day; this covers whatever either read missed.

The query (`ats-trades`, Activity Flex Query, Trades at Execution level, XML, last 7 calendar days) is the
Boss's, created in the IBKR Client Portal; its token and query id are in `.env` (`IBKR_FLEX_TOKEN`,
`IBKR_FLEX_QUERY_ID`). Measured against the TWS trade log on 2026-11-05:

- `dateTime` is US Eastern time ("20261103;093001" is 14:30:01 UTC), `ibExecID` has the same format as the
  API's exec id, and `ibOrderID` is the permId (10 digits). A combo's legs carry per-leg exec ids.
- Prices, quantities and times matched the eight hand-entered fills to the second; commission differs only by
  rounding.

What this module refuses to do:

- It never rewrites a fill. A Flex row is inserted only when no fill with its exec id exists AND no fill records
  the same fact (instrument, side, quantity, price, within 2 seconds): the hand-entered `stmt:` rows and the
  PDF/CSV imports carry other exec ids for the same executions, and counting them twice is how a position goes
  wrong.
- Nothing before the Day-1 baseline date is imported: that history was sealed (`ats clerk baseline`).
- A statement it cannot parse is refused whole. Failure to reach Flex is reported in the summary and never
  raised: it must not fail a Clerk run.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("ats.flex")

SEND_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
STATEMENT_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
_ET = ZoneInfo("America/New_York")
_DUP_SECONDS = 2
_IN_PROGRESS = {"1019", "1018"}     # statement being generated / too many requests: wait and ask again


class FlexUnavailable(RuntimeError):
    """The statement could not be had (not configured, refused, malformed). Never carries the token."""


def credentials() -> tuple[str, str] | None:
    from ..config import get_config

    secrets = get_config().secrets
    if secrets.ibkr_flex_token and secrets.ibkr_flex_query_id:
        return secrets.ibkr_flex_token, secrets.ibkr_flex_query_id
    return None


def _http_get(url: str, timeout: float = 60) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "ats-flex/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed IBKR hosts
        return response.read().decode("utf-8", "replace")


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _root(body: str, token: str) -> ET.Element:
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise FlexUnavailable(f"response is not XML: {_scrub(body, token)[:120]!r}") from exc


def fetch_statement(token: str, query_id: str, *, get=_http_get, sleep=time.sleep, attempts: int = 8) -> str:
    """Two-step Flex Web Service call: ask for the statement, then poll for it. Returns the statement XML."""
    sent = _root(get(f"{SEND_URL}?t={urllib.parse.quote(token)}&q={urllib.parse.quote(query_id)}&v=3"), token)
    if (sent.findtext("Status") or "").lower() != "success":
        raise FlexUnavailable(f"request refused: {sent.findtext('ErrorCode')} {sent.findtext('ErrorMessage')}")
    reference = sent.findtext("ReferenceCode")
    if not reference:
        raise FlexUnavailable("request accepted without a reference code")
    url = sent.findtext("Url") or STATEMENT_URL
    for attempt in range(attempts):
        sleep(3 if attempt == 0 else 5)
        body = get(f"{url}?t={urllib.parse.quote(token)}&q={urllib.parse.quote(reference)}&v=3")
        root = _root(body, token)
        if root.tag == "FlexQueryResponse":
            return body
        code = root.findtext("ErrorCode") or ""
        if code in _IN_PROGRESS:
            continue
        raise FlexUnavailable(f"statement refused: {code} {root.findtext('ErrorMessage')}")
    raise FlexUnavailable("statement still being generated after the polling budget")


def _et_to_utc(raw: str) -> str:
    match = re.fullmatch(r"(\d{8})[;T ]?(\d{6})", (raw or "").strip())
    if not match:
        raise FlexUnavailable(f"unreadable dateTime {raw!r}")
    local = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(tzinfo=_ET)
    return local.astimezone(timezone.utc).isoformat()


def statement_through(body: str) -> str:
    """The last day the statement covers (`toDate`, US Eastern, as YYYY-MM-DD); '' when it states none."""
    dates = [d for d in (st.attrib.get("toDate", "") for st in _root(body, "").iter("FlexStatement"))
             if re.fullmatch(r"\d{8}", d or "")]
    return f"{max(dates)[:4]}-{max(dates)[4:6]}-{max(dates)[6:]}" if dates else ""


def parse_trades(body: str) -> list[dict]:
    """Executions as the ledger's fill dicts (the shape `IBKRBroker.get_fills` returns)."""
    from ..journal.episodes import NOT_RECORDED, occ_key

    root = _root(body, "")
    fills = []
    for trade in root.iter("Trade"):
        a = trade.attrib
        sec_type = a.get("assetCategory") or "STK"
        if sec_type in NOT_RECORDED:
            continue
        try:
            qty, price = float(a["quantity"]), float(a["tradePrice"])
            exec_id, side = a["ibExecID"], {"BUY": "BOT", "SELL": "SLD"}[a["buySell"].upper()]
        except (KeyError, ValueError) as exc:
            raise FlexUnavailable(f"unreadable trade row: {sorted(a)[:6]}") from exc
        symbol, key, multiplier = a.get("symbol", ""), a.get("symbol", ""), float(a.get("multiplier") or 1)
        right, strike, expiry = None, None, None
        if sec_type == "OPT":
            symbol = symbol.split()[0]
            right, strike, expiry = (a.get("putCall") or "")[:1].upper() or None, float(a.get("strike") or 0) or None, \
                a.get("expiry") or None
            if not (right and strike and expiry):
                raise FlexUnavailable(f"option row without contract fields: {a.get('symbol')!r}")
            key = occ_key(symbol, expiry, right, strike)
        fills.append({"exec_id": exec_id, "symbol": symbol, "side": side, "shares": abs(qty), "price": price,
                      "time": _et_to_utc(a.get("dateTime", "")), "realized_pnl": None,
                      "commission": abs(float(a.get("ibCommission") or 0)), "order_id": "",
                      "perm_id": a.get("ibOrderID", ""), "order_ref": a.get("orderReference", ""),
                      "sec_type": sec_type, "instrument_key": key, "multiplier": multiplier, "right": right,
                      "strike": strike, "expiry": expiry, "currency": a.get("currency", ""),
                      "con_id": a.get("conid", ""), "local_symbol": ""})
    return fills


def _known(fill: dict, existing: list[dict]) -> bool:
    """A fill with this exec id, or recording the same execution under another id (`stmt:` rows)."""
    t = datetime.fromisoformat(fill["time"])
    for e in existing:
        if e["exec_id"] == fill["exec_id"]:
            return True
        if ((e["instrument_key"] or e["symbol"]) == fill["instrument_key"] and e["side"] == fill["side"]
                and abs(float(e["shares"] or 0) - fill["shares"]) < 1e-9
                and abs(float(e["price"] or 0) - fill["price"]) < 1e-6):
            try:
                when = datetime.fromisoformat((e["time"] or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            if abs((when - t).total_seconds()) <= _DUP_SECONDS:
                return True
    return False


def _covered_by_a_whole(fills: list[dict], existing: list[dict]) -> set[int]:
    """Statement rows that are the parts of one recorded fill.

    The broker reports a partly-filled order as several executions (NVDA 2026-11-04: 6 + 14), while a hand-entered
    row from the trade log shows the one total (20). Same instrument, side and price, within two seconds, and the
    parts add up to the recorded quantity: the same execution, already in the ledger.
    """
    covered: set[int] = set()
    for e in existing:
        try:
            when = datetime.fromisoformat((e["time"] or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        parts = [f for f in fills
                 if (e["instrument_key"] or e["symbol"]) == f["instrument_key"] and e["side"] == f["side"]
                 and abs(float(e["price"] or 0) - f["price"]) < 1e-6
                 and abs((datetime.fromisoformat(f["time"]) - when).total_seconds()) <= _DUP_SECONDS]
        if len(parts) > 1 and abs(sum(f["shares"] for f in parts) - float(e["shares"] or 0)) < 1e-9:
            covered.update(id(f) for f in parts)
    return covered


class _Static:
    """A broker that returns the given fills: lets `reconcile` attribute and link them exactly as a live read."""

    def __init__(self, fills: list[dict]):
        self._fills = fills

    def get_fills(self) -> list[dict]:
        return self._fills

    def completed_orders(self) -> list[dict]:
        return []


def backfill(*, store=None, dry_run: bool = False, fetch=None) -> dict:
    """Insert the statement's executions the ledger does not have. Never raises; see the module docstring."""
    from ..execution.baseline import baseline_date
    from ..memory import get_store
    from . import reconcile

    creds = credentials()
    if creds is None:
        return {"status": "skipped", "reason": "IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID not set"}
    store = store or get_store()
    try:
        body = (fetch or fetch_statement)(*creds)
        fills = parse_trades(body)
        through = statement_through(body)
    except Exception as exc:  # noqa: BLE001 - a failed back-fill must not fail the Clerk run
        message = _scrub(str(exc), creds[0])[:200]
        log.warning("flex back-fill unavailable: %s", message)
        return {"status": "unavailable", "error": message}
    cutoff = baseline_date(store) or ""
    in_scope = [f for f in fills if f["time"][:10] >= cutoff]
    existing = [dict(r) for r in store.conn.execute(
        "SELECT exec_id, symbol, instrument_key, side, shares, price, time FROM fills")]
    covered = _covered_by_a_whole(in_scope, existing)
    missing = [f for f in in_scope if id(f) not in covered and not _known(f, existing)]
    summary = {"status": "ok", "through": through, "statement_rows": len(fills), "before_baseline": len(fills) - len(in_scope),
               "already_recorded": len(in_scope) - len(missing), "missing": len(missing), "inserted": 0,
               "backfilled": [f"{f['time'][:16]} {f['symbol']} {f['side']} {f['shares']:g} @ {f['price']:g}"
                              for f in missing], "dry_run": dry_run}
    if missing and not dry_run:
        result = reconcile.reconcile(_Static(missing), store=store, advance_window=False)
        summary["inserted"] = result["fills_new"]
        if result["errors"]:
            summary["errors"] = result["errors"]
        log.info("flex back-fill inserted %s fill(s): %s", summary["inserted"], summary["backfilled"])
    return summary


def render(summary: dict) -> str:
    if summary["status"] != "ok":
        return f"Flex 对账单：{summary['status']}（{summary.get('reason') or summary.get('error', '')}）"
    lines = [f"Flex 对账单：{summary['statement_rows']} 笔执行，早于建账日 {summary['before_baseline']} 笔，"
             f"库里已有 {summary['already_recorded']} 笔，缺 {summary['missing']} 笔，"
             f"{'将补入' if summary['dry_run'] else '已补入'} {summary['missing'] if summary['dry_run'] else summary['inserted']} 笔"]
    lines += [f"  · {row}" for row in summary["backfilled"]]
    return "\n".join(lines)
