---
name: error-handling
description: Error handling and reliability bugs — silent failures, continued execution after errors, missing edge-case guards, unhandled rejections. Covers Go, Python, and TypeScript/React patterns.
---

# Error Handling & Reliability

Flag code that swallows errors, continues on failure, or forgets edge cases. Cite `REVIEW-RULE-ID-EH-*` tokens in findings.

## Scope gates (CHECK BEFORE MATCHING ANY RULE)

A rule only applies when **all three** are true:

1. **File extension matches the rule's language section.**
   `*-EH-GO-*` → `.go`. `*-EH-PY-*` → `.py`. `*-EH-TS-*` → `.ts`/`.tsx`.
2. **The construct under review actually has an error path.**
   "Missing error handler" applies to operations that CAN fail at runtime — network calls, parsing, file I/O, async operations. Pure synchronous state assignments and object spreads cannot fail; they have no error path to handle.
3. **The diff actually adds the pattern.** Pre-existing code on unchanged lines is out of scope.

If any gate fails, do NOT flag — return `No findings.` for that rule.

## Common false positives — DO NOT FLAG

- **Zustand `set((state) => ({...}))`.** This is a pure synchronous state update. There is no async work, no I/O, no parsing — nothing to wrap in try/catch. EH-TS-MUTATION-NO-ERROR-HANDLER targets `useMutation` (TanStack Query); Zustand actions are not mutations in that sense.
- **Pure utility functions and reducers** in `src/utils/`, `src/store/`, `src/lib/`. Wrapping these in try/catch would be cargo-cult error handling that hides bugs.
- **State-management actions in store files** (`src/store/*.ts`). EH-TS-NO-ERROR-BOUNDARY applies to React COMPONENTS — store actions are neither components nor in a render path.
- **Object construction / dict literals.** Building a `{a: 1, b: 2}` cannot throw under normal conditions; demanding error handling around it is noise.

## Go

### REVIEW-RULE-ID-EH-GO-DEFER-CLOSE-ERR
`defer f.Close()` / `defer cursor.Close()` / `defer resp.Body.Close()` — errors from deferred calls silently discarded. Use a named return and `closeWithErr` helper, or capture in a sentinel check.

### REVIEW-RULE-ID-EH-GO-LOG-NO-RETURN
`if err != nil { log.Printf(...) }` with no `return` — error logged but execution continues on the error path, often producing downstream nil panics.

### REVIEW-RULE-ID-EH-GO-ERR-SHADOWING
`:=` inside a nested block redefines `err`, masking the outer error. The outer `err` is never checked.

### REVIEW-RULE-ID-EH-GO-PANIC-IN-LIB
`panic(err)` in library or handler code where callers expect errors back. Panics should be reserved for truly unrecoverable states.

### REVIEW-RULE-ID-EH-GO-GOROUTINE-PANIC
Goroutine started with no `defer recover()` — an unhandled panic crashes the entire process.

### REVIEW-RULE-ID-EH-GO-BULK-WRITE
MongoDB bulk write: only the top-level `err` checked; `BulkWriteResult.WriteErrors` / `WriteConcernErrors` not inspected — partial failures silently ignored.

### REVIEW-RULE-ID-EH-GO-ERRORS-NEW
`errors.New("%s: %v", ...)` — `errors.New` does not interpolate; use `fmt.Errorf` instead.

### REVIEW-RULE-ID-EH-GO-NIL-DEREF
Missing nil check before dereferencing a pointer returned from a function that can return nil (especially on error paths).

## Python

### REVIEW-RULE-ID-EH-PY-BARE-EXCEPT
`except Exception: pass` or `except Exception as e: logger.error(e)` with no re-raise or explicit recovery — error swallowed.

### REVIEW-RULE-ID-EH-PY-TASK-NO-CALLBACK
`asyncio.create_task(...)` with no done callback and no `await` — exceptions from the task are silently lost.

## TypeScript/React

### REVIEW-RULE-ID-EH-TS-NO-ERROR-BOUNDARY
Route-level component, data-fetching feature panel, or third-party widget integration NOT wrapped in a React Error Boundary. A render error in an unwrapped subtree crashes the entire app.

### REVIEW-RULE-ID-EH-TS-QUERY-NO-ERROR-UI
`useQuery` call that doesn't check `isError` / `error` and render a visible UI indicator (toast, banner, inline message). Silently showing stale data on fetch failure is a reliability bug.

### REVIEW-RULE-ID-EH-TS-MUTATION-NO-ERROR-HANDLER
`useMutation` with no `onError` handler — user gets no feedback on failure. For transient failures (network, 5xx), also evaluate whether retry is appropriate.

## Logic / edge cases

### REVIEW-RULE-ID-EH-OFF-BY-ONE
Off-by-one in pagination math: `skip = page * size` instead of `skip = (page - 1) * size` when `page` is 1-indexed.

### REVIEW-RULE-ID-EH-MISSING-NIL-GUARD
Missing nil/None/undefined guard before first use of a value returned from a function whose signature admits nil.

### REVIEW-RULE-ID-EH-UNHANDLED-EDGE
Edge case unhandled: empty list, zero value, nil collection, boundary values (max int, empty string, negative numbers).

## What NOT to flag

- Explicit `// nolint:errcheck` or `# type: ignore` with a justifying comment
- Linter-caught issues (errcheck, ruff's E722)
- Intentional catch-and-continue in retry loops where the outer logic handles state

## Output

Every finding MUST cite a specific `REVIEW-RULE-ID-EH-*` token from this skill.
