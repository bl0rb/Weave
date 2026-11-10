# Context continuity and task affinity

**Route tasks, not messages.** A task is one goal plus everything that serves
it: clarifications, follow-up questions, fixes, verification, the final report.

## Task affinity

- The routing decision made at the start of a task holds until the task is done
  or the topic changes. The main agent owns the task for its whole lifetime.
- Follow-up and small side questions are answered by the main agent on the
  current model, even when a lower tier could answer them. No model change, no
  delegation.
- Rising difficulty inside a task is not a new task. It may trigger an
  escalation (`escalation-policy.md`); the task still stays with the main agent.
- A topic change allows a new routing decision. It is a topic change only when
  the goal is new and does not build on the files, findings or decisions of the
  current task. When unsure, treat it as a continuation.

## Keeping results

- This conversation is the single record of the task. Summarise every subagent
  result here in a few lines before acting on it.
- Never re-delegate or repeat an analysis whose result is already in this
  conversation; pass the result on instead (`context-handover.md`).
- A follow-up to an earlier escalation goes to the same agent via `SendMessage`
  (it keeps its history) instead of a new agent with a new handover.
- Changing the main model re-reads the whole conversation without cache. Do not
  ask for it mid-task unless `escalation-policy.md` step 3 applies.
