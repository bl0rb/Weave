# Model routing

Refines the "Models" and "Agents" sections of `AGENTS.md`. Where the two
differ, these rules apply in Claude Code. Rules refer to tiers; model names
appear only in the agent frontmatter and in the capability order below.

## Tiers

| Tier | Runs as | Model set in |
|---|---|---|
| L1 Lightweight | subagent `scout` | `model:` in `.claude/agents/scout.md` |
| L2 Standard | main agent (this conversation) | session model, chosen by the user |
| L3 Deep reasoning | subagent `escalation-reviewer` | `model:` in `.claude/agents/escalation-reviewer.md` |

Capability order of model families, weakest first: haiku, sonnet, opus, fable.
Versions within one family count as equal.

## Who does a step

The task stays with the main agent (`context-continuity.md`). These bullets
decide single steps inside it.

- **Default: the main agent does the step itself.** Development, analysis,
  debugging, implementation and anything that builds on earlier turns stay here.
- **L1 for a self-contained, read-only lookup** that returns a short summary
  and would otherwise take many tool calls or pull large files or logs into
  this conversation, such as summarising a large log. Whatever one search or
  a handful of tool calls answers is done inline, as are small edits:
  starting a subagent costs more than that.
- The two agents are narrower than the Scout and Reviewer roles in
  `AGENTS.md`: `scout` locates and summarises, `escalation-reviewer` answers
  one hard question. Finding root causes and reviewing diffs stay with the
  main agent.
- **L3 only through `escalation-policy.md`.** Never as a starting point.

## Cost rule

Optimise total cost to a correct result, not price per call. Count the handover
tokens, the subagent re-reading files, failed attempts and lost prompt cache.
A cheaper model that needs two extra attempts is the expensive choice.

- One subagent at a time by default. At most two `scout` agents in parallel, and
  only for disjoint questions. Never more than one `escalation-reviewer`.
- Do not pass a per-call `model` override to these agents. The user sets the
  mapping in the frontmatter; do not edit it yourself.

These are instructions the main agent follows. Claude Code does not enforce
them and does not route tasks to another model on its own.
