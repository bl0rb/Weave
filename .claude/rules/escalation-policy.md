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
Work that cannot be put as one question is covered by step 3.

## Steps

1. Start `escalation-reviewer` with a handover per `context-handover.md`. If
   you have an advisor tool and the answer rests on this conversation rather
   than on new reads or checks, ask the advisor instead. Claude Code checks
   the advisor's rank and it needs no handover, so only the first and third
   value-check bullets apply. Never use both for one problem.
2. A round is one L3 call (reviewer or advisor) on a problem, including a call
   that errors or is answered by a substitute model (Fallback). At most two
   rounds per problem; a reworded, narrowed or split question about the same
   failure or decision is the same problem. A follow-up goes to the same agent
   (`context-continuity.md`). Resending context after a first `NEEDS_CONTEXT:`
   is not a round; after a second one it is.
3. If two rounds did not resolve it, or the rest of the task needs deep
   reasoning throughout and cannot be isolated into a question, tell the user
   and recommend switching the main model with `/model` to a family that ranks
   above the current one. If there is none, report the problem as unresolved.
   Only the user can switch. Name the current model in the recommendation, and
   when that task is done or the topic changes, remind the user to switch back.

Then integrate the result per `context-handover.md` and continue the task on
the main model.

## Fallback

If starting an agent returns an error:

- Do not retry in a loop and do not switch to another agent type.
- L3: continue on the main model with a changed approach and tell the user
  that the escalation did not happen.
- L1: do the lookup inline.

Claude Code can also run an agent on a substitute model without an error.
Signs: the reply opens with a line saying the agent switched models, or its
`model:` line (`context-handover.md`, Return) ranks below the family in the
agent's frontmatter or cannot be ranked. Then name the model when you report
the escalation and treat the reply as a second view, not as an L3 result.
