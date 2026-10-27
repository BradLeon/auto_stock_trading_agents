"""Agent implementations — one package/module per role.

Analyst roles (macro/, sector/, pead/) update the knowledge base; the chief
(chief/) is the single decision maker, feeding the decision graph in
``ats.graph.chief``. risk_validator holds the read-only guardrail review
(no caller in src today; kept until the trade path decides). Each LLM role loads its SKILL.md (see
``ats/skills/``) and calls the model via ``ats.llm``.
"""
