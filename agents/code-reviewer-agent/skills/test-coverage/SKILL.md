---
name: test-coverage
description: Test-quality review — untested non-trivial logic, tests that do not test what they claim, flakiness patterns, assertion quality, missing coverage for new API surfaces. Cites coverage gaps by file/function, not by line count.
---

# Test Coverage

Focus on high-value coverage gaps — not line coverage. Cite `REVIEW-RULE-ID-TC-*` tokens in findings.

## Scope gates (CHECK BEFORE MATCHING ANY RULE)

A rule only applies when **all three** are true:

1. **The diff adds non-trivial logic that lacks coverage,** or **the diff adds tests that have a real flaw.** "Could you add MORE tests" without a specific function the change misses is not a finding.
2. **The assertion under review is genuinely weak.** A "weak assertion" is one whose pass condition is too loose to catch the regression the test is named for — `assertIsNotNone`, `assert True`, mock `.called_once()` with no value check. An assertion that pins the EXACT expected value (`.toBe(true)`, `.toEqual({a: 1})`, `.toBe('exact-string')`) is NOT weak even if simple.
3. **Structural complaints (TC-AAA, TC-ASSERTION-MSG) only fire on long, dense tests.** Short three-line tests don't need section comments or assertion messages. Don't flag concise tests for missing structure.

If any gate fails, do NOT flag — return `No findings.` for that rule.

## Common false positives — DO NOT FLAG

- **`expect(block.closed).toBe(true)`.** This is a specific-value assertion against a boolean — it's exactly what TC-WEAK-ASSERTION is NOT. The rule targets `expect(x).toBeTruthy()` and `expect(x).toBeDefined()` where the actual value is unverified.
- **`expect(state.subagents).toEqual({})`.** Specific-value match against a known-empty shape. Not weak.
- **A short test with three assert lines and a clear name.** TC-AAA is a recommendation for long tests, not every test.
- **Tests that call `expect(...).toBe(...)` without a custom message.** Vitest/Jest produce excellent diff output on failure; a custom message helps for non-obvious assertions but is not required.

## Coverage gaps

### REVIEW-RULE-ID-TC-UNTESTED-LOGIC
New function/method with branching logic, error paths, or business rules that has NO corresponding test. Pure getters, setters, and one-line pass-through wrappers are exempt.

### REVIEW-RULE-ID-TC-HAPPY-PATH-ONLY
Test suite covers only the success case for logic with multiple distinct error paths. Every `if err != nil` branch, every exception handler, every validation failure branch deserves a test.

### REVIEW-RULE-ID-TC-NEW-API-NO-TEST
New REST endpoint, gRPC method, or public SDK function added without at least one integration or handler test covering the normal flow and one error case.

## Test correctness

### REVIEW-RULE-ID-TC-WRONG-TEST
Test whose name describes behavior X but whose assertions test Y. Classic example: `TestCreateUser_ReturnsError_WhenEmailInvalid` that asserts `mock.called_once()` rather than the error value. The test will pass even if the production code doesn't actually return an error.

### REVIEW-RULE-ID-TC-WEAK-ASSERTION
`assert True`, `assertIsNotNone`, or checking only that a mock was called — these pass even when the return value is wrong. Assert the specific expected value.

### REVIEW-RULE-ID-TC-MOCK-OVER-REAL
Test asserts on the mock's `.call_args` / `.call_count` rather than on what the function under test returned. Mock assertions are useful for verifying interactions, but they are NOT a substitute for asserting the function's behavior.

## Flakiness

### REVIEW-RULE-ID-TC-FLAKY-TIME
Assertion on wall-clock time or `time.Now()` without mocking, tolerance window, or freezing via a test clock.

### REVIEW-RULE-ID-TC-FLAKY-ORDER
Test whose outcome depends on the order other tests ran (shared global state, singleton not reset, module-level cache).

### REVIEW-RULE-ID-TC-FLAKY-PORT
Test that hardcodes a TCP port, temp directory, or file path that could collide with parallel runs.

### REVIEW-RULE-ID-TC-FLAKY-GOROUTINE
Goroutine started in a test without waiting (`sync.WaitGroup`, `done` channel) — `time.Sleep()` as a substitute is flaky.

### REVIEW-RULE-ID-TC-FLAKY-NETWORK
Real HTTP call, real database, or real filesystem access in what should be a unit test. Use `httptest`, `testcontainers`, or mocks.

## Structure (recommended, not required)

### REVIEW-RULE-ID-TC-AAA
Test body lacks clear Arrange / Act / Assert sections. Long tests benefit from blank-line separation or comment markers (`# Act`, `# Assert`). Short tests don't need it.

### REVIEW-RULE-ID-TC-ASSERTION-MSG
Non-trivial assertions without a human-readable message. `assert result == expected, f"got {result}, want {expected}"` beats a bare assertion when the test fails.

## What NOT to flag

- Tests for code under a `TODO`/`XXX` marker that is explicitly scoped-deferred
- Missing tests for trivial pass-through methods (e.g. `func (s *Service) X() int { return s.x }`)
- Flake patterns already mitigated (e.g. port in a test uses `:0` for OS-assigned)
- Requests for higher numeric coverage without naming a specific function

## Output

Every finding MUST cite a specific `REVIEW-RULE-ID-TC-*` token from this skill.
