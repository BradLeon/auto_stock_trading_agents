"""Pydantic data contracts shared across the system."""

from .channel import ApprovalRequest, Notification, ReportBundle
from .decision import Action, BossApproval, OrderType, TradeDecision
from .market import OHLCV, MarketSnapshot, Ticker
from .memory import PerformanceRecord, TradeLogEntry
from .portfolio import ExposureBreakdown, PortfolioSnapshot, Position
from .reports import (
    BaseReport,
    FundamentalReport,
    IndustryReport,
    MacroReport,
    Signal,
    TechnicalReport,
)
from .risk import RiskGuardrails

__all__ = [
    # market
    "Ticker",
    "OHLCV",
    "MarketSnapshot",
    # reports
    "BaseReport",
    "Signal",
    "MacroReport",
    "IndustryReport",
    "FundamentalReport",
    "TechnicalReport",
    # risk
    "RiskGuardrails",
    # decision
    "Action",
    "OrderType",
    "TradeDecision",
    "BossApproval",
    # portfolio
    "Position",
    "ExposureBreakdown",
    "PortfolioSnapshot",
    # memory
    "TradeLogEntry",
    "PerformanceRecord",
    # channel
    "Notification",
    "ApprovalRequest",
    "ReportBundle",
]
