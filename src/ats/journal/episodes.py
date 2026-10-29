"""Build trade episodes from fills: one round trip, net position 0 → nonzero → 0.

`build_episodes` is a pure function — no I/O, no wall clock — so it is fully testable
against a list of fill dicts. This is deliberate: the acceptance bar for this stage is
"per-symbol realized P&L matches the IBKR statement to the cent", and a reducer that
depends on anything beyond the fills themselves cannot be trusted to reproduce that
statement deterministically on a rerun.

Scope for this stage: episode IDENTITY and FINANCIAL FACTS (realized P&L, avg
entry/exit, commission, origin, opened/closed timestamps). Deliberately NOT here —
next stage, because they need price history or a live mark, not just fills:
`holding_days`, `r_multiple`, `mae_pct`/`mfe_pct`, `exit_reason`,
`invalidation_triggered`, `horizon_overdue_days`. Left `None` until then.

No `episode_legs` table: a leg is just a fill tagged with which episode/entry it
belongs to, so `fills.episode_id` / `fills.entry_id` carry that — a second table would
duplicate what `fills` already records (Occam).

## The pre-tracking seed

Fills alone cannot tell the whole story: the very first fill this system ever recorded
for a name may be a TRIM of a position that existed before tracking began (measured:
GOOG's only recorded fill is a single SLD of 13 shares — with no prior BUY, a naive
reducer would read that as opening a SHORT position, which is simply wrong; GOOG is a
long holding). If a live portfolio snapshot is available, the shortfall between the
currently-held quantity and the net implied by fills alone is injected as ONE synthetic
opening leg, dated just before the earliest real fill, tagged `origin="pre_tracking"`
and priced at the broker's own average cost. This is a bulk, clearly-labelled
approximation of "whatever existed before we could observe it" — not a reconstruction
of the specific missing fills, which the trade journal plan explicitly rules out.

## One episode stream per contract

Episodes reduce per INSTRUMENT (see "Instrument identity" below), not per symbol: an AAPL
call and AAPL stock are separate positions with separate round trips. A stock's
instrument key is its symbol, so stock episode ids are what they always were.
Option prices stay per share (as IBKR reports executions); `multiplier` says how
many shares one contract is. An option that has passed its expiry, is no longer
held, and has no closing fill is closed by a synthetic zero-price leg at the
expiry close (`exit_reason="expired"`): expirations produce no execution, so
without it the episode would stay open forever. Like the seed, that leg is never
persisted as a fill and carries no realized P&L.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from ..schemas.journal import EpisodeOrigin, TradeEpisode

log = logging.getLogger("ats.journal")

_SEED_PREFIX = "seed:"


# --------------------------------------------------------------------------- #
# Instrument identity
#
# Which contract a fill or a position is in.
#
# IBKR names an option by its underlying (`contract.symbol == "AAPL"` for an AAPL
# call), so a ledger keyed by symbol alone folds a short put into the stock
# position. The instrument key keeps them apart: a stock keys by its symbol (so
# stock episode ids are unchanged), an option by its OCC code — the same
# `AAPL  270319C00300000` the broker statement prints in its 总数 lines.
#
# FX conversions (`secType=CASH`) are not instruments here: they are currency
# moves between markets, not positions (Boss ruling 2026-10-29), and are never
# recorded.
# --------------------------------------------------------------------------- #
NOT_RECORDED = frozenset({"CASH"})
UNKNOWN_SUFFIX = " OPT?"     # an option whose contract the record no longer names


def occ_key(root: str, expiry: str, right: str, strike: float) -> str:
    """`expiry` is YYYYMMDD (IBKR lastTradeDateOrContractMonth)."""
    return f"{root:<6}{expiry[2:8]}{right.upper()[:1]}{round(float(strike) * 1000):08d}"


def unknown_option_key(root: str) -> str:
    return f"{root}{UNKNOWN_SUFFIX}"


def is_unknown(key: str) -> bool:
    return key.endswith(UNKNOWN_SUFFIX)


def fill_key(fill: dict) -> str:
    return fill.get("instrument_key") or fill.get("symbol") or ""


def position_key(pos) -> str:
    if getattr(pos, "sec_type", "STK") == "OPT" and pos.expiry and pos.right and pos.strike:
        return occ_key(pos.symbol, pos.expiry, pos.right, pos.strike)
    return pos.symbol


def contract_fields(contract) -> dict:
    """The ledger's contract columns from an ib_insync Contract."""
    sec_type = getattr(contract, "secType", "") or "STK"
    out = {"sec_type": sec_type, "con_id": str(getattr(contract, "conId", "") or ""),
           "local_symbol": getattr(contract, "localSymbol", "") or "",
           "currency": getattr(contract, "currency", "") or "",
           "multiplier": 1.0, "right": None, "strike": None, "expiry": None,
           "instrument_key": contract.symbol}
    if sec_type == "OPT":
        expiry = getattr(contract, "lastTradeDateOrContractMonth", "") or ""
        right = (getattr(contract, "right", "") or "")[:1].upper()
        strike = float(getattr(contract, "strike", 0) or 0)
        out.update(multiplier=float(getattr(contract, "multiplier", 0) or 0) or 100.0,
                   right=right or None, strike=strike or None, expiry=expiry or None,
                   instrument_key=(occ_key(contract.symbol, expiry, right, strike)
                                   if expiry and right and strike
                                   else unknown_option_key(contract.symbol)))
    return out


