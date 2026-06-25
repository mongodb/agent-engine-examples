---
name: convention-quality
description: Language-specific convention and code-cleanliness checks across TypeScript/React, Python, and Go. Covers naming, idiomatic patterns, dead code, and general code hygiene. Use for reviews that touch application source code.
---

# Convention & Code Quality

Use this skill to flag language-level convention violations and code-cleanliness issues. Cite `REVIEW-RULE-ID-CQ-*` tokens in findings.

## Scope

Applies to any change under `agentic-platform-ui/`, `runner/`, `internal/`, or `executor-control-plane/`. Check the changed file against its language's conventions below and the project's `docs/coding-standards.md` if that content has been passed to you.

## Rules

### REVIEW-RULE-ID-CQ-NAMING
Python: functions, variables, methods use `snake_case`; classes use `CapWords`; constants use `UPPER_SNAKE_CASE`. Go: exported identifiers use `CamelCase`, unexported use `camelCase`, package names are short lowercase. TypeScript/React: components `PascalCase`, hooks `useCamelCase`, variables `camelCase`. Flag single-letter identifiers outside loop counters.

### REVIEW-RULE-ID-CQ-DEAD-CODE
Unused imports, unused variables, unreachable code after return/raise, commented-out blocks left in the diff. Linters catch most of this — only flag cases that would slip past ruff/eslint/go vet.

### REVIEW-RULE-ID-CQ-DUPLICATION
Two or more near-identical blocks of logic in the same file or adjacent files that should be extracted. Judgement call: only flag when the duplication is load-bearing and the abstraction is obvious; don't force DRY for three-line snippets.

### REVIEW-RULE-ID-CQ-LOCAL-CONVENTION
Before flagging a style deviation, read the surrounding unchanged code. If the repo consistently uses one pattern (e.g. early returns everywhere), a change that introduces the opposite pattern is a real finding. If the file already mixes styles, don't flag.

### REVIEW-RULE-ID-CQ-COMMENTS
Comments that describe WHAT the code does (redundant with well-named identifiers). Comments that describe intent, invariants, or non-obvious WHY are valuable — don't flag those. Never flag docstrings for documented exported symbols.

### REVIEW-RULE-ID-CQ-MAGIC-NUMBERS
Hardcoded thresholds, timeouts, retry counts, or buffer sizes used at a call site without a named constant. Flag `time.Sleep(30 * time.Second)` with no comment or constant; accept it if the value is self-evidently test-scoped.

### REVIEW-RULE-ID-CQ-TYPE-ANNOTATION
Python functions in library code missing type hints on parameters or return types (test fixtures and __init__ are exempt). TypeScript public function/component signatures without explicit return types where a reader would benefit.

## What NOT to flag

- Issues a linter (ruff, eslint, go vet), typechecker (mypy, tsc), or compiler would catch — assume CI handles those
- Pre-existing issues on lines not touched by the diff
- Pedantic style preferences not grounded in project conventions
- Intentional behavioral changes that match the stated purpose of the PR

## Output format

Every finding you emit MUST include the specific `REVIEW-RULE-ID-CQ-*` token from this file. That citation proves this skill was consulted.
