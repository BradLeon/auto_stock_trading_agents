"""Backfill historical executions from IBKR broker records.

Two exports are read: the 交易确认报告 (trade confirmation, PDF; execution rows
with time of day, checked against its own 总数 lines) and the Transaction History
CSV (one row per execution, DATE ONLY, checked by its net amounts summing to the
period's cash 变更 — see "Transaction History CSV" below).

`reqExecutions` only reaches back to the current session, so fills the system was
not running to see are gone from the API. The Boss exports a 交易确认报告 from the
broker back office instead (2026-10-29); this module reads it into `fills`.

What it trusts, and what it refuses:

- Only execution rows (the indented lines) of the 股票 and 股票和指数期权
  sections are imported; the order-level rows repeat them. Each option row gets
  its contract identity (`journal.episodes.occ_key`): OCC key, right, strike, expiry
  and the multiplier the row's own proceeds imply. FX (外汇) is skipped — currency
  moves between markets are not positions (Boss ruling 2026-10-29).
- The statement carries no orderRef, permId or execution id, so every row gets a
  content-derived `stmt:` exec id (re-importing is a no-op) and is attributed by
  the same `reconcile.attribute` as a live fill — never assumed to be a system
  order.
- Before anything is written, per instrument × side quantity must equal the
  statement's own 总数 line exactly, and proceeds and commission to within the
  cent rounding of the rows summed (each row is printed rounded, the total is
  not: measured SHV 3 rows sum to -33,086.26 against a printed -33,086.27).
  Any mismatch or unparsed trade line refuses the whole batch.
- A row that duplicates a fill already captured from TWS (same symbol, side,
  shares, price, within 2 seconds) is skipped.
- The statement has no realized P&L column; imported legs carry none rather than
  a number reconstructed from an estimated cost basis.

## Transaction History CSV

- Every row's 净额 must sum to the header's 变更 (cash change) to the cent, which
  proves no row was dropped; a USD trade's 总额 must equal quantity x price x
  multiplier. 总额 and 佣金 are in the base currency, so a non-USD row's own
  ratio gives its FX rate and its commission is converted back to the trade's
  currency (the unit TWS and the PDF record).
- Times are date-only. Rows are listed newest first; each day's executions are
  placed at 12:00 ET plus one second per row, oldest first, which keeps their
  relative order and nothing more. Exec ids carry `stmt:txn:`.
- A fill TWS captured before fills carried contract fields, matching a row on
  date, side, quantity and price, is not imported again; when that row names an
  option or FX contract, the match is recorded as the fill's classification
  (`decided_by="broker_record"`) — the broker's own record of which contract it
  was, replacing an earlier "contract unknown" classification.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..journal.episodes import is_unknown, occ_key

_ET = ZoneInfo("America/New_York")     # statement note 7: times are US Eastern
_SECTIONS = ("股票", "股票和指数期权", "外汇", "金融产品信息")
_TAKEN = {"股票": "STK", "股票和指数期权": "OPT"}
_OPTION = re.compile(r"^(?P<root>.+?) (?P<expiry>\d{2}[A-Z]{3}\d{2}) (?P<strike>[\d.]+) "
                     r"(?P<right>[CP])$")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
_NUM = r"-?[\d,]+(?:\.\d+)?"
_ROW = re.compile(
    rf"^(?P<indent>\s*)U\d+ (?P<symbol>.+?) (?P<date>\d{{4}}-\d{{2}}-\d{{2}}), "
    rf"(?P<clock>\d{{2}}:\d{{2}}:\d{{2}}) \d{{4}}-\d{{2}}-\d{{2}} (?P<venue>\S+) "
    rf"(?P<side>BUY|SELL) (?P<qty>{_NUM}) (?P<price>{_NUM}) (?P<proceeds>{_NUM}) "
    rf"(?P<commission>{_NUM}) (?P<fee>{_NUM})(?: .*)?$")
_TOTAL = re.compile(r"^\s*总数 (?P<symbol>.+?) \((?P<side>买入|卖出)\) (?P<rest>.*)$")
_DUP_SECONDS = 2


class StatementMismatch(ValueError):
    """The parsed executions do not reproduce the statement's own totals."""


@dataclass
class Parsed:
    fills: list[dict] = field(default_factory=list)
    totals: dict[tuple[str, str], tuple[float, float, float]] = field(default_factory=dict)
    skipped: Counter = field(default_factory=Counter)
    unparsed: list[str] = field(default_factory=list)
    date_only: bool = False
    fx: list[dict] = field(default_factory=list)      # FX rows: only to classify legacy fills


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def _side(raw: str) -> str:
    return "BOT" if raw in ("BUY", "买入") else "SLD"


def _group(key: str) -> str:
    """Totals print an OCC key with column padding that text extraction may not
    keep exactly; compare keys without spaces."""
    return key.replace(" ", "")


