"""Instrument-level metadata used to translate securities into economic risks."""

from __future__ import annotations

from pydantic import BaseModel, Field


def normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace(" ", "-").replace(".", "-")


class InstrumentRiskMeta(BaseModel):
    economic_entity: str = ""
    label: str = ""
    risk_symbol: str = ""
    layer_symbol: str = ""
    exposure_multiplier: float = Field(1.0, gt=0)
    product_type: str = "stock"


class ResolvedInstrumentRisk(BaseModel):
    symbol: str
    economic_entity: str
    label: str
    risk_symbol: str
    layer_symbol: str
    exposure_multiplier: float = 1.0
    product_type: str = "stock"


class InstrumentRiskRegistry(BaseModel):
    instruments: dict[str, InstrumentRiskMeta] = Field(default_factory=dict)

    def resolve(self, symbol: str) -> ResolvedInstrumentRisk:
        upper = symbol.upper()
        norm = normalize_symbol(symbol)
        meta = self.instruments.get(upper) or self.instruments.get(symbol)
        if meta is None:
            meta = next(
                (value for key, value in self.instruments.items()
                 if normalize_symbol(key) == norm),
                InstrumentRiskMeta(),
            )
        entity = meta.economic_entity or norm
        risk_symbol = meta.risk_symbol or symbol
        return ResolvedInstrumentRisk(
            symbol=symbol,
            economic_entity=entity,
            label=meta.label or risk_symbol,
            risk_symbol=risk_symbol,
            layer_symbol=meta.layer_symbol or risk_symbol,
            exposure_multiplier=meta.exposure_multiplier,
            product_type=meta.product_type,
        )
