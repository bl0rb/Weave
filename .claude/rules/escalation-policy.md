# Escalation policy

Escalation means: the main agent asks the L3 agent `escalation-reviewer` one
concrete question and then continues the task itself.

## Triggers

One is enough, but it must be backed by something observed in this task:

1. Two or more distinct solution attempts failed on the same problem.
2. High uncertainty on a decision that is costly to reverse (architecture, data
   model, security, public contract).
3. An architecture problem that spans several services or contracts and has no
   clearly better option.
4. Contradictory analysis results that a cheap check cannot settle.
5. A root cause still unknown after the likely explanations were tested.
6. The current model keeps producing wrong or circular reasoning on the problem.

Not triggers: message length, number of follow-up questions, large but
mechanical work, missing information the user or a quick check can supply.

## Value check

Escalate only if all four hold:

- The question fits in one or two sentences.
- L3 adds capability: the model family your system prompt names for this
  conversation ranks below the family in the `model:` line of
  `.claude/agents/escalation-reviewer.md` (read that line; order in
  `model-routing.md`). If it ranks the same or higher, or you cannot tell,
  escalate only for trigger 4 (an independent second view).
- No cheaper step would settle it: run the test, read the full error, ask the user.
- The handover costs less than another failed attempt is likely to.

If the check fails, change the approach on the current model or ask the user.

## Steps

1. Start `escalation-reviewer` with a handover per `context-handover.md`. If
   the advisor tool is enabled in this session, consult it instead: same
   triggers and value check, no handover, and it counts as a round.
2. At most two rounds per problem; a follow-up goes to the same agent
   (`context-continuity.md`).
3. If two rounds did not resolve it, or the rest of the task needs deep
   reasoning throughout and cannot be isolated into a question, tell the user
   and recommend switching the main model with `/model`. Only the user can do
   that. When that task is done, remind the user to switch back.

Then integrate the result per `context-handover.md` and continue the task on
the main model.

## Fallback

If starting an agent returns an error:

- Do not retry in a loop and do not switch to another agent type.
- L3: continue on the main model with a changed approach and tell the user
  that the escalation did not happen.
- L1: do the lookup inline.

Claude Code can also run an agent on a substitute model without any error.
`escalation-reviewer` ends its reply with the model it ran on. Name that model
when you report the escalation, and if it does not rank above this
conversation's family, treat the reply as a second view, not as an L3 result.