def _cost(raw: str) -> float:
    """The statement prints commission as a signed cash flow (cost negative, a
    rebate positive); the ledger records cost as positive."""
    return -_num(raw) or 0.0


def _identity(fill: dict) -> tuple:
    # Stocks key by symbol exactly as the first (stock-only) import did, so their
    # exec ids — and a re-import's idempotence — are unchanged.
    name = fill["symbol"] if fill["sec_type"] == "STK" else fill["instrument_key"]
    return (name, fill["time"], fill["side"], fill["shares"], fill["price"],
            fill["commission"], fill["venue"])


def _exec_id(fill: dict, occurrence: int) -> str:
    key = "|".join(str(v) for v in _identity(fill)) + f"|{occurrence}"
    return "stmt:" + hashlib.sha256(key.encode()).hexdigest()[:20]


def _contract(name: str, sec_type: str, qty: float, price: float,
              proceeds: float, currency: str) -> dict:
    if sec_type == "STK":
        return {"symbol": name, "sec_type": "STK", "instrument_key": name,
                "multiplier": 1.0, "right": None, "strike": None, "expiry": None,
                "currency": currency}
    m = _OPTION.match(name)
    if m is None:
        raise StatementMismatch(f"option row not recognised: {name!r}")
    day, mon, yy = m["expiry"][:2], m["expiry"][2:5], m["expiry"][5:]
    expiry = f"20{yy}{_MONTHS[mon]:02d}{day}"
    mult = round(abs(proceeds) / (qty * price)) if qty and price else 100
    return {"symbol": m["root"], "sec_type": "OPT",
            "instrument_key": occ_key(m["root"], expiry, m["right"], float(m["strike"])),
            "multiplier": float(mult), "right": m["right"], "strike": float(m["strike"]),
            "expiry": expiry, "currency": currency}


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".csv":
        return path.read_text(encoding="utf-8-sig")
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


_OCC = re.compile(r"^(?P<root>.{1,6}?)\s*(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")
_TRADE_KINDS = {"买": "BOT", "卖": "SLD"}


def _float(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_transactions(text: str) -> Parsed:
    out = Parsed(date_only=True)
    rows = list(csv.reader(io.StringIO(text)))
    change = next((_float(r[3]) for r in rows
                   if len(r) > 3 and r[0] == "总结" and r[2] == "变更"), None)
    tx = [r for r in rows if len(r) >= 13 and r[0] == "Transaction History" and r[1] == "Data"]
    net = sum(_float(r[12]) or 0.0 for r in tx)
    if change is None or abs(net - change) > 0.01:
        out.unparsed.append(f"net amounts {net:.2f} do not sum to 变更 {change}")
    by_day: dict[str, list[list[str]]] = defaultdict(list)
    for r in tx:
        by_day[r[2]].append(r)
    seen: Counter = Counter()
    for day, day_rows in sorted(by_day.items()):
        for i, r in enumerate(reversed(day_rows)):       # file lists newest first
            kind, code = r[5], r[6]
            qty, price, total, comm = (_float(r[7]), _float(r[8]), _float(r[10]),
                                       _float(r[11]))
            local = datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=_ET)
            at = (local.astimezone(timezone.utc) + timedelta(seconds=i)).isoformat()
            if kind == "外汇交易组成部分":
                base, _, _ = code.partition(".")
                out.fx.append({"symbol": base, "side": "BOT" if (qty or 0) > 0 else "SLD",
                               "shares": abs(qty or 0), "price": price, "date": day})
                out.skipped["外汇"] += 1
                continue
            if kind not in _TRADE_KINDS:
                out.skipped[kind] += 1
                continue
            if not (qty and price and total is not None):
                out.unparsed.append(",".join(r))
                continue
            occ = _OCC.match(code)
            mult = 100.0 if occ else 1.0
            fx = abs(total) / (abs(qty) * price * mult)
            if r[9] == "USD" and abs(abs(total) - abs(qty) * price * mult) > 0.01:
                out.unparsed.append(",".join(r))
                continue
            if occ:
                expiry = "20" + occ["yymmdd"]
                contract = {"symbol": occ["root"].strip(), "sec_type": "OPT",
                            "instrument_key": occ_key(occ["root"].strip(), expiry,
                                                      occ["right"], int(occ["strike"]) / 1000),
                            "multiplier": mult, "right": occ["right"],
                            "strike": int(occ["strike"]) / 1000, "expiry": expiry}
            else:
                contract = {"symbol": code, "sec_type": "STK", "instrument_key": code,
                            "multiplier": 1.0, "right": None, "strike": None, "expiry": None}
            fill = {**contract, "currency": r[9], "side": _TRADE_KINDS[kind],
                    "shares": abs(qty), "price": price, "time": at,
                    "commission": round(-(comm or 0.0) / fx, 6) or 0.0,
                    "realized_pnl": None, "order_id": "", "perm_id": "", "order_ref": "",
                    "venue": "", "proceeds": total / fx, "date": day}
            base_id = _identity(fill)
            key = "|".join(str(v) for v in base_id) + f"|{seen[base_id]}"
            seen[base_id] += 1
            fill["exec_id"] = "stmt:txn:" + hashlib.sha256(key.encode()).hexdigest()[:20]
            out.fills.append(fill)
    return out


