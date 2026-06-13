"""Assemble the trading-cycle StateGraph.

    START → ingest → (Send fan-out) → [macro | industry×N | fundamental×M | technical×M]
          → risk_manager → manager → boss_review (interrupt) → trader → persist → END

All analyst nodes edge into risk_manager, which therefore runs once after the
entire parallel layer completes (map-reduce join).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import TradingState

ANALYST_NODES = ("macro_analyst", "industry_analyst", "fundamental_analyst", "technical_analyst")


def build_graph(checkpointer: Any | None = None):
    g = StateGraph(TradingState)

    g.add_node("ingest", nodes.ingest)
    g.add_node("macro_analyst", nodes.macro_analyst)
    g.add_node("industry_analyst", nodes.industry_analyst)
    g.add_node("fundamental_analyst", nodes.fundamental_analyst)
    g.add_node("technical_analyst", nodes.technical_analyst)
    g.add_node("risk_manager", nodes.risk_manager)
    g.add_node("manager", nodes.manager)
    g.add_node("boss_review", nodes.boss_review)
    g.add_node("trader", nodes.trader)
    g.add_node("persist", nodes.persist)

    g.add_edge(START, "ingest")
    # Parallel fan-out via Send; targets listed so the graph knows the edges.
    g.add_conditional_edges("ingest", nodes.fan_out, list(ANALYST_NODES))
    # Join: every analyst flows into the risk manager (runs once, after all).
    for n in ANALYST_NODES:
        g.add_edge(n, "risk_manager")
    g.add_edge("risk_manager", "manager")
    g.add_edge("manager", "boss_review")
    g.add_edge("boss_review", "trader")
    g.add_edge("trader", "persist")
    g.add_edge("persist", END)

    return g.compile(checkpointer=checkpointer)
