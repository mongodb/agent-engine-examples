---
name: api-stability
description: Contract-breaking changes and caller-assumption violations. Covers REST/gRPC/proto wire-format changes, exported Go/Python signature changes, and cross-service callers whose assumptions are violated.
---

# Codebase Context & API Stability

Flag changes that break callers — even callers not in the diff. Cite `REVIEW-RULE-ID-API-*` tokens in findings.

## Scope gates (CHECK BEFORE MATCHING ANY RULE)

A rule only applies when **all three** are true:

1. **File extension matches the rule's language section.**
   `*-API-GO-*` → `.go`. `*-API-PY-*` → `.py`. Proto rules → `.proto`. REST rules → handler/router files. There is no `API-TS-*` namespace — TypeScript Zustand actions / hooks / components are not API surfaces under this skill.
2. **The change has consumers OUTSIDE the same module.** Caller-assumption rules apply when callers exist in a different package, service, or process. Internal renames inside `src/store/`, `src/components/`, or a single Go package with no external imports do not break anything outside the diff.
3. **The diff actually changes the contract.** Adding a NEW function, NEW field, NEW endpoint is a strict superset and not a break.

If any gate fails, do NOT flag — return `No findings.` for that rule.

## Common false positives — DO NOT FLAG

- **TypeScript Zustand store actions changing their behavior.** There is no API-TS-* rule. Even when an action's side effects change, those callers all live in the same UI bundle and the diff already updates them or the tests; this is not a cross-service contract break.
- **Internal helper rename inside a single Go file.** API-GO-SIGNATURE targets EXPORTED (Capital-letter) symbols with cross-package callers. A package-private rename has no external caller surface.
- **Adding a NEW exported field/method.** Strict supersets don't break callers. Only field/method REMOVAL or RENAME is breaking under this skill.
- **Changes whose only callers are in the same PR's diff.** If every caller is updated together, no out-of-tree consumer breaks.

## Caller-assumption violations

### REVIEW-RULE-ID-API-CALLER-RETURN
Changed function signature where callers assume a specific return shape (always non-nil, always a certain struct type). Changing from `(T, error)` to `(*T, error)` when callers have `result.Field` access breaks them.

### REVIEW-RULE-ID-API-CALLER-ERROR
Sentinel error renamed, removed, or the error message format changed. Callers using `errors.Is(err, ErrNotFound)` or matching on the string break silently.

### REVIEW-RULE-ID-API-CALLER-SIDE-EFFECT
Function had a documented or conventional side effect (writes DB, emits event, updates cache) that this change removes. Callers relying on the side effect will see stale state.

### REVIEW-RULE-ID-API-CALLER-ORDER
Change makes the function's return order non-deterministic, where callers previously assumed sorted/insertion order.

### REVIEW-RULE-ID-API-CALLER-CONCURRENCY
Function previously safe to call concurrently; change introduces shared mutable state. Or the reverse — a function documented as goroutine-safe no longer is.

## REST API

### REVIEW-RULE-ID-API-REST-REMOVED
Endpoint path removed from the router without a deprecation plan.

### REVIEW-RULE-ID-API-REST-FIELD-REMOVED
Response JSON field removed or renamed. Callers that destructure (`{ foo } = response` in TS, `response.foo` in Go) break.

### REVIEW-RULE-ID-API-REST-STATUS
HTTP status code changed for a given request (e.g. 200 → 201, 400 → 422). Callers branching on specific codes behave differently.

### REVIEW-RULE-ID-API-REST-AUTH
Endpoint's authentication requirement changed — now requires auth when it didn't, or vice versa.

### REVIEW-RULE-ID-API-REST-REQUIRED-FIELD
New required field added to a request body — old callers that don't send it break.

## gRPC / Proto

### REVIEW-RULE-ID-API-PROTO-FIELD-NUMBER
Proto field number reused or removed — ALWAYS breaking in proto3 wire format. Removed fields must be `reserved`.

### REVIEW-RULE-ID-API-PROTO-TYPE
Field type changed (int32 → int64, string → bytes, etc.) — breaking.

### REVIEW-RULE-ID-API-PROTO-ENUM
Enum value removed — breaking for consumers that pattern-match. Enums should be append-only or use `reserved`.

### REVIEW-RULE-ID-API-PROTO-METHOD
Service method removed or renamed — breaking for all gRPC clients.

### REVIEW-RULE-ID-API-PROTO-REQUIRED
New field added and treated as required by the consumer. Proto3 has no "required" keyword, but validation layers often emulate it. Flag if producer code can't yet populate the field.

## Internal Go/Python packages

### REVIEW-RULE-ID-API-GO-SIGNATURE
Exported Go function/method signature change (parameters added, removed, or reordered; return type changed) breaks every caller.

### REVIEW-RULE-ID-API-GO-STRUCT
Exported struct field removed or renamed — breaks code that references it by name or serializes it to JSON.

### REVIEW-RULE-ID-API-GO-INTERFACE
Interface method added — breaks all existing implementations in the codebase that don't implement the new method.

### REVIEW-RULE-ID-API-PY-SIGNATURE
Public Python function signature change (positional param added, keyword renamed, removed) breaks callers.

## Cross-service fan-out

When any of the above is flagged, recommend the reviewer grep the codebase for consumers:
- `agentic-platform-ui/src/services/` — TS callers of REST API
- `runner/` and `client-libraries/` — Python callers of gRPC/REST
- Test harnesses, `test_flow.sh` scripts, canary suites

## What NOT to flag

- Unexported identifiers (Go: lowercase first letter; Python: single-underscore prefix)
- Signature changes whose callers are all in the same PR's diff
- Strict superset changes (adding an optional field, adding a new endpoint, adding an unused enum value)

## Output

Every finding MUST cite a specific `REVIEW-RULE-ID-API-*` token from this skill.