def parse(text: str) -> Parsed:
    out = Parsed()
    section = ""
    currency = ""
    lines = text.splitlines()
    seen: Counter = Counter()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.strip() in _SECTIONS:
            section = line.strip()
            continue
        if re.fullmatch(r"[A-Z]{3}", line.strip()):
            currency = line.strip()
            continue
        total = _TOTAL.match(line)
        if total and section in _TAKEN:
            rest = total["rest"].split()
            if len(rest) < 5 and i < len(lines):      # a long average price wrapped
                rest = (total["rest"] + lines[i].strip()).split()
                i += 1
            out.totals[(_group(total["symbol"]), _side(total["side"]))] = (
                abs(_num(rest[0])), _num(rest[-3]), _cost(rest[-2]))
            continue
        row = _ROW.match(line)
        if row is None:
            if re.match(r"^\s*U\d+ ", line) and section != "金融产品信息":
                out.unparsed.append(line)
            continue
        if not row["indent"]:
            continue                                # order-level row; its executions follow
        if section not in _TAKEN:
            out.skipped[section or "unknown"] += 1
            continue
        local = datetime.fromisoformat(f"{row['date']}T{row['clock']}").replace(tzinfo=_ET)
        qty, price, proceeds = abs(_num(row["qty"])), _num(row["price"]), _num(row["proceeds"])
        fill = {**_contract(row["symbol"].strip(), _TAKEN[section], qty, price, proceeds,
                            currency),
                "side": _side(row["side"]), "shares": qty, "price": price,
                "time": local.astimezone(timezone.utc).isoformat(),
                "commission": _cost(row["commission"]), "realized_pnl": None,
                "order_id": "", "perm_id": "", "order_ref": "",
                "venue": row["venue"], "proceeds": proceeds}
        base = _identity(fill)
        fill["exec_id"] = _exec_id(fill, seen[base])
        seen[base] += 1
        out.fills.append(fill)
    return out


def verify(parsed: Parsed) -> None:
    """Refuse unless every symbol × side sums to the statement's own 总数 line."""
    if parsed.unparsed:
        raise StatementMismatch(f"{len(parsed.unparsed)} trade line(s) not parsed")
    sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for f in parsed.fills:
        acc = sums[(_group(f["instrument_key"]), f["side"])]
        acc[0] += f["shares"]
        acc[1] += f["proceeds"]
        acc[2] += f["commission"]
        acc[3] += 1
    if set(sums) != set(parsed.totals):
        raise StatementMismatch(
            f"groups without a matching 总数 line: {sorted(set(sums) ^ set(parsed.totals))}")
    def off(got: float, want: float, rows: int) -> bool:
        return abs(got - want) > 0.005 * rows + 1e-9

    bad = [k for k, (q, p, c) in parsed.totals.items()
           if abs(sums[k][0] - q) > 1e-6 or off(sums[k][1], p, sums[k][3])
           or off(sums[k][2], c, sums[k][3])]
    if bad:
        raise StatementMismatch(f"totals differ for {sorted(bad)}")


def _et_date(stamp: str) -> str:
    try:
        t = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(_ET).date().isoformat()


def _uncontracted(e: dict) -> bool:
    """Captured without contract fields, and not yet tied to a named contract."""
    return e["sec_type"] is None and (is_unknown(e["ikey"])
                                      or (e["ikey"] == e["symbol"] and e["itype"] == "STK"))


def _legacy_match(fill: dict, existing: list[dict]) -> dict | None:
    """Date-only row vs a captured fill: same day, side, quantity and price, and
    the same instrument — or a fill captured without contract fields whose
    symbol is this row's underlying."""
    for e in existing:
        if (_et_date(e["time"]) == fill["date"] and e["side"] == fill["side"]
                and abs(float(e["shares"] or 0) - fill["shares"]) < 1e-9
                and abs(float(e["price"] or 0) - (fill["price"] or 0)) < 1e-6
                and (e["ikey"] == fill.get("instrument_key")
                     or (_uncontracted(e) and e["symbol"] == fill["symbol"]))):
            return e
    return None


