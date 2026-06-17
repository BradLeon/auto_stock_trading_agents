"""Fundamental data contract (yfinance metrics + SEC filings), for the analyst."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class Filing(BaseModel):
    form: str          # 10-K, 10-Q, 8-K, ...
    filed: date
    title: str = ""
    url: str = ""


class StatementMetric(BaseModel):
    """One financial-statement line with QoQ / YoY change."""

    label: str
    value: float | None = None
    qoq: float | None = None        # % for $ metrics, percentage-points for margins
    yoy: float | None = None
    unit: str = ""                  # "$M" | "%" | "$"
    delta_unit: str = "%"           # "%" | "pp"

    def render(self) -> str:
        def fmt(v, u):
            return "n/a" if v is None else (f"{v:,.0f}{u}" if u == "$M"
                                            else f"{v:.2f}{u}" if u in ("%", "$") else f"{v}{u}")

        def dlt(v):
            return "n/a" if v is None else f"{v:+.1f}{self.delta_unit}"

        return f"{self.label}: {fmt(self.value, self.unit)} (QoQ {dlt(self.qoq)}, YoY {dlt(self.yoy)})"


class FinancialStatements(BaseModel):
    period: str = ""                # latest quarter end, e.g. 2026-03-31
    lines: list[StatementMetric] = Field(default_factory=list)


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
    statements: FinancialStatements | None = None
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
        if self.statements and self.statements.lines:
            lines.append(f"Quarterly statements (latest {self.statements.period}):")
            lines += [f"  {m.render()}" for m in self.statements.lines]
        if self.recent_filings:
            lines.append("Recent SEC filings: " +
                         ", ".join(f"{x.form} {x.filed}" for x in self.recent_filings[:5]))
        if self.notes:
            lines.append("Notes: " + "; ".join(self.notes))
        return "\n".join(lines)
