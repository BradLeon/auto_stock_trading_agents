"""OpenRouter sometimes leaks later tool-call parameters into the first string
field as literal `</parameter><parameter name="...">` text. The repair must
recover them (observed live on the chief's ChiefOutput, 2026-07-15)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ats.agents.base import _repair_param_leak


class _Decision(BaseModel):
    symbol: str
    action: str


class _Out(BaseModel):
    summary: str = ""
    decisions: list[_Decision] = Field(default_factory=list)


LEAKED = (
    '整体立场：本次为纯风控修复。</parameter>\n'
    '<parameter name="decisions">[\n'
    '  {"symbol": "BRK B", "action": "trim"},\n'
    '  {"symbol": "MRVL", "action": "trim"}\n'
    ']</parameter>'
)


def test_repairs_leaked_decisions():
    fixed = _repair_param_leak(_Out(summary=LEAKED, decisions=[]), _Out)
    assert fixed.summary == "整体立场：本次为纯风控修复。"
    assert [d.symbol for d in fixed.decisions] == ["BRK B", "MRVL"]


def test_repairs_unclosed_final_parameter():
    fixed = _repair_param_leak(
        _Out(summary='ok</parameter>\n<parameter name="decisions">'
                     '[{"symbol": "TSM", "action": "buy"}]'), _Out)
    assert fixed.summary == "ok"
    assert fixed.decisions[0].symbol == "TSM"


def test_clean_output_passes_through_unchanged():
    clean = _Out(summary="正常输出", decisions=[_Decision(symbol="TSM", action="buy")])
    assert _repair_param_leak(clean, _Out) is clean


def test_unknown_parameter_names_are_ignored():
    fixed = _repair_param_leak(
        _Out(summary='ok</parameter>\n<parameter name="nope">x</parameter>'), _Out)
    assert fixed.summary == "ok"
    assert fixed.decisions == []