def _is_duplicate(fill: dict, existing: list[dict]) -> bool:
    if "date" in fill:
        return _legacy_match(fill, existing) is not None
    t = datetime.fromisoformat(fill["time"])
    for e in existing:
        if (e["ikey"] == fill["instrument_key"] and e["side"] == fill["side"]
                and abs(float(e["shares"] or 0) - fill["shares"]) < 1e-9
                and abs(float(e["price"] or 0) - fill["price"]) < 1e-6):
            try:
                et = datetime.fromisoformat((e["time"] or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if et.tzinfo is None:
                et = et.replace(tzinfo=timezone.utc)
            if abs((et - t).total_seconds()) <= _DUP_SECONDS:
                return True
    return False


def import_statement(path: Path, *, store=None, dry_run: bool = False) -> dict:
    from ..memory import get_store
    from .reconcile import _manual_since, attribute

    store = store or get_store()
    text = read_text(path)
    if path.suffix.lower() == ".csv":
        parsed = parse_transactions(text)
        if parsed.unparsed:
            raise StatementMismatch(f"{len(parsed.unparsed)} row(s) failed: {parsed.unparsed[0][:120]}")
    else:
        parsed = parse(text)
        verify(parsed)
    existing = [dict(r) for r in store.conn.execute(
        f"SELECT * FROM ({store._FILL_INSTRUMENT}) "
        "WHERE exec_id NOT LIKE 'stmt:%'").fetchall()]
    fresh = [f for f in parsed.fills if not _is_duplicate(f, existing)]
    # The broker's own record of which contract an uncontracted legacy fill was.
    legacy = []
    for row in parsed.fills:
        m = _legacy_match(row, existing) if parsed.date_only else None
        if m is not None and _uncontracted(m) and row["sec_type"] != "STK":
            legacy.append((m["exec_id"], row["sec_type"], row["instrument_key"],
                           row["multiplier"], row["date"]))
    for fx in parsed.fx:
        m = _legacy_match({**fx, "instrument_key": None}, existing)
        if m is not None and _uncontracted(m):
            legacy.append((m["exec_id"], "CASH", fx["symbol"], 1.0, fx["date"]))
    trades = [dict(r) for r in store.conn.execute(
        "SELECT rowid AS rid, * FROM trades ORDER BY rowid").fetchall()]
    since = _manual_since()
    classified = [(f, *attribute(f, trades, since)) for f in fresh]
    summary = {"executions": len(parsed.fills),
               "verified_by": ("全部行净额合计 = 期间现金变更" if parsed.date_only
                               else f"{len(parsed.totals)} 组与总数行一致"),
               "skipped_sections": dict(parsed.skipped),
               "duplicates_of_tws": len(parsed.fills) - len(fresh),
               "legacy_classified": [(e, k) for e, _, k, _, _ in legacy],
               "origins": dict(Counter(origin for _, _, origin, _ in classified)),
               "by_type": dict(Counter(f["sec_type"] for f in fresh)),
               "inserted": 0, "contract_backfilled": 0, "dry_run": dry_run}
    if dry_run:
        return summary
    summary["inserted"] = store.upsert_fills(fresh)
    for exec_id, sec_type, key, mult, day in legacy:
        store.classify_fill(exec_id, sec_type=sec_type, instrument_key=key, multiplier=mult,
                            basis=f"{path.name}: {day} row names {key}",
                            decided_by="broker_record")
    # Rows from the first, stock-only import predate the contract columns: fill
    # those blanks in (never overwriting a value already recorded).
    summary["contract_backfilled"] = sum(store.conn.execute(
        "UPDATE fills SET sec_type = ?, instrument_key = ?, multiplier = ?, right = ?, "
        "strike = ?, expiry = ?, currency = ? WHERE exec_id = ? AND sec_type IS NULL",
        (f["sec_type"], f["instrument_key"], f["multiplier"], f["right"], f["strike"],
         f["expiry"], f["currency"], f["exec_id"])).rowcount for f in fresh)
    for f, row, origin, how in classified:
        if row is not None:
            # Never expected (the statement carries no linking evidence), but if
            # perm/order evidence ever appears the fill must inherit its chain.
            store.conn.execute(
                "UPDATE fills SET origin = ?, link_confidence = ?, entry_id = ?, cycle_id = ?, "
                "revision_no = ?, decision_hash = ?, approval_id = ? WHERE exec_id = ?",
                (origin, how, row.get("client_order_id"), row.get("cycle_id"),
                 row.get("revision_no"), row.get("decision_hash") or "",
                 row.get("approval_id") or "", f["exec_id"]))
        else:
            store.conn.execute(
                "UPDATE fills SET origin = ?, link_confidence = ? WHERE exec_id = ? "
                "AND origin IS NULL", (origin, how, f["exec_id"]))
            if origin == "unattributed":
                store.record_ledger_exception(
                    kind="unattributed_fill", subject_key=f"fill:{f['exec_id']}",
                    symbol=f["symbol"],
                    basis="statement row before the manual-classification cutoff; "
                          "no linking evidence")
    store.conn.commit()
    return summary
