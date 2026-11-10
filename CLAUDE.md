# CLAUDE.md

@AGENTS.md

## Model escalation

Three model tiers, routed per task (not per message). The rules load automatically
from `.claude/rules/`; each topic has exactly one owner file:

| Topic | File |
|---|---|
| Tiers, model mapping, when to delegate | `.claude/rules/model-routing.md` |
| Task affinity, follow-ups, topic changes | `.claude/rules/context-continuity.md` |
| Escalation triggers, value check, fallback | `.claude/rules/escalation-policy.md` |
| Handover and return format | `.claude/rules/context-handover.md` |

Agents: `scout` (L1), `escalation-reviewer` (L3) in `.claude/agents/`.
Configuration, technical limits and test matrix: `.claude/README.md` (not auto-loaded).
