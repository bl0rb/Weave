# AGENTS.md

## Goal

Complete tasks correctly with minimal token, tool, agent, and credit usage.

## Scope

* Make the smallest change that fully solves the task.
* Do not refactor, rename, reformat, or modify unrelated code.
* Do not add features or dependencies unless required.
* Reuse existing project patterns and utilities.
* Stop when the requested task is complete and verified.

## Exploration

* Inspect only files relevant to the task.
* Prefer targeted search for exact symbols, errors, routes, components, or functions.
* Do not scan the full repository unless necessary.
* Do not reread files unless they changed or important context is missing.
* Ignore generated folders, dependencies, caches, build output, and unrelated files.
* Stop investigating once enough information exists to implement safely.

## Context

* Keep context minimal.
* Read only relevant file sections when possible.
* Summarize findings instead of passing large raw outputs.
* Do not repeat information already established.
* Do not include unnecessary logs, code, or conversation history in agent handoffs.

## Agents

* Do not spawn additional agents unless parallel work clearly saves time or improves correctness.
* Do not use sub-agents for simple searches, edits, tests, formatting, or one-file fixes.
* Avoid multiple agents investigating the same problem.

If using 3 agents:

1. **Scout:** find relevant files and root cause.
2. **Implementer:** make the smallest correct change.
3. **Reviewer:** inspect the diff and verify only relevant behavior.

Each agent should reuse prior findings instead of repeating work.

## Models

Use the cheapest capable model when model selection is available.

Use stronger models only for difficult reasoning, architecture, debugging, security, or problems cheaper models could not solve.

## Implementation

* Prefer small diffs.
* Follow existing code conventions.
* Avoid unrelated cleanup.
* Do not rewrite working code merely for style.
* Do not add abstractions unless needed.
* Do not install packages if existing code can solve the task.

## Testing

Run the smallest relevant validation first:

1. directly related test
2. related test file/module
3. broader tests only if necessary

* Do not run the full test suite by default.
* Do not repeatedly run unchanged tests.
* Do not fix unrelated failures.
* Do not run expensive builds unless relevant.

## Debugging

* Start from errors, stack traces, failing tests, logs, and reproduction steps.
* Test the most likely explanation first.
* Avoid broad speculative investigation.
* Do not repeat failed actions without changing the approach.

## Output

Keep communication minimal.

* Do not narrate routine actions.
* Do not explain obvious steps.
* Do not repeat the task.
* Do not provide long reasoning unless requested.
* Final response: maximum 5–8 lines unless explicitly requested otherwise.

Final response should contain only:

* what changed
* important files changed
* tests/checks performed
* blocker or remaining issue, if any

## Completion

When the requested behavior works and relevant validation passes:

**STOP.**

Do not continue with optional cleanup, refactoring, documentation, extra testing, or additional agent work unless explicitly requested.

## Priority

Prioritize:

1. safety
2. explicit user instructions
3. correctness
4. minimal scope
5. credit/token efficiency

Never sacrifice correctness or safety just to save credits.