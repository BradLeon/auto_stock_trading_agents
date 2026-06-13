# Skills — Agent execution procedures

Each agent role gets one `SKILL.md` here, codifying its workflow (goal, data
tools to call, analysis checklist, output schema constraints, few-shot
examples). At runtime the agent node injects its SKILL.md into the system prompt
(stable prefix → Claude prompt caching). Process lives here; code stays thin.

Planned (Phase 4+):

```
skills/
  macro-analyst/SKILL.md
  industry-analyst/SKILL.md
  fundamental-analyst/SKILL.md
  technical-analyst/SKILL.md
  risk-manager/SKILL.md
  manager/SKILL.md
```
