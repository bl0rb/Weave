# Context handover

A subagent does not see this conversation. About the task it knows only what
the handover says.

## Handover to `escalation-reviewer`

Use exactly these headings. Write "none" instead of dropping a section.

```markdown
## Task
The user's original request, in their terms.

## Project context
What part of the system this concerns; constraints that apply (2–5 lines).

## Findings so far
What is established, each with its evidence (file:line, command output).

## Changes made
Files already changed and why; whether they are committed.

## Failed attempts
What was tried, what happened, why it was rejected.

## Relevant files
Paths (with line ranges) worth reading. Nothing else needs to be searched.

## Open problems
What is still unknown or blocked.

## Question
The one concrete thing to answer or decide, and what a useful answer contains.
```

- Reference code by `path:line`; paste only the lines the question hinges on.
- Leave out conversation history, unrelated findings and raw logs.
- Mark anything unverified as an assumption.
- A handover to `scout` is one question plus where to look; no template needed.

## Return

`escalation-reviewer` answers under **Answer**, **Evidence**, **Proposed
change**, **Not verified**, **Next step**, followed by a `model:` line with
the model id its system prompt states. That line is a hint and may read
`unknown`. If the handover is incomplete it answers `NEEDS_CONTEXT:` instead;
send the missing part to the same agent (`escalation-policy.md`, step 2).

`scout` returns facts with `path:line`. `NEEDS_L2:` marks a part that needs
analysis; do that part yourself.

## Integration by the main agent

1. Check the answer against the evidence; run the smallest relevant test before
   relying on a proposed change.
2. Record the result in the conversation in a few lines.
3. Apply it and continue the original task. Responsibility stays with the main
   agent, including for anything the subagent got wrong.
