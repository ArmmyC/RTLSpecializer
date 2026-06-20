# Process Spec: Codex Context Reduction v0.1

## 1. Goal

Reduce repetitive Codex prompt tokens by moving durable repository instructions into reusable project files.

This spec follows the Codex best-practice pattern of keeping durable guidance in `AGENTS.md`, using task-specific specs under `docs/specs/`, and keeping review/prompt templates in smaller referenced documents.

## 2. Problem

Recent Codex prompts repeated the same rules for every task:

- do not download data,
- do not call LLMs,
- do not run EDA/simulation/synthesis,
- do not commit generated data,
- read the active spec,
- run tests listed in the spec,
- summarize changed files and test results.

That repeated text increases token usage and makes prompts harder to scan.

## 3. Solution

Create reusable context files:

```text
AGENTS.md
docs/codex/code_review.md
docs/codex/task_prompt_template.md
```

Then future prompts can be short:

```text
Pull latest.

Read:
docs/specs/<active-spec>.md

Follow AGENTS.md.
Implement the spec exactly.
Run the tests listed in the spec.
Commit and push.
```

## 4. Files added

### `AGENTS.md`

Repo-level durable guidance for Codex:

- project summary,
- repo layout,
- always-do rules,
- never-do rules,
- default commands,
- coding conventions,
- review expectations,
- definition of done.

### `docs/codex/code_review.md`

Reusable review checklist for inspecting Codex-generated work:

- scope/spec fit,
- safety and data handling,
- architecture/code quality,
- tests,
- docs/UX,
- standard Repo Review output format.

### `docs/codex/task_prompt_template.md`

Short prompt templates for:

- implementation tasks,
- focused fixes,
- review tasks.

## 5. Expected workflow change

Before this spec, prompts often repeated durable rules.

After this spec, prompts should include only:

- the task goal,
- the active spec path,
- any fresh local error/output context,
- a short instruction to follow `AGENTS.md`.

## 6. Usage examples

### Implementation

```text
Pull latest.

Task:
Harden finalization output cleanup.

Read:
docs/specs/finalization-output-safety-hardening-v0.1.md

Follow AGENTS.md.
Implement the spec exactly.
Run the tests listed in the spec.
Commit and push.

Report changed files, commands run, test results, and tradeoffs.
```

### Review

```text
Pull latest.

Review the latest implementation against:
docs/specs/finalization-output-safety-hardening-v0.1.md

Follow AGENTS.md and docs/codex/code_review.md.
Report using the Repo Review format.
```

## 7. Definition of done

Done when:

- `AGENTS.md` exists at repo root.
- `docs/codex/code_review.md` exists.
- `docs/codex/task_prompt_template.md` exists.
- Future prompts can drop repeated durable constraints.
- No source code, data, generated outputs, or schemas are changed by this process-only spec.
