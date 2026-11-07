"""The risk role's reads, all through the governed read API (switch-trade r5 task 6.4).

Risk judges the account, so its business scope is the book it is judging: the held symbols (and their
economic risk symbols and option underlyings), taken from the live account read. Not the configured watch
list — a holding outside that list would otherwise be refused its beta and price, and the review would
quietly change.

Every read here is a `read_input("risk", ...)`: the contract is checked, the entity is checked against the
bound book, a replay gets the frozen input, and the read is audited with its source time and payload hash.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Iterable

RISK = "risk"


def _risk_context():
    from ..workflow.runtime_reads import current_read_context

    current = current_read_context()
    return current if current and current.identity.consumer_id == RISK else None


def _bound_entities(context) -> set[str]:
    scope = (context.invocation["actual_identity"]["scope"] if context.invocation
             else context.identity.scope)
    return set(scope.get("entities") or [])


def _outer_as_of(context):
    """The business time of the run that called risk, if any.

    Risk reviews the run's own moment: a review nested in the Chief's run takes the Chief's point in time,
    so a replay of that run reproduces it; reading the clock here would be an input the run never froze.
    Only a risk call outside any run (the CLI) takes the current time.
    """
    if context is None:
        return None
    scope = (context.invocation["actual_identity"]["scope"] if context.invocation
             else context.identity.scope)
    return ((scope or {}).get("time_range") or {}).get("end") or None


@contextmanager
def _bound(entities: Iterable[str] = ()):
    """Run inside a risk business scope covering `entities`.

    Reuses the current risk binding when it already covers them; otherwise binds the risk role's own
    portfolio scope for exactly these entities — also when called from inside another role's run (the
    Chief records the posture on its own snapshot), since each role reads under its own authority.
    """
    wanted = sorted({str(e) for e in entities if e})
    context = _risk_context()
    if context is not None and set(wanted) <= _bound_entities(context):
        yield context
        return
    from ..workflow.runtime_reads import (bind_read, business_identity, consumer_entities,
                                          current_read_context)

    # A read that names no entity (rules, the account, the NAV history) binds the role's own portfolio
    # scope, the same one its CLI entry binds; a read for symbols binds exactly those symbols.
    identity = business_identity(RISK, kind="portfolio", scope_id="portfolio",
                                 entities=wanted or consumer_entities(RISK, "portfolio", "portfolio"),
                                 as_of=_outer_as_of(current_read_context()))
    with bind_read(identity) as bound:
        yield bound


def book_entities(portfolio, registry=None) -> list[str]:
    """Every symbol a review of this book may read for: held symbols, their risk symbols, option underlyings."""
    registry = registry or rules().instruments
    out: set[str] = set()
    for p in portfolio.positions:
        for symbol in {p.symbol, getattr(p, "underlying", None) or p.symbol}:
            out.add(symbol)
            out.add(registry.resolve(symbol).risk_symbol)
    return sorted(out)


@contextmanager
def book(portfolio, registry=None):
    """Bind the risk scope to this book for the reads a review of it makes."""
    with _bound(book_entities(portfolio, registry)):
        yield


def _in_book(symbols: Iterable[str]) -> None:
    """A per-symbol read is made for a book under review, never on its own: the symbol must be in it."""
    context = _risk_context()
    if context is None:
        raise PermissionError("risk_symbol_read_outside_book")
    outside = set(symbols) - _bound_entities(context)
    if outside:
        raise PermissionError("risk_symbol_read_outside_book:" + ",".join(sorted(outside)))


# --------------------------------------------------------------------------- #
# RISK_RULES
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Rules:
    limits: object       # config.app.risk
    policy: object       # RiskPolicy
    instruments: object  # InstrumentRiskRegistry


def rules() -> Rules:
    from ..config import RiskConfig
    from ..data.consumer_api import read_input
    from ..schemas.instruments import InstrumentRiskRegistry
    from ..schemas.risk import RiskPolicy

    with _bound():
        packet = read_input(RISK, "RISK_RULES", scope={})
    if packet.status != "complete" or not packet.payload:
        raise RuntimeError("risk_rules_unavailable:" + ";".join(packet.gaps))
    payload = packet.payload
    return Rules(limits=RiskConfig.model_validate(payload["limits"]),
                 policy=RiskPolicy.model_validate(payload["policy"]),
                 instruments=InstrumentRiskRegistry.model_validate(payload["instruments"]))


# --------------------------------------------------------------------------- #
# PORTFOLIO_DATA / HISTORY_DATA
# --------------------------------------------------------------------------- #
def account():
    """The live book, or None when the broker cannot be read (the caller decides what that means)."""
    from ..data.consumer_api import read_input
    from ..schemas.portfolio import PortfolioSnapshot
    from ..broker.account import account_broker

    with _bound():
        packet = read_input(RISK, "PORTFOLIO_DATA", scope={"kind": "portfolio"}, broker=account_broker())
    return PortfolioSnapshot.model_validate(packet.payload) if packet.payload else None


def performance_history(*, limit: int = 250):
    """(records, completeness) for the drawdown layer."""
    from ..data.consumer_api import read_input
    from ..memory import get_store
    from ..schemas.memory import PerformanceRecord

    with _bound():
        packet = read_input(RISK, "HISTORY_DATA", scope={"kind": "performance_history", "limit": limit},
                            store=get_store())
    if not packet.payload:
        raise RuntimeError("performance_history_unavailable:" + ";".join(packet.gaps))
    return ([PerformanceRecord.model_validate(r) for r in packet.payload["records"]],
            SimpleNamespace(**packet.payload["completeness"]))


# --------------------------------------------------------------------------- #
# MARKET_DATA
# --------------------------------------------------------------------------- #
def beta(symbol: str) -> tuple[float | None, bool]:
    """(beta, fetched) — `fetched` is whether the read went to the network."""
    from ..data.consumer_api import read_input

    _in_book([symbol])
    packet = read_input(RISK, "MARKET_DATA", scope={"kind": "beta", "entity": symbol})
    payload = packet.payload or {}
    return payload.get("beta"), bool(payload.get("fetched"))


def close_history(symbols: list[str]) -> dict[str, list[float]]:
    """{symbol: closes} for the symbols that have them (one year of daily closes)."""
    from ..data.consumer_api import read_input

    if not symbols:
        return {}
    _in_book(symbols)
    packet = read_input(RISK, "MARKET_DATA", scope={"kind": "close_history", "entities": list(symbols)})
    return {symbol: list(row["closes"]) for symbol, row in (packet.payload or {}).items()
            if row.get("status") == "succeeded" and row.get("closes")}


def atm_iv(symbol: str) -> float | None:
    """The option chain's at-the-money IV, in percent."""
    from ..data.consumer_api import read_input

    _in_book([symbol])
    packet = read_input(RISK, "MARKET_DATA", scope={"kind": "options", "entity": symbol})
    return (packet.payload or {}).get("atm_iv")
