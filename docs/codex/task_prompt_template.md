# Codex Task Prompt Template

Use this template to keep Codex prompts short. Durable repo rules live in `AGENTS.md`; task-specific details live in `docs/specs/*.md`.

## Standard implementation prompt

```text
Pull latest.

Task:
<one sentence describing the goal>

Read:
<docs/specs/name-of-active-spec.md>

Follow AGENTS.md.
Implement the spec exactly.
Do not expand scope.
Run the tests listed in the spec.
Commit and push.

Report:
- changed files
- commands run
- test results
- generated files, if any
- tradeoffs or known issues
```

## Standard focused-fix prompt

```text
Pull latest.

Read:
<docs/specs/name-of-fix-spec.md>

Follow AGENTS.md.
This is a focused fix. Keep existing workflow behavior unchanged except for the requested fix.
Run the tests listed in the spec.
Commit and push.

Report:
- changed files
- commands run
- test results
- smoke results, if any
- tradeoffs or known issues
```

## Standard review prompt

```text
Pull latest.

Review the latest implementation against:
<docs/specs/name-of-active-spec.md>

Follow AGENTS.md and docs/codex/code_review.md.
Do not modify files unless the review finds a blocking issue and the task explicitly asks for a fix.

Report using the Repo Review format.
```

## When to include extra context

Add extra context only when it is not already in `AGENTS.md` or the active spec:

- a new error message,
- a failing command output,
- a local path that Codex cannot infer,
- a new design decision,
- a temporary exception to normal rules.

Keep repeated safety, data, testing, and reporting rules in `AGENTS.md` instead of pasting them into every prompt.
