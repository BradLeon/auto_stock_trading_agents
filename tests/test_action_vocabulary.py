"""Action vocabulary: one declaration, one case, no silent degradation.

An action value that drifts between layers (PEAD's list missing `add`, a report
carrying `BUY`) does not fail loudly — it produces a wrong order or a silently
skipped risk check. These tests pin the vocabulary to a single declaration and make
the unknown value a hard failure everywhere it is consumed.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ats.risk import checks as risk_checks
from ats.schemas.decision import (
    ACTIONS,
    Action,
    UnknownActionError,
    action_direction,
    broker_side,
    display_action,
    is_increasing_action,
    normalize_action,
)
from ats.schemas.journal import JournalEntry
from ats.schemas.pead import PeadRecommendation
from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot

CANONICAL = {"buy", "add", "hold", "trim", "sell"}
SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "ats"


def _walk_python(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _literal_values(node: ast.Subscript) -> set[str]:
    sl = node.slice
    elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
    return {e.value for e in elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def _scan_action_declarations():
    """Every place src/ declares its own action value set."""
    found = []
    for path, tree in _walk_python(SRC_ROOT):
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and getattr(node.value, "id", "") == "Literal":
                if _literal_values(node) & CANONICAL:
                    found.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}")
    return found


def _scan_inline_membership():
    """`action in ("buy", "add")` — a hand-copied subset of the vocabulary."""
    found = []
    for path, tree in _walk_python(SRC_ROOT):
        if path.name == "decision.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and any(
                isinstance(op, ast.In) for op in node.ops
            ):
                for comp in node.comparators:
                    if isinstance(comp, (ast.Tuple, ast.List)):
                        values = {e.value for e in comp.elts
                                  if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                        if values and values <= CANONICAL:
                            found.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}")
    return found


# Uppercase action strings are a broker/display representation; only these modules may
# hold them, and only as a mapping source or an external fill side.
_ALLOWED_UPPERCASE = {"schemas/decision.py", "journal/episodes.py"}


def _scan_uppercase_literals():
    found = []
    for path, tree in _walk_python(SRC_ROOT):
        rel = str(path.relative_to(SRC_ROOT))
        if rel in _ALLOWED_UPPERCASE:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value in {"BUY", "SELL", "HOLD", "TRIM", "ADD"}):
                found.append(f"{rel}:{node.lineno}={node.value}")
    return found


def test_3_1_action_value_set_is_declared_once():
    """3.1: one declaration site — no second `Literal[...]` copy of the vocabulary."""
    sites = {s.split(":")[0] for s in _scan_action_declarations()}
    assert sites == {"schemas/decision.py"}, (
        f"action vocabulary declared in more than one place: {sorted(sites)}"
    )


def test_3_1_no_inline_action_membership_tests():
    """3.1: consumption points must use the shared helpers, not a copied tuple."""
    assert _scan_inline_membership() == []


def test_3_1_uppercase_actions_live_only_in_mapping_modules():
    """3.1: `BUY`/`SELL` are a broker-side representation, not an internal value."""
    assert _scan_uppercase_literals() == []


def test_3_2_all_three_types_share_the_vocabulary():
    assert set(ACTIONS) == CANONICAL
    assert set(PeadRecommendation.model_fields["action"].annotation.__args__) == CANONICAL
    assert set(JournalEntry.model_fields["action"].annotation.__args__) == CANONICAL
    assert set(Action.__args__) == CANONICAL


def test_3_2_add_is_available_to_analyst_recommendations():
    rec = PeadRecommendation(symbol="NVDA", action="add", notional_hint=1_000)
    assert rec.action == "add"


def test_3_2_journal_action_is_typed_not_a_bare_string():
    entry = JournalEntry(entry_id="e1", cycle_id="c1", as_of=datetime.now(timezone.utc),
                         symbol="NVDA", action="trim")
    assert entry.action == "trim"
    with pytest.raises(Exception):
        JournalEntry(entry_id="e2", cycle_id="c1", as_of=datetime.now(timezone.utc),
                     symbol="NVDA", action="rotate")   # not in the vocabulary


def test_3_3_mixed_case_is_normalised_before_the_domain_object():
    assert normalize_action("BUY") == "buy"
    assert normalize_action(" Buy ") == "buy"
    assert normalize_action("TrIm") == "trim"
    rec = PeadRecommendation(symbol="NVDA", action="BUY")
    assert rec.action == "buy"                       # canonical lowercase is stored
    entry = JournalEntry(entry_id="e3", cycle_id="c1", as_of=datetime.now(timezone.utc),
                         symbol="NVDA", action="SELL")
    assert entry.action == "sell"


def test_3_3_unknown_action_is_rejected_not_defaulted():
    with pytest.raises(UnknownActionError) as exc:
        normalize_action("rotate")
    assert "rotate" in str(exc.value)
    assert "rotate" not in CANONICAL


def test_3_4_broker_mapping_covers_every_canonical_value():
    assert broker_side("buy") == "BUY"
    assert broker_side("add") == "BUY"
    assert broker_side("trim") == "SELL"
    assert broker_side("sell") == "SELL"
    assert broker_side("hold") is None       # no order at all — still explicitly mapped
    assert broker_side("Buy") == "BUY"       # mapping consumes the canonical value


def test_3_4_unknown_action_has_no_default_direction():
    with pytest.raises(UnknownActionError):
        broker_side("rotate")


def test_3_4_display_text_is_derived_from_the_canonical_value():
    for action in ACTIONS:
        assert display_action(action) == action.upper()
    assert display_action("buy") == "BUY"


def test_3_5_risk_check_fails_on_an_action_outside_the_vocabulary():
    """An unknown action must be blocked, never read as "nothing to check"."""
    pf = PortfolioSnapshot(as_of=datetime.now(timezone.utc), net_liquidation=1_000_000,
                           cash=1_000_000, gross_exposure=0.0, daily_pnl=0.0,
                           positions=[], exposure=ExposureBreakdown())
    decision = type("FakeDecision", (), {})()  # placeholder replaced below
    from ats.schemas.decision import TradeDecision

    decision = TradeDecision.model_construct(
        symbol="NVDA", action="rotate", notional_usd=1_000.0, qty=None,
        target_weight=None, order_type="limit", limit_price=None,
        time_in_force="DAY", conviction=0.5, rationale="", references=[],
        setup="unknown", stop_price=None, target_price=None,
        planned_horizon_days=None, invalidation="")

    approved, notes, _review = risk_checks.pre_trade([decision], pf)

    assert approved == []
    assert any("BLOCK" in n and "rotate" in n for n in notes), notes


def test_3_6_direction_helpers_agree_with_the_vocabulary():
    assert is_increasing_action("buy") and is_increasing_action("add")
    assert not is_increasing_action("hold")
    assert action_direction("trim") == -1
    assert action_direction("sell") == -1
    assert action_direction("hold") == 0
