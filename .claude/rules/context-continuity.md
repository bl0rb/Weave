# Context continuity and task affinity

**Decide per task, not per message.** A task is one goal plus everything that serves
it: clarifications, follow-up questions, fixes, verification, the final report.

## Task affinity

- A task stays with the main agent and the session model from start to finish.
  Delegating a single step (`model-routing.md`) or escalating a question
  (`escalation-policy.md`) does not move the task.
- Follow-up and small side questions are answered by the main agent on the
  current model, even when a lower tier could answer them.
- Rising difficulty inside a task is not a new task. It may trigger an
  escalation; the task still stays with the main agent.
- A topic change starts a new task that is judged afresh: delegations and
  escalation rounds of the previous task do not carry over. It is a topic change only when the goal is new and does not
  build on the files, findings or decisions of the current task. When unsure,
  treat it as a continuation.

## Keeping results

- This conversation is the single record of the task; subagent results are
  recorded here (`context-handover.md`).
- Never re-delegate or repeat an analysis whose result is already in this
  conversation; pass the result on instead.
- A follow-up to an earlier escalation goes to the same agent via `SendMessage`
  (it keeps its history) instead of a new agent with a new handover.
- Changing the main model re-reads the whole conversation without cache. Do not
  ask for it mid-task unless `escalation-policy.md` step 3 applies.
