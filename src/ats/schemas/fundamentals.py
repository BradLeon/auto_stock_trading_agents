"""Fundamental data contract (yfinance metrics + SEC filings), for the analyst."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class Filing(BaseModel):
    form: str          # 10-K, 10-Q, 8-K, ...
    filed: date
    title: str = ""
    url: str = ""


class FundamentalData(BaseModel):
    symbol: str
    as_of: datetime
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_sales: float | None = None
    profit_margin: float | None = Field(None, description="net margin, fraction")
    revenue_growth: float | None = Field(None, description="yoy, fraction")
    earnings_growth: float | None = Field(None, description="yoy, fraction")
    free_cashflow: float | None = None
    dividend_yield: float | None = None
    recent_filings: list[Filing] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def to_context(self) -> str:
        def pct(v):
            return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "n/a"

        def num(v):
            if not isinstance(v, (int, float)):
                return "n/a"
            for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
                if abs(v) >= div:
                    return f"{v / div:.1f}{unit}"
            return f"{v:.0f}"

        lines = [
            f"MktCap {num(self.market_cap)}, P/E {self.trailing_pe or 'n/a'} "
            f"(fwd {self.forward_pe or 'n/a'}), P/S {self.price_to_sales or 'n/a'}",
            f"Net margin {pct(self.profit_margin)}, Rev growth {pct(self.revenue_growth)}, "
            f"EPS growth {pct(self.earnings_growth)}, FCF {num(self.free_cashflow)}",
        ]
        if self.recent_filings:
            lines.append("Recent SEC filings: " +
                         ", ".join(f"{x.form} {x.filed}" for x in self.recent_filings[:5]))
        if self.notes:
            lines.append("Notes: " + "; ".join(self.notes))
        return "\n".join(lines)
