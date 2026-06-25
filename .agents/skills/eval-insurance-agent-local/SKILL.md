---
name: eval-insurance-agent-local
description: Evaluate the locally deployed insurance agent directly against local OE endpoints. Uses the TypeScript persona runner and `test_flow.sh` for direct smoke tests. Use when the user wants direct local insurance-agent evals, persona grading, smoke tests, or container-level verification without the browser UI.
---

# Eval: Insurance Agent (Local OE)

Run the insurance agent directly against the local Orchestration Engine exposed by the insurance-agent container.

Supports two modes:

- **Mode A: 10-Persona Local Eval** — Runs the dedicated TypeScript flow runner in `agents/insurance-agent/eval/insurance-agent-flow.ts`, writes a transcript, and grades both the regular `/invoke` path and the live `/invoke/stream` path.
- **Mode B: Quick Smoke Test** — Runs `test_flow.sh` directly against the local OE for a fast direct-endpoint sanity check.

If the user wants to verify the deployed insurance agent through the local full platform UI, Agent Playground, or admin reviews page, use the separate `eval-insurance-agent-local-ui` skill instead.

---

## Step 0: Resolve the Local App URL and Mode

For **Mode A**, the TypeScript runner can auto-discover a healthy local app URL from the live local stack and falls back to `.agentic/dev-state.json`. Use `--base-url` only when you want to force a specific local app URL.

For **Mode B**, resolve `OE_URL` manually from Docker:

```bash
HOST_PORT=$(docker port insurance-agent-app-1 8000 2>/dev/null | cut -d: -f2)
```

If `HOST_PORT` is empty, stop and tell the user:

> The insurance agent container is not running. Start it with:
>
> ```
> cd <agent-workspace> && agentic dev up --local-sdk
> ```

Otherwise:

```bash
OE_URL="http://localhost:${HOST_PORT}"
```

Health check:

```bash
curl -sf "${OE_URL}/health"
```

If the health check fails, stop and tell the user the local app is not healthy. Suggest checking logs with `agentic dev logs`.

Determine mode:

- If the user says **"eval"**, **"personas"**, **"full"**, **"stress"**, or **"grade"**: run **Mode A**.
- If the user says **"smoke"**, **"quick"**, or **"test_flow"**: run **Mode B**.
- If the user says **"both"** or **"all"**: run **Mode B** first, then **Mode A** only if they explicitly want both direct checks.
- Otherwise: ask which direct local mode to run.

---

## Mode A: 10-Persona Local Eval

Run the dedicated TypeScript flow runner from `agents/insurance-agent/eval/insurance-agent-flow.ts`. This is the local-OE counterpart to the script in `agentic-platform`, but it removes API Gateway auth and uses OE-native endpoints directly.

The script:

- Auto-discovers a healthy local app URL from the live stack and falls back to `.agentic/dev-state.json`
- Exercises both `/invoke` and `/invoke/stream` by default
- Uses async invoke plus polling for the non-streaming path, and direct SSE for the streaming path
- Uses `/resume/{id}` plus execution polling for human-review completion
- Writes a markdown transcript to `agents/insurance-agent/eval/transcripts/`

Run it with:

```bash
cd agents/insurance-agent/eval && npx tsx insurance-agent-flow.ts --transport both
```

Useful options:

```bash
# Run only the streaming transport
cd agents/insurance-agent/eval && npx tsx insurance-agent-flow.ts --transport stream

# Run only the async invoke + poll transport
cd agents/insurance-agent/eval && npx tsx insurance-agent-flow.ts --transport invoke

# Run specific personas
cd agents/insurance-agent/eval && npx tsx insurance-agent-flow.ts --transport both --personas 1,3,5

# Sequential is the default for local OE; use 2-3 only if the user asks
cd agents/insurance-agent/eval && npx tsx insurance-agent-flow.ts --transport both --concurrency 2

# Target an explicit local app URL instead of auto-discovery
cd agents/insurance-agent/eval && npx tsx insurance-agent-flow.ts --transport both --base-url http://localhost:32825

# Increase per-step polling for slow local machines or models
cd agents/insurance-agent/eval && npx tsx insurance-agent-flow.ts --transport both --poll-timeout-ms 300000
```

Wait for it to complete. It prints a transcript path like:

```text
agents/insurance-agent/eval/transcripts/2026-04-08T20-00-00-both.md
```

The canonical persona definitions live in `agents/insurance-agent/eval/insurance-agent-flow.ts`. Keep the skill aligned to that file rather than duplicating persona data here.

### Mode A report

