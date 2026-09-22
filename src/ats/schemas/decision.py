"""Trade decision and Boss approval contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Action = Literal["buy", "add", "hold", "trim", "sell"]
OrderType = Literal["market", "limit"]
TimeInForce = Literal["DAY", "GTC"]

# --- The action vocabulary is declared once, here ---------------------------- #
# Every other module — analyst recommendations, trade decisions, risk checks,
# broker mapping, journal entries — must reference these helpers rather than
# inlining its own tuple of values. A second copy drifts (PEAD's list used to be
# missing `add`) and the drift only shows up as a silently wrong order.
ACTIONS: tuple[Action, ...] = ("buy", "add", "hold", "trim", "sell")

# Direction of exposure change: +1 increases, -1 reduces, 0 no change.
ACTION_DIRECTION: dict[Action, int] = {
    "buy": 1, "add": 1, "hold": 0, "trim": -1, "sell": -1,
}

# Broker-side representation, derived from the canonical value by explicit mapping.
# `hold` produces no order — it is still listed so the mapping covers every value and
# an unknown value is an error rather than a default direction.
BROKER_SIDE_BY_ACTION: dict[Action, str | None] = {
    "buy": "BUY", "add": "BUY", "trim": "SELL", "sell": "SELL", "hold": None,
}


class UnknownActionError(ValueError):
    """An action value outside the declared vocabulary.

    Raised instead of silently treating the value as `hold`: an unknown action must be
    rejected and reported with its original value, never degraded into a neutral one.
    """

    def __init__(self, value: object, where: str = "") -> None:
        self.value = value
        self.where = where
        suffix = f" (at {where})" if where else ""
        super().__init__(
            f"unknown action {value!r}{suffix}; expected one of {', '.join(ACTIONS)}"
        )


def normalize_action(value: str, *, where: str = "") -> Action:
    """Fold an upstream value into the canonical lowercase form.

    This is the single normalization point: it runs once, before the value enters a
    domain object, so no layer downstream has to guess the case again.
    """
    if not isinstance(value, str):
        raise UnknownActionError(value, where)
    normalized = value.strip().lower()
    if normalized not in ACTIONS:
        raise UnknownActionError(value, where)
    return normalized  # type: ignore[return-value]


def action_direction(action: str, *, where: str = "") -> int:
    """+1 increases exposure, -1 reduces it, 0 leaves it unchanged."""
    return ACTION_DIRECTION[normalize_action(action, where=where)]


def is_increasing_action(action: str, *, where: str = "") -> bool:
    return action_direction(action, where=where) > 0


# Human-facing rendering, derived from the canonical value by explicit mapping.
# Display text is a one-way street: it must never be written back as an internal value
# nor used to validate one.
ACTION_DISPLAY: dict[Action, str] = {action: action.upper() for action in ACTIONS}


def display_action(action: str, *, where: str = "") -> str:
    """Render `action` for humans (reports, approval cards, CLI)."""
    return ACTION_DISPLAY[normalize_action(action, where=where)]


def broker_side(action: str, *, where: str = "") -> str | None:
    """Broker-side side for `action`; `None` when the action places no order.

    Unknown values raise — the mapper must never fall back to a default direction.
    """
    return BROKER_SIDE_BY_ACTION[normalize_action(action, where=where)]
# What kind of trade this is. Expectancy-per-setup is the single most useful thing a
# journal can aggregate, and inferring it later by regexing Chinese free-text rationale
# would be wrong often enough to poison the statistic — so the Chief states it.
Setup = Literal["pead_event", "risk_repair", "stop_loss", "sector_rotation",
                "macro_tilt", "manual", "boss_override", "unknown"]

# Class shares: LLMs/yfinance write BRK.B / BRK-B; IBKR (our canonical form) uses "BRK B".
_CLASS_SHARE_RE = re.compile(r"^([A-Z]+)[.\-]([A-Z])$")


class TradeDecision(BaseModel):
    """A single proposed action on one symbol, produced by the Manager."""

    symbol: str

    @field_validator("symbol")
    @classmethod
    def _broker_native_symbol(cls, v: str) -> str:
        return _CLASS_SHARE_RE.sub(r"\1 \2", v.strip().upper())
    action: Action

    @field_validator("action", mode="before")
    @classmethod
    def _canonical_action(cls, v: object) -> object:
        # The single normalization point: case and stray whitespace are folded here,
        # and anything outside the vocabulary is rejected rather than defaulted.
        return normalize_action(v, where="TradeDecision.action") if isinstance(v, str) else v
    target_weight: float | None = Field(None, ge=0, le=1, description="desired portfolio weight")
    qty: float | None = Field(None, description="absolute share delta; sign implied by action")
    notional_usd: float | None = Field(None, ge=0)
    order_type: OrderType = "limit"
    limit_price: float | None = None
    time_in_force: TimeInForce = "DAY"
    conviction: float = Field(0.0, ge=0, le=1)
    rationale: str = ""
    references: list[str] = Field(default_factory=list, description="report ids / sources cited")

    # --- pre-registered plan (journal) --------------------------------------
    # Declared intent, NOT resting orders: the approval gate means an order reaches
    # the broker hours after the decision, and a GTC stop left on a post-earnings
    # gapper guarantees the worst fill. These exist so the exit criterion is on record
    # BEFORE the outcome is known, which is what makes the review falsifiable.
    setup: Setup = "unknown"
    stop_price: float | None = Field(None, gt=0, description="declared invalidation price")
    target_price: float | None = Field(None, gt=0)
    planned_horizon_days: int | None = Field(None, gt=0, description="intended holding window")
    invalidation: str = Field("", description="what observation would falsify this thesis")


class BossApproval(BaseModel):
    """Human-in-the-loop verdict injected via interrupt resume."""

    status: Literal["approved", "rejected", "modified"]
    reviewer: str = ""
    reviewed_at: datetime | None = None
    comment: str = ""
    # Symbols the Boss approved / rejected; empty `approved` with status=approved means all.
    approved_symbols: list[str] = Field(default_factory=list)
    rejected_symbols: list[str] = Field(default_factory=list)
    # Optional decisions that fully replace the Manager's (status=modified).
    overrides: list[TradeDecision] = Field(default_factory=list)
    # Free-form instructions from the Boss straight to the Trader (bypass Manager).
    direct_instructions: list[TradeDecision] = Field(default_factory=list)

    def effective_decisions(self, proposed: list[TradeDecision]) -> list[TradeDecision]:
        """Resolve what the Trader should actually execute given this verdict."""
        if self.status == "rejected":
            return list(self.direct_instructions)
        if self.status == "modified" and self.overrides:
            return self.overrides + self.direct_instructions
        # approved: optionally filter to approved_symbols, drop rejected_symbols
        decisions = proposed
        if self.approved_symbols:
            decisions = [d for d in decisions if d.symbol in self.approved_symbols]
        if self.rejected_symbols:
            decisions = [d for d in decisions if d.symbol not in self.rejected_symbols]
        return decisions + self.direct_instructions
