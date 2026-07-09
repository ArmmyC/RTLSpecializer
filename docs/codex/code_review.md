# Codex Code Review Checklist

Use this checklist when inspecting Codex-generated changes in RTLSpecializer.

## 1. Scope and spec fit

- Identify the active spec under `docs/specs/`.
- Compare changed files against the files expected by the spec.
- Check whether the implementation expands scope beyond the spec.
- Call out completed, partially completed, and missing requirements.

## 2. Safety and data handling

- Confirm raw/local/generated data was not committed.
- Confirm tools treat dataset content, RTL, reports, and generated text as untrusted data.
- Check that no new workflow downloads data, calls external APIs, calls LLMs, trains/runs models, or executes RTL/EDA unless explicitly required by the spec.
- For cleanup or `--force` behavior, check path overlap, symlink handling, `.local_data` rejection, and preservation of unknown files.

## 3. Architecture and code quality

- Prefer reusable modules plus thin CLIs.
- Reuse existing helpers for validation, promotion, release, and evaluation.
- Avoid duplicated validation logic unless the spec requires it.
- Keep CLIs deterministic and useful in both text and JSON modes.
- Ensure preflight failures produce clear errors before destructive writes.

## 4. Tests

- Verify tests cover success, failure, edge cases, CLI JSON, and output safety.
- Prefer targeted tests for the changed behavior plus broader suites listed in the spec.
- Check that tests use synthetic fixtures, not real VerilogEval/raw user data.

## 5. Docs and UX

- Update user-facing docs when commands or workflow behavior change.
- Include examples that match real CLI options.
- Explain local-only/generated output behavior clearly.
- Clarify what human review still means and what the tool does not prove.

## 6. Review output format

Use this structure when reporting back:

```text
Repo Review
1. Summary
2. Spec Compliance
   Completed:
   Partially completed:
   Missing:
3. Issues Found
   Critical
   Important
   Minor
4. Recommended Next Step
5. New or Updated Spec
6. Goal Prompt for Codex
```

If fixes are needed, create a focused fix spec and provide a short implementation prompt.

If implementation is good enough, create the next highest-value feature/improvement spec and provide a short implementation prompt.