```text
## Insurance Agent Local Eval — 10-Persona Results

Discovery: <auto-discovered source or --base-url>
App URL: http://localhost:<port>
Branch: <branch>  Commit: <sha>
Transports: invoke, stream

### invoke
| Step | Description                        | Pass Rate |
|------|------------------------------------|-----------|
| 1    | Asks for personal info             | x/10      |
| 2    | Asks for car/coverage info         | x/10      |
| 3    | Explains coverage type differences | x/10      |
| 4    | Returns personalized quote         | x/10      |
| 5    | Creates policy with POL number     | x/10      |
| 6    | Starts fresh session               | 10/10     |
| 7    | Asks about accident + recognizes   | x/10      |
| 8    | Triggers human review              | x/10      |
| 9    | Resolves after approval            | x/10      |

Overall: xx/90 (xx.x%)

### stream
| Step | Description                        | Pass Rate |
|------|------------------------------------|-----------|
| 1    | Asks for personal info             | x/10      |
| 2    | Asks for car/coverage info         | x/10      |
| 3    | Explains coverage type differences | x/10      |
| 4    | Returns personalized quote         | x/10      |
| 5    | Creates policy with POL number     | x/10      |
| 6    | Starts fresh session               | 10/10     |
| 7    | Asks about accident + recognizes   | x/10      |
| 8    | Triggers human review              | x/10      |
| 9    | Resolves after approval            | x/10      |

Overall: xx/90 (xx.x%)

### Failures
- [stream] Persona 3, Step 7 (James Wilson): <brief description>. Response: "<excerpt>"
```

---

## Mode B: Quick Smoke Test

Run the direct OE smoke test from the insurance-agent workspace:

```bash
BASE_URL="${OE_URL}" SLEEP_TIME=5 bash agents/insurance-agent/test_flow.sh
```

The script covers:

- Customer acquisition
- Returning-customer memory recall
- Low-risk claims
- High-risk claims with `/resume/{id}`

This quick smoke test is still `/invoke`-based. Use **Mode A** when you need direct coverage of both `/invoke` and `/invoke/stream`.

### Mode B report

```text
## Insurance Agent Local Eval — Smoke Test

Container: insurance-agent-app-1
OE URL: http://localhost:<port>
Branch: <branch>  Commit: <sha>

Part 1: Customer Acquisition (Tests 1-7) — PASS/FAIL
Part 2: Claims Processing (Tests 8-10)   — PASS/FAIL
Part 3: Human-in-the-Loop (Tests 11-13)  — PASS/FAIL

Overall: PASS / FAIL
```

For any failure, include the test number and an error excerpt.

---

## Shared Grading Criteria

These criteria apply to **Mode A**. This is a semantic evaluation — responses do not need exact wording, just the right intent and information.

### Step 1: Greeting

- **Assert**: The agent asks for personal information — name, email or address, and phone. **PASS** if the response requests at least 2 of these 3.

### Step 2: Provide Personal Info

- **Assert**: The agent asks for vehicle details (make, model, year), coverage type, and/or driver's age. **PASS** if it requests car information or coverage preference.

### Step 3: Ask About Coverage Types

- **Assert**: The agent explains differences between coverage levels (Basic, Standard, Comprehensive). **PASS** if it describes at least 2 of 3 tiers with meaningful differences.

### Step 4: Provide Vehicle and Coverage Choice

- **Assert**: The agent returns a personalized quote with a price (monthly or annual premium). **PASS** if the response contains a dollar amount.

### Step 5: Create the Policy

- **Assert**: The agent confirms policy creation and provides a policy number (`POL-xxx`). **PASS** if a policy number is present.

### Step 6: New Session (Memory Boundary)

- **Assert**: Always **PASS** — this is a client-side thread switch.

### Step 7: Report an Accident

- **Assert**: The agent asks follow-up questions about the accident and shows awareness of the customer (uses their name or references their policy). **FAIL** if it asks for policy information it should already know.

### Step 8: Provide Claim Details (Triggers Human Review)

- **Assert**: The transcript shows `Human Review: TRIGGERED`, plus `Suspend Reason` / `Suspend Context` when available.

### Step 9: Approve the Human Review

- **Assert**: The transcript shows a successful approval action/result and `Resume Status: completed` with a non-empty final assistant result.

### Grading Rules

- If a step returns an HTTP error or response payload with an `error` field, grade it as **FAIL**.
- If a critical step fails (for example policy creation), later claim steps may cascade-fail; record that explicitly.
- If a direct local step fails before the conversation starts, report the failing OE/check step instead of forcing the conversation rubric.

---

## Common Helpers

Get branch and commit for the report:

```bash
git -C agents/insurance-agent branch --show-current 2>/dev/null || echo "detached"
git -C agents/insurance-agent rev-parse --short HEAD 2>/dev/null || echo "unknown"
```

---

## Troubleshooting

- **Auto-discovery cannot find a healthy app URL**: Start the stack with `agentic dev up --local-sdk`, then check `agentic dev logs`. If needed, pass `--base-url http://localhost:<port>` explicitly.
- **Container not found for Mode B**: Run `docker ps --filter name=insurance-agent` to verify the container name. The naming convention is usually `{project-dir}-app-1`.
- **OE port keeps changing**: This is expected. Re-discover it via `docker port` at the start of each run, or let the TypeScript runner do that automatically.
- **Mode A polling times out**: Re-run with `--concurrency 1 --poll-timeout-ms 300000`.
- **Human review not triggered**: Verify the claim amount is high enough (typically > `$5,000`) and that the description warrants a high risk level.
- **`test_flow.sh` fails on resume**: Increase `SLEEP_TIME`, for example `SLEEP_TIME=10 BASE_URL=... bash agents/insurance-agent/test_flow.sh`.
