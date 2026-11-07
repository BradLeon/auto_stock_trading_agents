"""Fill currency → USD (Clerk report §4, performance).

A fill's price, realized P&L and commission are in the CONTRACT's own currency (HKD for 7709, EUR for HY9H):
that is what TWS and the Flex statement record, and what the episode reducer and the to-the-cent reconciliation
work in, so the ledger keeps those numbers untouched. What is added is the rate, `fills.fx_to_usd` (USD per one
unit of the contract currency) with `fills.fx_source` saying where it came from:

- `usd`          the contract is in USD (rate 1)
- `flex`         the statement's own per-trade rate (`fxRateToBase`), exact for that trade
- `ibkr_live`    the account's ExchangeRate when the fill was captured: an estimate, up to a day off the trade
- `estimate_now` a historical row filled in with today's ExchangeRate: an estimate, flagged as such
- `missing`      no rate known. Never read as USD: callers show the native amount and say the rate is missing.

USD figures are always native × `fx_to_usd`; they are not stored a second time.
"""

from __future__ import annotations

from typing import Any

USD = "USD"
SYMBOL = {"USD": "$", "HKD": "HK$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNH": "CN¥", "TWD": "NT$"}
ESTIMATED = {"ibkr_live", "estimate_now"}


def usd_rate(currency: str, base_currency: str, rates: dict[str, float]) -> float | None:
    """USD per one unit of `currency`, from account rates quoted as base-currency units per one unit.

    None when it cannot be worked out — a missing rate must stay visible, not become 1.0.
    """
    currency, base = (currency or "").upper(), (base_currency or USD).upper()
    if currency == USD:
        return 1.0
    rate = rates.get(currency)
    if rate is None or rate <= 0:
        return None
    if base == USD:
        return float(rate)
    usd_in_base = rates.get(USD)
    return float(rate) / float(usd_in_base) if usd_in_base and usd_in_base > 0 else None


def resolve(fill: dict) -> tuple[float | None, str]:
    """The (rate, source) to store for a fill: what it carries, else USD ⇒ 1, else missing (never a guess of 1)."""
    rate, source = fill.get("fx_to_usd"), fill.get("fx_source")
    if isinstance(rate, (int, float)) and rate > 0:
        return float(rate), source or "unknown"
    currency = (fill.get("currency") or "").upper()
    if currency == USD:
        return 1.0, "usd"
    return None, "missing" if currency else "currency_unknown"


def usd_value(amount: float | None, fill: dict) -> float | None:
    rate, _ = resolve(fill)
    return None if amount is None or rate is None else amount * rate


def money(amount: float, fill: dict, *, signed: bool = False, digits: int = 0) -> str:
    """`$1,234` for USD; `HK$28,500（≈ $3,650）` for a known rate; `HKD 28,500（汇率缺失）` when it is not known."""
    currency = (fill.get("currency") or "").upper()
    rate, source = resolve(fill)

    def render(symbol: str, value: float) -> str:
        sign = "+" if signed and value >= 0 else ("-" if value < 0 else "")
        return f"{sign}{symbol}{abs(value):,.{digits}f}"

    if currency == USD:
        return render("$", amount)
    if not currency:
        return f"币种未记 {amount:+,.{digits}f}" if signed else f"币种未记 {amount:,.{digits}f}"
    native = render(SYMBOL.get(currency, f"{currency} "), amount)
    if rate is None:
        return f"{native}（汇率缺失）"
    approx = "≈ " + render("$", amount * rate)
    return f"{native}（{approx}{'，估算汇率' if source in ESTIMATED else ''}）"
