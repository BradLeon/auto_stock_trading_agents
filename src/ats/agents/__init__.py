"""Agent implementations.

Phase 2 keeps stub node bodies in ``ats.graph.nodes``. Phase 3+ moves the real
logic here — one module per role (analysts/, risk_manager, manager, trader) —
each loading its SKILL.md (see ``ats/skills/``) and calling the LLM via
``ats.llm.get_model``. The graph wiring in ``ats.graph.build`` stays unchanged;
nodes will simply delegate to these implementations.
"""
