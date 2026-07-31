"""Technical analyst — deterministic timing/exposure readings.

Produces a suggested RISK EXPOSURE per symbol. It is an analyst: it never emits
an order, a quantity or a notional. Only the Chief turns anything here into a
TradeDecision (docs/DESIGN.md §4).
"""