def _is_buy(side: str) -> bool:
    return (side or "").upper() in ("BOT", "BUY", "BUY_TO_OPEN", "BUY_TO_COVER")


def _parse_time(raw: str) -> datetime:
    dt = datetime.fromisoformat((raw or "").replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _net_signed(fills: list[dict]) -> float:
    return sum((f["shares"] if _is_buy(f.get("side", "")) else -f["shares"])
              for f in fills if f.get("shares"))


def _seed_fill(symbol: str, implied_qty: float, avg_cost: float, before: datetime) -> dict:
    """A synthetic opening leg representing the position that existed before our
    earliest real fill. `exec_id` carries the `seed:` prefix so downstream code can
    tell it apart from a real execution and never persist it as one. `symbol` here
    is the instrument key."""
    return {
        "exec_id": f"{_SEED_PREFIX}{symbol}", "symbol": symbol,
        "side": "BOT" if implied_qty > 0 else "SLD", "shares": abs(implied_qty),
        "price": avg_cost, "time": (before - timedelta(seconds=1)).isoformat(),
        "realized_pnl": None, "commission": 0.0, "origin": "pre_tracking", "entry_id": None,
    }


def _expiry_fill(key: str, net: float, expiry: str) -> dict:
    """The zero-price close of an option that expired with no closing fill."""
    at = datetime(int(expiry[:4]), int(expiry[4:6]), int(expiry[6:8]), 21, tzinfo=timezone.utc)
    return {
        "exec_id": f"{_SEED_PREFIX}expiry:{key}", "symbol": key,
        "side": "SLD" if net > 0 else "BOT", "shares": abs(net), "price": 0.0,
        "time": at.isoformat(), "realized_pnl": None, "commission": 0.0,
        "origin": None, "entry_id": None, "expired": True,
    }


class _Accumulator:
    """The episode currently being built, before it is finalized."""

    def __init__(self, symbol: str, direction: str, opened_at: datetime,
                opening_exec_id: str, contract: dict | None = None):
        self.symbol = symbol
        self.contract = contract or {}
        self.expired = False
        self.direction = direction              # "long" | "short"
        self.opened_at = opened_at
        # The episode id is DERIVED from the fill that opened it, not a random UUID —
        # a rerun of the reducer over the same fills must produce the same id, or
        # save_episode's upsert creates a duplicate row instead of replacing one.
        self.opening_exec_id = opening_exec_id
        self.closed_at: datetime | None = None
        self.entry_notional = 0.0
        self.entry_qty = 0.0
        self.exit_notional = 0.0
        self.exit_qty = 0.0
        self.realized_pnl = 0.0
        self.has_realized = False
        self.commission = 0.0
        self.origins: set[str] = set()
        self.primary_entry_id: str = ""

    def add_leg(self, fill: dict, role: str, qty: float, entry_id: str | None) -> None:
        price = float(fill.get("price") or 0.0)
        if role in ("open", "add"):
            self.entry_notional += price * qty
            self.entry_qty += qty
        else:  # trim | close
            self.exit_notional += price * qty
            self.exit_qty += qty
        rp = fill.get("realized_pnl")
        if isinstance(rp, (int, float)):
            self.realized_pnl += float(rp)
            self.has_realized = True
        self.commission += float(fill.get("commission") or 0.0)
        if fill.get("expired"):
            self.expired = True     # a synthetic close says nothing about who traded
            return
        origin = fill.get("origin") or "manual"   # no positive evidence -> assume manual
        self.origins.add(origin if origin in ("system", "manual", "pre_tracking") else "manual")
        # First leg with a REAL plan behind it, not necessarily the chronological
        # opening leg: when a pre-tracking seed opens the episode it never carries an
        # entry_id (it is synthetic), so if a later add-on leg has one — a real
        # Chief-driven buy on top of an inherited position — that IS the plan worth
        # attaching, and must not be shadowed by the seed going first.
        if entry_id and not self.primary_entry_id:
            self.primary_entry_id = entry_id

    @property
    def origin(self) -> EpisodeOrigin:
        if len(self.origins) == 1:
            return next(iter(self.origins))  # type: ignore[return-value]
        return "mixed"

    def finalize(self, *, status: str) -> TradeEpisode:
        avg_entry = self.entry_notional / self.entry_qty if self.entry_qty else None
        avg_exit = self.exit_notional / self.exit_qty if self.exit_qty else None
        # Any pre-tracking leg makes the entry basis an approximation (the broker's
        # blended average cost), even if later legs were fully observed.
        basis = "ibkr_avg_cost" if "pre_tracking" in self.origins else "observed_fills"
        key = self.contract.get("instrument_key") or self.symbol
        return TradeEpisode(
            episode_id=f"{key}:{self.opening_exec_id}",
            symbol=self.symbol, direction=self.direction, origin=self.origin,
            instrument_key=key if key != self.symbol else "",
            sec_type=self.contract.get("sec_type") or "STK",
            multiplier=float(self.contract.get("multiplier") or 1.0),
            exit_reason="expired" if self.expired and status == "closed" else None,
            status=status, opened_at=self.opened_at, closed_at=self.closed_at,
            avg_entry=avg_entry, avg_exit=avg_exit,
            realized_pnl=(round(self.realized_pnl, 2) if self.has_realized else None),
            commission=round(self.commission, 2) if self.commission else None,
            basis_source=basis,
            setup="unknown", primary_entry_id=self.primary_entry_id,
        )


def build_episodes(symbol: str, fills: list[dict], *,
                   contract: dict | None = None) -> list[TradeEpisode]:
    """Reduce one instrument's fills (any order) into episodes. `contract` carries
    instrument_key / sec_type / multiplier for a non-stock instrument.

    Handles the edge case of a single fill flipping net position through zero
    (e.g. selling more than currently held, flipping long to short) by splitting
    it: the portion that closes the old episode, and the portion that opens the
    new one, are both attributed their proportional share of commission/realized
    P&L.
    """
    ordered = sorted(fills, key=lambda f: _parse_time(f.get("time", "")))
    net = 0.0
    cur: _Accumulator | None = None
    out: list[TradeEpisode] = []

    for f in ordered:
        shares = float(f.get("shares") or 0.0)
        if shares <= 0:
            continue
        signed = shares if _is_buy(f.get("side", "")) else -shares
        entry_id = f.get("entry_id") or None
        remaining = signed
        total = abs(signed) or 1.0

        while remaining != 0:
            if cur is None:
                direction = "long" if remaining > 0 else "short"
                cur = _Accumulator(symbol, direction, _parse_time(f["time"]),
                                  f.get("exec_id", ""), contract)
                qty = abs(remaining)
                cur.add_leg(_proportional_leg(f, qty / total), "open", qty, entry_id)
                net += remaining
                remaining = 0.0
                continue

            same_direction = (cur.direction == "long" and remaining > 0) or \
                             (cur.direction == "short" and remaining < 0)
            if same_direction:
                qty = abs(remaining)
                cur.add_leg(_proportional_leg(f, qty / total), "add", qty, entry_id)
                net += remaining
                remaining = 0.0
            else:
                held = abs(net)
                reduce_qty = min(abs(remaining), held)
                reduce_signed = reduce_qty if remaining > 0 else -reduce_qty
                role = "close" if reduce_qty >= held else "trim"
                cur.add_leg(_proportional_leg(f, reduce_qty / total), role, reduce_qty, entry_id)
                net += reduce_signed
                remaining -= reduce_signed
                if abs(net) < 1e-9:
                    cur.closed_at = _parse_time(f["time"])
                    out.append(cur.finalize(status="closed"))
                    cur = None

    if cur is not None:
        out.append(cur.finalize(status="open"))
    return out


def _proportional_leg(fill: dict, frac: float) -> dict:
    """A fill split across two episodes (the zero-crossing case) carries its price
    unchanged but its commission and realized P&L pro-rated by share count."""
    if frac >= 1.0:
        return fill
    out = dict(fill)
    for k in ("commission", "realized_pnl"):
        if isinstance(fill.get(k), (int, float)):
            out[k] = fill[k] * frac
    return out


def seed_pre_tracking(symbol: str, qty: float, avg_cost: float,
                      market_price: float, as_of: date, *,
                      contract: dict | None = None) -> TradeEpisode:
    """A standalone open episode for a position with ZERO recorded fills at all —
    held since before this system ever observed an execution for it.

    Distinct from the mid-`build_episodes` seed leg (`_seed_fill`), which handles the
    more common case of a position that has SOME observed fills plus an unexplained
    remainder.
    """
    direction = "long" if qty >= 0 else "short"
    contract = contract or {}
    key = contract.get("instrument_key") or symbol
    mult = float(contract.get("multiplier") or 1.0)
    return TradeEpisode(
        # Deterministic and stable: there is at most one standalone (zero-fills)
        # pre-tracking episode per instrument at a time, so a fixed suffix is enough
        # for a rerun to replace rather than duplicate it.
        episode_id=f"{key}:pre_tracking", symbol=symbol, direction=direction,
        instrument_key=key if key != symbol else "",
        sec_type=contract.get("sec_type") or "STK", multiplier=mult,
        origin="pre_tracking", status="open",
        opened_at=_combine_utc(as_of),
        avg_entry=avg_cost, basis_source="ibkr_avg_cost",
        unrealized_pnl=round((market_price - avg_cost) * qty * mult, 2) if qty else None,
        setup="unknown",
    )


def _combine_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _held_qty(portfolio, key: str) -> float | None:
    pos = _position(portfolio, key)
    return pos.qty if pos else 0.0


def _position(portfolio, key: str):
    for p in getattr(portfolio, "positions", None) or []:
        if position_key(p) == key:
            return p
    return None


def _per_share_cost(pos) -> float:
    """IBKR's averageCost is per CONTRACT for an option (price x multiplier)."""
    if getattr(pos, "sec_type", "STK") == "OPT":
        return pos.avg_cost / (pos.multiplier or 100.0)
    return pos.avg_cost


def _expired(expiry: str | None, today: date) -> bool:
    try:
        return bool(expiry) and date(int(expiry[:4]), int(expiry[4:6]),
                                     int(expiry[6:8])) < today
    except ValueError:
        return False


def rebuild_all(*, store=None, portfolio=None) -> dict:
    """Rebuild episodes for every symbol with fills, seeding the pre-tracking
    remainder where a live portfolio snapshot shows more (or less) held than the
    fills alone explain.

    `portfolio`, if given, is a PortfolioSnapshot already fetched by the caller — this
    function never calls the broker itself, so it stays cheap enough to run every
    session and easy to test with a canned snapshot.
    """
    from ..memory import get_store

    store = store or get_store()
    summary = {"symbols": 0, "episodes": 0, "seeded": 0, "expired": 0}
    instruments = store.instruments_with_fills()
    seen = {key for key, *_ in instruments}
    today = datetime.now(timezone.utc).date()

    for key, symbol, sec_type, mult in instruments:
        fills = store.fills_for_instrument(key)
        all_fills = list(fills)
        expiry = next((f.get("expiry") for f in fills if f.get("expiry")), None)
        contract = {"instrument_key": key, "sec_type": sec_type, "multiplier": mult}
        if portfolio is not None:
            held = _held_qty(portfolio, key)
            if (sec_type == "OPT" and not held and _expired(expiry, today)
                    and abs(_net_signed(fills)) > 1e-6):
                all_fills.append(_expiry_fill(key, _net_signed(fills), expiry))
                summary["expired"] += 1
            implied_prior = held - _net_signed(all_fills)
            if abs(implied_prior) > 1e-6:
                pos = _position(portfolio, key)
                # `held == 0` (fully closed by the fills we DO have, e.g. a name
                # bought before tracking began and later sold entirely) means there
                # is no live position to read a cost basis from. Fall back to the
                # earliest observed fill's own price — realized_pnl is unaffected
                # either way, since it always sums the fills' own realizedPNL, never
                # derived from this cost estimate; only the seed leg's cosmetic
                # avg_entry is approximate here.
                cost = _per_share_cost(pos) if pos else min(fills, key=lambda f: _parse_time(
                    f.get("time", "")))["price"]
                earliest = min(_parse_time(f["time"]) for f in fills)
                all_fills = [_seed_fill(key, implied_prior, cost, earliest)] + all_fills
                summary["seeded"] += 1

        episodes = build_episodes(symbol, all_fills, contract=contract)
        for i, ep in enumerate(episodes):
            if ep.primary_entry_id:
                entry = store.get_journal_entry(ep.primary_entry_id)
                if entry:
                    episodes[i] = ep.model_copy(update={"setup": entry.setup})

        for ep in episodes:
            store.save_episode(ep)
        # Episode ids derive from the opening leg, so a new seed or a backfilled
        # earlier fill re-keys them; save_episode only upserts, and the superseded
        # rows would linger as phantom open positions.
        summary["superseded"] = summary.get("superseded", 0) + _drop_superseded(
            store, key, {ep.episode_id for ep in episodes})
        _assign_fill_episode_ids(store, all_fills, episodes)
        summary["symbols"] += 1
        summary["episodes"] += len(episodes)

    # Positions held with ZERO recorded fills at all — never observed, so they never
    # appear in instruments_with_fills() and need their own standalone seed.
    if portfolio is not None:
        held_keys = set()
        for pos in portfolio.positions:
            key = position_key(pos)
            if pos.qty:
                held_keys.add(key)
            if key in seen or not pos.qty or pos.sec_type in NOT_RECORDED:
                continue
            store.save_episode(seed_pre_tracking(
                pos.symbol, pos.qty, _per_share_cost(pos), pos.market_price,
                datetime.now(timezone.utc).date(),
                contract={"instrument_key": key, "sec_type": pos.sec_type,
                          "multiplier": pos.multiplier or 1.0}))
            summary["seeded"] += 1
            summary["episodes"] += 1
        # A standalone seed for a name since sold off-system and never seen filled.
        for (key,) in store.conn.execute(
                "SELECT DISTINCT COALESCE(NULLIF(instrument_key, ''), symbol) "
                "FROM trade_episodes").fetchall():
            if key not in seen and key not in held_keys:
                summary["superseded"] = summary.get("superseded", 0) + _drop_superseded(
                    store, key, set())

    store.set_meta("last_episode_rebuild_at", datetime.now(timezone.utc).isoformat())
    return summary


def _drop_superseded(store, key: str, keep: set[str]) -> int:
    rows = [r[0] for r in store.conn.execute(
        "SELECT episode_id FROM trade_episodes "
        "WHERE COALESCE(NULLIF(instrument_key, ''), symbol) = ?", (key,)).fetchall()
        if r[0] not in keep]
    store.conn.executemany("DELETE FROM trade_episodes WHERE episode_id = ?",
                           [(r,) for r in rows])
    return len(rows)


def _assign_fill_episode_ids(store, all_fills: list[dict],
                             episodes: list[TradeEpisode]) -> None:
    """Re-walk the same reduction to know which episode each REAL fill landed in, and
    write `fills.episode_id`. The synthetic seed leg (if any) is walked for state but
    never persisted — it has no row in `fills`. Kept separate from `build_episodes` so
    that function stays a pure list-in/list-out reducer with no store dependency."""
    ordered = sorted(all_fills, key=lambda f: _parse_time(f.get("time", "")))
    ep_iter = iter(e.episode_id for e in episodes)
    net = 0.0
    cur_id: str | None = None
    for f in ordered:
        shares = float(f.get("shares") or 0.0)
        if shares <= 0:
            continue
        signed = shares if _is_buy(f.get("side", "")) else -shares
        if cur_id is None or net == 0:
            cur_id = next(ep_iter, cur_id)
        net += signed
        if not str(f.get("exec_id", "")).startswith(_SEED_PREFIX):
            store.conn.execute("UPDATE fills SET episode_id = ? WHERE exec_id = ?",
                              (cur_id, f["exec_id"]))
        if abs(net) < 1e-9:
            net = 0.0
            cur_id = None
    store.conn.commit()


def run() -> int:
    from ..trader.execute import IBKRBroker

    portfolio = None
    try:
        portfolio = IBKRBroker().get_portfolio()
    except Exception as exc:  # noqa: BLE001 - degrade to fills-only rebuild
        log.warning("episode rebuild: no live portfolio (%s); skipping pre_tracking seed", exc)

    s = rebuild_all(portfolio=portfolio)
    print(f"回合重建：{s['symbols']} 个标的 → {s['episodes']} 个回合"
          + (f"（含 {s['seeded']} 个存量持仓种子）" if s["seeded"] else ""))
    return 0
