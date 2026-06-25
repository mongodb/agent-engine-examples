---
name: architecture-docs
description: Architecture documentation — component diagrams, data flow docs, ADRs, dependency documentation. Detects significant structural changes without corresponding architectural docs.
---

# Architecture Documentation

Identify architecture documentation that has drifted from the codebase and draft updated content. Cite `DOCS-RULE-ID-ARCH-*` tokens in findings.

## Scope

You are responsible for high-level architecture documentation: component overviews, service interaction diagrams, ADRs (Architecture Decision Records), and dependency documentation. Focus on changes that alter the system's structure, not routine code changes within existing components.

## Rules

### DOCS-RULE-ID-ARCH-COMPONENT
A new component, service, or major module was added to the repository without corresponding architecture documentation.

**Check for:**
- New top-level directories representing services or components
- New `main.go`, `main.py`, or similar entry points for new services
- New Kubernetes deployments or service definitions

### DOCS-RULE-ID-ARCH-FLOW
A data flow between components changed — new inter-service calls, changed message formats, modified event chains, or altered request routing — but architecture diagrams or flow documentation was not updated.

**Check for:**
- New HTTP/gRPC client calls to other services
- Changed message queue producers or consumers
- Modified request routing or middleware chains
- New database connections from services that previously didn't use them

### DOCS-RULE-ID-ARCH-DECISION
A significant design decision was made in the code that warrants an ADR but has none. Good candidates: choosing a new external dependency, adopting a new pattern, changing an authentication model, or introducing a new protocol.

**Check for:**
- New external dependencies added to dependency files (go.mod, pyproject.toml, package.json)
- New integration patterns (new message queue, new cache layer, new auth provider)
- Significant refactors that change how components interact

### DOCS-RULE-ID-ARCH-DEPENDENCY
A new external dependency was added without documentation explaining why it was chosen and what it provides.

**Check for:**
- New entries in go.mod, pyproject.toml, or package.json that are significant (not just version bumps)
- New Docker base images or infrastructure dependencies
- New third-party API integrations

## Output format

For each finding, provide:

```
**File:** <path to the doc file that needs updating, or suggested new file path>
**Rule:** DOCS-RULE-ID-ARCH-<name>
**What changed:** <brief description of the architectural change>
**Current text:** <the stale section, or "N/A — no existing documentation">
**Suggested update:** <the corrected or new text>
```

**Important:** Always prefer updating an existing documentation file over creating a new one. Only suggest a new file path when no existing file covers the topic. New file names must be descriptive of their content (e.g., `docs/architecture/auth-flow.md`) — never use dates in file names.

Separate multiple findings with `---` on its own line.

If no architecture doc updates are needed, return exactly: `No findings.`
