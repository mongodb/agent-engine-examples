---
name: concurrency-leaks
description: Resource leaks and concurrency bugs that cause goroutine leaks, memory leaks, hangs, or data races. Covers Go, Python asyncio, and TypeScript/React hooks. Use for backend and UI reviews.
---

# Resource Leaks & Concurrency

Flag code that can leak goroutines/tasks/memory, race on shared state, or hang on unclosed resources. Cite `REVIEW-RULE-ID-CC-*` tokens in findings.

## Scope gates (CHECK BEFORE MATCHING ANY RULE)

A rule only applies when **all three** are true:

1. **File extension matches the rule's language section.**
   `*-CC-GO-*` requires `.go`. `*-CC-PY-*` requires `.py`. `*-CC-TS-*` requires `.ts` or `.tsx`.
2. **The code construct matches the rule's exact target.**
   React rules (`*-CC-TS-*`) apply ONLY to functions that DIRECTLY call a React hook (`useEffect`, `useState`, `useCallback`, `useMemo`, etc.) or render JSX. Functions that take a `set` parameter and update state, or import from `zustand`/`redux`/`jotai`, are state-store callbacks — NOT React hooks — and the React rules do not apply.
3. **The diff actually adds the pattern.** Pre-existing patterns on unchanged lines are out of scope.

If any gate fails, do NOT flag — return `No findings.` for that rule.

## Common false positives — DO NOT FLAG

- **Zustand store actions** (`agentic-platform-ui/src/store/*.ts` with `create<T>(set => ({...}))` or `set((state) => ...)`). These are NOT React hooks. The CC-TS-STALE-CLOSURE rule does not apply — Zustand mutators close over `set` and `state` by API contract, not over React props/state.
- **Plain TypeScript utility functions** outside `components/` and `pages/`. Same logic — they're not React components or hooks.
- **Object spreads in state mutators** (`{ ...prev, foo: 1 }`). This is the canonical Zustand/Redux update pattern, not a stale closure.
- **`set((state) => state.x ?? defaults)` reading from previous state.** This is the prescribed Zustand way to read prior state during a write — not a stale closure.

## Go

### REVIEW-RULE-ID-CC-GO-GOROUTINE-LEAK
`go func()` with no `WaitGroup`, done channel, or `select { case <-ctx.Done(): ... }` — the goroutine can never exit cleanly on shutdown.

### REVIEW-RULE-ID-CC-GO-CHANNEL-BLOCK
Goroutine that sends/receives on a channel with no matching consumer/producer and no `case <-ctx.Done():` escape — blocks forever.

### REVIEW-RULE-ID-CC-GO-CONTEXT-CANCEL
`context.WithCancel()` or `context.WithTimeout()` called without a matching `defer cancel()` — context leak.

### REVIEW-RULE-ID-CC-GO-TIMER-STOP
`time.NewTicker()` / `time.NewTimer()` without `defer ticker.Stop()` / `defer timer.Stop()` — timer goroutine leaks.

### REVIEW-RULE-ID-CC-GO-BODY-CLOSE
Successful HTTP call with no `defer resp.Body.Close()` — connection leak.

### REVIEW-RULE-ID-CC-GO-CURSOR-CLOSE
MongoDB `collection.Find()` + `cursor.Next()` iteration without `defer cursor.Close(ctx)` — cursor leak on early return.

### REVIEW-RULE-ID-CC-GO-MAP-RACE
Map written inside a goroutine without `sync.RWMutex` or `sync.Map` — data race (detectable by `go test -race`).

### REVIEW-RULE-ID-CC-GO-WAITGROUP
`sync.WaitGroup.Done()` not called on every return path (especially error branches) — `Wait()` hangs forever.

### REVIEW-RULE-ID-CC-GO-RANGE-CHAN
`range ch` over a channel that is never closed — ranging goroutine blocks forever.

### REVIEW-RULE-ID-CC-GO-LOCK-ORDER
Inconsistent mutex lock order across goroutines — potential deadlock.

## Python asyncio

### REVIEW-RULE-ID-CC-PY-BLOCKING-IO
Blocking call (`requests.get`, `urllib.urlopen`, `time.sleep`, large `open().read()`) inside `async def` — blocks the event loop.

### REVIEW-RULE-ID-CC-PY-TASK-GC
`asyncio.create_task(...)` result discarded (not stored, not awaited, not tracked) — task can be garbage-collected and silently canceled.

### REVIEW-RULE-ID-CC-PY-MISSING-AWAIT
Coroutine function called without `await` — produces a coroutine object that is never executed.

### REVIEW-RULE-ID-CC-PY-GATHER-EXC
`asyncio.gather(...)` without `return_exceptions=True` — one task failure cancels all siblings silently.

## TypeScript/React

### REVIEW-RULE-ID-CC-TS-EFFECT-CLEANUP
`useEffect` that registers an event listener, `setInterval`, subscription, or WebSocket without returning a cleanup function — leaks on unmount.

### REVIEW-RULE-ID-CC-TS-FETCH-ABORT
`fetch()` / axios inside `useEffect` without an `AbortController` — can `setState` on an unmounted component.

### REVIEW-RULE-ID-CC-TS-STALE-CLOSURE
`useEffect` / `useCallback` / `useMemo` closes over state or props not listed in its dependency array — stale values at runtime.

## In-memory growth (any language)

### REVIEW-RULE-ID-CC-CACHE-UNBOUNDED
Map/dict/struct field used as a cache with only inserts, no eviction or TTL — unbounded memory growth over the service's lifetime.

## What NOT to flag

- Pre-existing unchanged lines
- Issues covered by Go's `-race` detector running in CI
- Short-lived scripts and tests where leaks are harmless

## Output

Every finding MUST cite a specific `REVIEW-RULE-ID-CC-*` token from this skill.
