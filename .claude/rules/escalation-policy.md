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
- The L3 model is stronger than the model this conversation runs on. If it is
  not, escalate only for trigger 4 (an independent second view).
- No cheaper step would settle it: run the test, read the full error, ask the user.
- The handover costs less than another failed attempt is likely to.

If the check fails, change the approach on the current model or ask the user.

## Steps

1. Start `escalation-reviewer` with a handover per `context-handover.md`.
2. For a follow-up, message the same agent. At most two rounds per problem.
3. If two rounds did not resolve it, or the rest of the task needs deep
   reasoning throughout and cannot be isolated into a question, tell the user
   and recommend switching the main model with `/model`. Only the user can do
   that; say that it costs one uncached re-read of the conversation.

After every escalation the main agent verifies the result, integrates it and
continues the original task on its own model.

## Fallback

If an agent cannot be started or its model is not available:

- Do not retry in a loop and do not escalate to a different agent type instead.
- L3 unavailable: continue on the main model with a changed approach and tell
  the user that the escalation did not happen.
- L1 unavailable: do the lookup inline.
- Never report a result as L3-reviewed when the review did not run.
