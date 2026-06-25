---
name: documentation
description: Documentation drift — repository map, architecture READMEs, environment variables, ADRs, proto comments, public API doc comments. Always run, even for code-only PRs.
---

# Documentation

Flag documentation that has drifted from the code. Cite `REVIEW-RULE-ID-DOC-*` tokens in findings.

## Scope gates (CHECK BEFORE MATCHING ANY RULE)

A rule only applies when **all three** are true:

1. **The thing being added is actually a documented surface.** DOC-PUBLIC-API targets exported Go functions/types, public Python classes, REST/gRPC endpoints — code that has external consumers who'd read its docs. Internal TypeScript helpers, Zustand actions, and React components are not on that list.
2. **The corresponding doc location exists.** DOC-COMPONENT-README applies only when a `docs/<component>/README.md` exists for that component. Don't invent a new doc to fail it on.
3. **The diff actually adds the surface.** Pre-existing surfaces missing docs is not new drift caused by this PR.

If any gate fails, do NOT flag — return `No findings.` for that rule.

## Common false positives — DO NOT FLAG

- **Zustand store actions** (`upsertSubagentBlock`, `clearDebugRuns`, etc.). These are internal TypeScript functions consumed in the same package. They are not "public API" in DOC-PUBLIC-API's sense — that rule targets cross-process consumers (Go callers, Python callers, REST clients), not in-package TypeScript callers.
- **React component props or render helpers.** Same logic — internal to the front-end app, not a public API surface.
- **Type definitions and interfaces.** TypeScript types are documentation IN the type signature; restating that in a doc comment is noise.
- **Renames or refactors of internal symbols.** Internal renames don't need ADRs (DOC-ADR is for architectural decisions like new stores, new auth models, new protocols).

## Repository-level

### REVIEW-RULE-ID-DOC-REPO-MAP
New directory added under the repository root (e.g. `runner/new-service/`, `internal/domains/new-thing/`) that does not appear in the repository map in `AGENTS.md` / `CLAUDE.md`. The map entry should describe what the directory contains in one line.

### REVIEW-RULE-ID-DOC-COMPONENT-README
Changes to a component's service interface, data flow, or public contract that are NOT reflected in the corresponding `docs/<component>/README.md`. Scan the existing component README for claims the diff has invalidated.

### REVIEW-RULE-ID-DOC-ENV-VAR
New environment variable introduced via `os.Getenv()`, `import.meta.env.*`, a Viper config key, or a typed config struct field — that has no entry in the relevant `docs/<component>/README.md` config table. Include variable name, purpose, and default value in the doc.

### REVIEW-RULE-ID-DOC-ADR
Significant architectural decision in the diff (new external dependency, new storage pattern, changed authentication model, new protocol, new deployment target) that warrants a record in `docs/decisions/` but has no accompanying ADR file. Decisions that are "we picked X over Y because Z" benefit most.

## Code-level

### REVIEW-RULE-ID-DOC-INTENT-COMMENT
Algorithm, workaround, or business rule in the diff whose intent would not be self-evident to a new reader — the *why* should be captured in a short comment. Don't flag well-named straightforward code.

### REVIEW-RULE-ID-DOC-PUBLIC-API
New exported Go function/type, new public Python function/class, or new REST/gRPC endpoint added without a doc comment. At minimum: one sentence describing purpose, and any non-obvious side effects or failure modes.

### REVIEW-RULE-ID-DOC-PROTO-FIELD
New proto message field added without a comment describing its purpose and valid values. Proto comments become the wire-contract documentation — future consumers depend on them.

## What NOT to flag

- Internal, unexported helpers with obvious bodies
- Test fixtures and mocks
- Existing stale docs on unchanged areas — only flag drift caused by this PR
- Missing `README.md` in a directory that matches its siblings' conventions (READMEs are per-component, not per-directory)

## Output

Every finding MUST cite a specific `REVIEW-RULE-ID-DOC-*` token from this skill.
