---
name: security-review
description: Security vulnerabilities — injection, SSRF, cross-tenant data leakage, unauthenticated endpoints, secrets in code, insecure client state, unbounded request bodies. Covers Go/Gin, Python, and TypeScript/React.
---

# Security Review

Flag code that could expose user data, allow unauthorized access, or enable injection attacks. Cite `REVIEW-RULE-ID-SEC-*` tokens in findings.

## Scope gates (CHECK BEFORE MATCHING ANY RULE)

A rule only applies when **all three** are true:

1. **The data has an external trust boundary.** Most SEC rules assume one side is untrusted (browser, attacker, cross-tenant request) and the other side is trusted (server, DB, secrets store). Code that only moves data within the SAME trust boundary (e.g. a browser-local Zustand store keying by sessionId) does NOT cross any boundary.
2. **A concrete pattern from this skill matches.** "This field name sounds sensitive" is not a finding. The rule must point at an actual sink (log line, response body, query interpolation, etc.).
3. **The diff actually adds the pattern.** Pre-existing surfaces on unchanged lines are out of scope.

If any gate fails, do NOT flag — return `No findings.` for that rule.

## Common false positives — DO NOT FLAG

- **Client-side TypeScript state types** with `error: string`, `token: string`, etc. as field NAMES. The field name is not the leak — the leak is whether something writes a real secret into the field AND ships it past a trust boundary. Browser-local state is not a leak.
- **`error?: string` on a Zustand store block** is the agent-visible error from a tool call — not a server error message that needs scrubbing. SEC-INTERNAL-FIELD-LEAK targets server JSON responses, not client state.
- **`logger.info("Model call started")`** without a payload value. SEC-LOG-SECRETS targets logs that interpolate sensitive runtime values (tokens, API keys); it does NOT target log lines that mention sensitive WORDS in their static text.
- **Server-side internal types whose fields stay server-side.** Only flag fields that actually appear in responses, traces, or persisted data the user can read.

## Go / API (Gin)

### REVIEW-RULE-ID-SEC-HTTP-TIMEOUT
`http.Client{}` constructed without a `Timeout` field — hangs indefinitely on slow/unresponsive hosts, exhausting goroutines.

### REVIEW-RULE-ID-SEC-UNBOUNDED-BODY
`io.ReadAll(r.Body)` without wrapping in `io.LimitReader()` — user can send an unbounded payload and exhaust memory.

### REVIEW-RULE-ID-SEC-SSRF
User-controlled value (from `c.Param()`, `c.Query()`, `c.PostForm()`, or request body field) used as the URL argument of `http.Get()` or equivalent — Server-Side Request Forgery.

### REVIEW-RULE-ID-SEC-UNPROTECTED-ROUTE
New Gin route registered outside the authenticated middleware group (no JWT check, no auth decorator) — unprotected endpoint.

### REVIEW-RULE-ID-SEC-TENANT-SCOPE
Data access query without `tenantID` / `orgID` / `projectID` scope filter — one tenant can read or write another tenant's data. Check every `.Find()`, `.UpdateOne()`, `.DeleteOne()` for proper scope filters.

### REVIEW-RULE-ID-SEC-NOSQL-INJECTION
MongoDB query document constructed with string interpolation from user input — NoSQL injection. Always use typed filter documents (`bson.M{"field": userInput}`), never string concatenation.

### REVIEW-RULE-ID-SEC-SHELL-INJECTION
`exec.Command("sh", "-c", userInput)` or `os.System(userInput)` in Go / `subprocess.run(cmd, shell=True)` with user input in Python — shell injection.

### REVIEW-RULE-ID-SEC-SECRETS-IN-CODE
API key, password, database connection string, or JWT secret as a literal in source code (not pulled from env var or secrets manager).

### REVIEW-RULE-ID-SEC-LOG-SECRETS
Sensitive field (`password`, `token`, `apiKey`, `secret`, `authorization`) appearing in log output, error message, or trace.

### REVIEW-RULE-ID-SEC-INTERNAL-FIELD-LEAK
Response JSON includes internal fields that should not be public — hashed credentials, other tenants' records, raw error stack traces.

### REVIEW-RULE-ID-SEC-ADMIN-BYPASS
Direct non-admin API call from UI (`agentic-platform-ui/src/services/`) or CLI (`client-libraries/agentic-cli/`) targeting `/api/v1`, `/ecp/`, or a raw ECP base URL — bypasses the Admin API's JWT auth, RBAC middleware, tenant-scoping, and audit surface. All new UI/CLI calls must target `/admin/api/v1`.

## TypeScript / Browser

### REVIEW-RULE-ID-SEC-DANGEROUS-HTML
`dangerouslySetInnerHTML` used with user-supplied (or server-rendered but user-authored) content — XSS.

### REVIEW-RULE-ID-SEC-LOCALSTORAGE-TOKEN
Auth token stored in `localStorage` or `sessionStorage` — reachable by any script via XSS. Use httpOnly cookies or short-lived in-memory tokens.

## Python

### REVIEW-RULE-ID-SEC-SQL-INJECTION
SQL query built with `f"..."`, `%`, or `+` concatenation using user input — use parameterized queries (`cursor.execute("... WHERE id = %s", (user_id,))`) or an ORM.

### REVIEW-RULE-ID-SEC-PICKLE-UNTRUSTED
`pickle.loads()` or `yaml.load()` (without `SafeLoader`) on data from an untrusted source — arbitrary code execution.

## What NOT to flag

- Test fixtures with dummy tokens/credentials that are clearly test-only
- Changes on unchanged lines
- Generic "could be vulnerable" observations with no concrete pattern from this skill

## Output

Every finding MUST cite a specific `REVIEW-RULE-ID-SEC-*` token from this skill.
