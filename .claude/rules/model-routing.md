# Model routing

Refines the "Models" and "Agents" sections of `AGENTS.md`. Rules refer to tiers
only; model names are set in the places listed below and nowhere else.

## Tiers

| Tier | Runs as | Model set in | Default |
|---|---|---|---|
| L1 Lightweight | subagent `scout` | `model:` in `.claude/agents/scout.md` | `haiku` |
| L2 Standard | main agent (this conversation) | session model (`/model`, `model` setting) | user's choice, `sonnet` recommended |
| L3 Deep reasoning | subagent `escalation-reviewer` | `model:` in `.claude/agents/escalation-reviewer.md` | `opus` |

## Routing

Route once per task (see `context-continuity.md`), then stay.

- **Default: the main agent does the work itself.** Development, analysis,
  debugging, implementation and anything that builds on earlier turns stay here.
- **L1 only for self-contained, read-only work** where a short summary is all
  that comes back: broad file or symbol search, summarising a large file or log.
  Do small lookups, quick questions and small edits inline: a subagent start
  costs more than a few tool calls on the main model.
- **L3 only through `escalation-policy.md`.** Never as a starting point.

## Cost rule

Optimise total cost to a correct result, not price per call. Count the handover
tokens, the subagent re-reading files, failed attempts and lost prompt cache.
A cheaper model that needs two extra attempts is the expensive choice.

- One subagent at a time by default. At most two `scout` agents in parallel, and
  only for disjoint questions. Never more than one `escalation-reviewer`.
- Do not delegate what is already known in this conversation.
- Do not pass a per-call `model` override to these agents; change the
  frontmatter instead, so the mapping stays in one place.

These are instructions the main agent follows. Claude Code does not enforce
them and does not switch the main model on its own.
