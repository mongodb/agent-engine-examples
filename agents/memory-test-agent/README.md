# Memory Policy Test Agent

An agent for testing the memory access control policy introduced in AP-2093.

## Tools

| Tool | What it runs |
|---|---|
| `run_all_scenarios` | All SDK surface-area + OE policy scenarios; returns a full report |
| `run_sdk_scenarios` | SDK write/read/context happy-path scenarios only |
| `run_policy_scenarios` | OE access-policy enforcement scenarios only (direct HTTP) |
| `run_context_isolation_scenarios` | AP-184 cross-user private memory isolation check only |

### SDK scenarios (`run_sdk_scenarios`)
Exercises `app.memory.*` end-to-end: semantic, episodic, taxonomic, procedural write/read, and `build_context` (private + org visibility).

### Policy scenarios (`run_policy_scenarios`)
Makes direct HTTP calls with `X-Agentic-Execution-Id` to verify the OE proxy enforces:
- **Rule 0** — no `user_id` + no `visibility` → 400
- **Rule 1** — `visibility=private` + cross-user → 403
- **Rule 2** — cross-user with no visibility → 403
- Happy paths — own write (201), own read (200), cross-user org/shared (200)

### Isolation scenario (`run_context_isolation_scenarios`)
AP-184: writes a private sentinel as user-A, confirms user-B cannot retrieve it via `build_context`, and confirms user-A can.

## Usage

```
agentic dev
# Then in the playground: "run all scenarios"
```
