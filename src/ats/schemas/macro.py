"""Macro data contract (FRED + market indices), consumed by the macro analyst."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MacroData(BaseModel):
    as_of: datetime
    # Rates (%)
    ust_10y: float | None = None
    ust_2y: float | None = None
    fed_funds: float | None = None
    real_10y: float | None = Field(None, description="10y TIPS real yield, %")
    # Inflation / employment / growth
    cpi_yoy: float | None = Field(None, description="headline CPI year-over-year, %")
    pce_yoy: float | None = Field(None, description="core PCE year-over-year, %")
    breakeven_10y: float | None = Field(None, description="10y breakeven inflation, %")
    unemployment: float | None = None
    nfp_change_k: float | None = Field(None, description="latest non-farm payrolls change, thousands")
    jobless_claims_k: float | None = Field(None, description="initial jobless claims, thousands")
    cfnai: float | None = Field(None, description="Chicago Fed National Activity Index (growth)")
    # Financial conditions (credit spreads, %)
    hy_oas: float | None = Field(None, description="ICE BofA US High Yield OAS, %")
    ig_oas: float | None = Field(None, description="ICE BofA US IG Corporate OAS, %")
    # Commodities / dollar
    oil_wti: float | None = None
    gold: float | None = None
    dxy: float | None = None
    # Market regime
    vix: float | None = None
    spx: float | None = None
    spx_chg_pct: float | None = None
    ndx: float | None = None
    ndx_chg_pct: float | None = None
    fear_greed: int | None = Field(None, ge=0, le=100)
    notes: list[str] = Field(default_factory=list, description="which feeds were unavailable")

    def to_context(self) -> str:
        def f(v, suf=""):
            return f"{v:.2f}{suf}" if isinstance(v, (int, float)) else "n/a"
        curve = (self.ust_10y - self.ust_2y) if (self.ust_10y and self.ust_2y) else None
        lines = [
            f"Rates: UST10Y {f(self.ust_10y, '%')}, UST2Y {f(self.ust_2y, '%')}, "
            f"FedFunds {f(self.fed_funds, '%')}, 10y-2y {f(curve, '%') if curve is not None else 'n/a'}, "
            f"real10y(TIPS) {f(self.real_10y, '%')}",
            f"Inflation/Jobs/Growth: CPI YoY {f(self.cpi_yoy, '%')}, core PCE YoY {f(self.pce_yoy, '%')}, "
            f"10y breakeven {f(self.breakeven_10y, '%')}, Unemployment {f(self.unemployment, '%')}, "
            f"NFP chg {f(self.nfp_change_k, 'k')}, jobless claims {f(self.jobless_claims_k, 'k')}, "
            f"CFNAI {f(self.cfnai)}",
            f"Financial conditions: HY OAS {f(self.hy_oas, '%')}, IG OAS {f(self.ig_oas, '%')} "
            f"(spread widening = risk-off)",
            f"Commodities/USD: WTI ${f(self.oil_wti)}, Gold ${f(self.gold)}, DXY {f(self.dxy)}",
            f"Regime: VIX {f(self.vix)}, SPX {f(self.spx)} ({f(self.spx_chg_pct, '%')}), "
            f"NDX {f(self.ndx)} ({f(self.ndx_chg_pct, '%')}), Fear&Greed {self.fear_greed or 'n/a'}",
        ]
        if self.notes:
            lines.append("Unavailable feeds: " + ", ".join(self.notes))
        return "\n".join(lines)
