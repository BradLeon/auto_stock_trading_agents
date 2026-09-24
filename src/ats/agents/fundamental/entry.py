"""双模式触发契约与显式入口（Phase D task 5.1）。

触发契约是显式的：一种触发只属于一种模式，例行由信息简报或预期数据变化触发，
事件由财报或明确公司事件触发。用错误的触发类型构造另一种模式的运行请求会被
拒绝——两条产线不共享入口，就不会互相污染。

入口也是显式的：CLI `ats analyst fundamental --mode routine|event`，或程序内
直接构造 `FundamentalRunRequest` 传入 `run_fundamental_pass`。运行独立终结，
不触发主理人、风控或任何执行链。
"""

from __future__ import annotations

from dataclasses import dataclass, field

log = __import__("logging").getLogger("ats.agents.fundamental.entry")

MODE_ROUTINE = "routine"
MODE_EVENT = "event"

# 触发契约：一种模式只接受自己的触发类型（5.1）。
ROUTINE_TRIGGERS: tuple[str, ...] = ("information_brief_update", "expectation_data_change")
EVENT_TRIGGERS: tuple[str, ...] = ("earnings_release", "company_event")

TRIGGERS_BY_MODE: dict[str, tuple[str, ...]] = {
    MODE_ROUTINE: ROUTINE_TRIGGERS,
    MODE_EVENT: EVENT_TRIGGERS,
}


@dataclass(frozen=True)
class FundamentalRunRequest:
    """一次基本面运行的显式请求。

    `mode` 决定整条产线：routine 走 `routine.run_routine_pass`，event 走
    `event.run_event_pass`。`trigger` 是触发来源，必须属于该模式的触发词表。
    """

    mode: str
    symbol: str
    trigger: str
    fiscal_label: str = ""       # 事件模式：目标报告期
    cutoff: str = ""             # 事件模式：基线冻结时刻（ISO）
    use_llm: bool = True
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in TRIGGERS_BY_MODE:
            raise ValueError(
                f"unknown fundamental run mode {self.mode!r}; "
                f"expected one of {', '.join(TRIGGERS_BY_MODE)}")
        allowed = TRIGGERS_BY_MODE[self.mode]
        if self.trigger not in allowed:
            raise ValueError(
                f"trigger {self.trigger!r} does not belong to mode {self.mode!r}; "
                f"allowed: {', '.join(allowed)}")
        if self.mode == MODE_EVENT and not self.fiscal_label:
            raise ValueError("event-mode run requires a fiscal_label (reporting period)")
        object.__setattr__(self, "symbol", self.symbol.upper())


def routine_request(symbol: str, *, trigger: str, use_llm: bool = True,
                    extra: dict | None = None) -> FundamentalRunRequest:
    """例行模式运行请求：信息简报或预期数据变化触发。"""
    return FundamentalRunRequest(mode=MODE_ROUTINE, symbol=symbol, trigger=trigger,
                                 use_llm=use_llm, extra=dict(extra or {}))


def event_request(symbol: str, *, trigger: str, fiscal_label: str,
                  cutoff: str = "", use_llm: bool = True,
                  extra: dict | None = None) -> FundamentalRunRequest:
    """事件模式运行请求：财报或明确公司事件触发，带报告期与 cutoff。"""
    return FundamentalRunRequest(mode=MODE_EVENT, symbol=symbol, trigger=trigger,
                                 fiscal_label=fiscal_label, cutoff=cutoff,
                                 use_llm=use_llm, extra=dict(extra or {}))


def build_run_request(mode: str, symbol: str, *, trigger: str,
                      **kwargs) -> FundamentalRunRequest:
    """按模式名分发构造——CLI 与调度器的唯一构造口。"""
    if mode == MODE_ROUTINE:
        return routine_request(symbol, trigger=trigger,
                               use_llm=kwargs.pop("use_llm", True), extra=kwargs)
    if mode == MODE_EVENT:
        return event_request(symbol, trigger=trigger, **kwargs)
    raise ValueError(f"unknown fundamental run mode {mode!r}")


def run_fundamental_pass(request: FundamentalRunRequest) -> dict:
    """执行一次基本面运行。独立终结：不触发主理人、风控或执行链。"""
    if request.mode == MODE_ROUTINE:
        from . import routine

        return routine.run_routine_pass(request)
    from . import event

    return event.run_event_pass(request)
