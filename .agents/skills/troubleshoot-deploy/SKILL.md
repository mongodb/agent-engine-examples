---
name: troubleshoot-deploy
description: Use when a deployment is stuck, failed, or succeeded but the agent does not respond. Diagnoses secrets, Atlas connectivity, memory, image, and pod startup problems with the agentengine CLI.
---

# Troubleshoot Failed Deployments

Diagnose deployments that are stuck, failed, or succeeded but not working. Start with the `agentengine` CLI. Use platform operator logs for the shared orchestration engine and memory server when the deploy result does not explain the failure.

---

## Prerequisites

Authenticate before running any diagnostic commands:

```bash
agentengine auth login
```

Confirm you're targeting the correct workspace:

```bash
agentengine status
```

If you see `not logged in` or `not initialized`, run `agentengine auth login` and `agentengine init` first.

---

## Quick Diagnostic Survey

Run these in order — each reveals a different layer of the deployment stack.

### Step 1: Check overall state

```bash
agentengine status --verbose
```

This shows the workspace state, component health, the latest deployment, and its IDs.

### Step 2: Read where the deploy failed

A failed `agentengine deploy` prints the stage, the component, a reason, and the error or last log line. To read the same result later:

```bash
agentengine deploy get
agentengine deploy get --json
```

The JSON output includes a `failure` object with `stage`, `component`, `reason`, and `excerpt`. The other fields list missing secrets and conditions.

### Step 3: Check deployment events

```bash
agentengine deploy logs --severity error
```

Shows error-level events from the most recent deployment timeline.

### Step 4: Check runtime logs by source

```bash
# Agent execution runtime (your code)
agentengine logs --source agent --level error --since 30m

# Tool executor
agentengine logs --source tool --level error --since 30m
```

Interactive queries cover windows up to 6 hours. To export a longer window, up to 24 hours:

```bash
agentengine logs export --service agent --since 24h
```

OE and memory-server logs are platform operator logs. Use the admin UI or
`GET /api/v1/projects/{projectId}/operator-logs` with deploy-viewer access. The deploy failure excerpt usually carries the same error.

### Step 5: If the build failed

```bash
agentengine build list
agentengine build logs <build-id>
```

---

## Decision Tree

```
Deploy failed?
│
├─ Read the failure block first
│  → "failed during: deploy setup (orchestration-engine or memory-server)"
│    means a shared platform dependency failed before your workload started
│  → "failed during: workload startup" means your agent image or its
│    configuration failed to start
│  → "reason:" names a platform diagnostic such as ImagePullFailed,
│    CrashLoopBackOff, OutOfMemory, or CustomerSecretInvalid
│
├─ Failed in under 5 minutes with no failure detail?
│  → Check: agentengine deploy logs --severity error
│  → Check: platform operator logs for orchestration-engine and memory-server
│  → See: "Deploy Fails Fast" below
│
├─ Timed out after ~30 minutes?
│  → A component couldn't reach a healthy state
│  → Check: agentengine deploy logs (look for which component stalled)
│  → See: Common Issues section (secrets, Atlas connectivity, auth)
│
├─ "ImagePullBackOff" in deploy events?
│  → Image no longer available in registry
│  → Fix: run agentengine deploy --build from the agent directory. It does
│    not support --workspace or --all.
│  → If persists after a fresh build → contact support
│
└─ Build failed?
   → agentengine build logs <build-id>
   → Check Dockerfile, dependencies, or syntax errors

Deploy succeeded but workspace doesn't respond?
│
├─ agentengine status shows "degraded"?
│  → agentengine status --verbose (identify unhealthy component)
│  → Check logs for that component
│
├─ "code = Unimplemented" errors when invoking?
│  → A service in the invoke path does not implement the method the gateway
│    called
│  → Check: agentengine logs --source tool for a failing tool
│  → If the error appears immediately after a platform upgrade, contact
│    support. A workspace redeploy does not change the shared engine.
│
└─ Agent crashes after startup?
   → agentengine logs --source agent --level error --since 1h
   → Look for stack traces or connection errors
```

---

## Common Issues

The deploy result names the stage and component and often includes the platform's own fix hint. Start there. The operator log lines below are a fallback for when the excerpt is empty or you need more context.

### 1. Project Secrets Missing or Misconfigured

**How to detect:**

- Deploy fails during `deploy setup (orchestration-engine)` with reason `CustomerSecretInvalid`
- The error excerpt contains `MONGODB_URI is required as a project-scoped secret`
- `agentengine deploy get` lists the missing names under `Declared secrets that are not configured`
- Operator logs, when needed: `service=orchestration-engine`, search `MONGODB_URI is required`; `service=memory-server`, search `VOYAGE_API_KEY`

**What you'll see:**

- OE: `MONGODB_URI is required`
- Memory server: `VOYAGE_API_KEY not set, embeddings will not be available`, which disables memory features

**Why this happens:**

Secrets were set at the *workspace* level instead of the *project* level. The orchestration engine and memory server are shared across all workspaces in a project, so they read only project-level secrets. Deploy also checks that every populated `.env` key exists as a project or workspace secret.

**How to fix:**

1. Set the secrets at project scope, which is the default:
   ```bash
   agentengine secret set MONGODB_URI
   agentengine secret set VOYAGE_API_KEY
   ```
   `MONGODB_URI` is required. `VOYAGE_API_KEY` is required when memory is enabled.
2. Apply a value you already stored: `agentengine secret sync`
3. Or redeploy: `agentengine deploy`

---

### 2. Atlas IP Access List Mismatch

**How to detect:**

- Deploy fails during `deploy setup (orchestration-engine or memory-server)`
- The error excerpt contains `the orchestration engine could not reach MongoDB via MONGODB_URI. Confirm the URI is a project-scoped secret and that the Atlas cluster allows connections from this environment.`
- Operator logs, when needed: `service=memory-server`, search `TLSV1_ALERT_INTERNAL_ERROR` or `ServerSelectionTimeoutError`; `service=orchestration-engine`, search `tls: internal error`

**What you'll see (any of these):**

- `[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error`
- `ServerSelectionTimeoutError: ... error=NetworkTimeout`
- `Failed to initialize MongoDB ... tls: internal error`

**Why this happens:**

Your Atlas project's IP Access List doesn't include the platform's egress IP addresses. Atlas rejects the connection at the TLS layer, making the error look like a TLS issue rather than a network access issue.

**How to fix:**

1. Go to **Atlas → Network Access → IP Access List**
2. Make sure you're editing the correct Atlas project (the one your `MONGODB_URI` points to)
3. Add the platform egress CIDR ranges (contact your platform administrator for the current list)
4. Connections will succeed on the next retry — or redeploy: `agentengine deploy`

**Common mistake:** Adding IPs to the wrong Atlas project. If you have multiple Atlas projects, add the allowlist to the one containing your target cluster.

---

### 3. Atlas Authentication Failure

**How to detect:**

- Deploy fails during `deploy setup (orchestration-engine)` with reason `CustomerSecretInvalid`
- The error excerpt contains `MongoDB was reached via MONGODB_URI but rejected the credentials (authentication failed). Verify the username, password, and auth source in the connection string match an active database user.`
- Operator logs, when needed: `service=memory-server`, search `Authentication failed` or `bad auth`; `service=orchestration-engine`, search `authentication failed`

**What you'll see (either of these):**

- `Authentication failed., full error: {..., 'code': 18, 'codeName': 'AuthenticationFailed'}` (user doesn't exist)
- `bad auth : authentication failed, code 8000, codeName AtlasError` (wrong password)

**Why this happens:**

The username or password in your `MONGODB_URI` is wrong, the database user doesn't exist on the target cluster, or the user doesn't have sufficient permissions.

**How to fix:**

1. **Atlas → Database Access** → confirm the user in your URI exists
2. Ensure the user has at least `readWriteAnyDatabase` (or scoped to your database)
3. URL-encode special characters in the password (`@` → `%40`, `#` → `%23`)
4. Confirm `authSource=admin` is in your URI (standard for Atlas)
5. Update `MONGODB_URI` in **Project → Secrets** if needed
6. Redeploy: `agentengine deploy`

---

### 4. Atlas Search Index Limit Exceeded

**How to detect:**

- Deploy fails during `deploy setup (memory-server)` with `CrashLoopBackOff`
- Operator logs: `service=memory-server`, search `maximum number of FTS indexes`

**What you'll see:**

- `The maximum number of FTS indexes has been reached for this instance size`

**Why this happens:**

The memory server creates ~10 search indexes. Small Atlas tiers have low caps:

| Atlas Tier | Search Index Limit |
|---|---|
| M0 / M2 / M5 (free/shared) | 3 |
| Flex | varies (low) |
| M10 | 25 |
| M20+ | higher |

**How to fix (choose one):**

- **Recommended:** Upgrade your Atlas cluster to M10 or above
- **Alternative:** Drop unused search indexes from the cluster:
  ```js
  // In Atlas Data Explorer or mongosh:
  // Replace the placeholder with the database reported by memory-server startup logs.
  db = db.getSiblingDB("<resolved_memory_database>")
  db.<collection>.aggregate([{ $listSearchIndexes: {} }])
  db.<collection>.dropSearchIndex("<unused-index-name>")
  ```

---

### 5. Legacy Index Name Conflict

**How to detect:**

- Deploy fails during `deploy setup (memory-server)` with `CrashLoopBackOff`
- Operator logs: `service=memory-server`, search `IndexOptionsConflict`

**What you'll see:**

- `Index already exists with a different name: session_counters_session_id_org_id_unique, code 85 IndexOptionsConflict`

**Why this happens:**

An older platform version created an index under an auto-generated name. The current version tries to create the same index under a new explicit name — MongoDB rejects the duplicate.

**How to fix:**

On current tenant software, a redeploy usually auto-clears this — the memory server drops conflicting legacy indexes on boot:

```bash
agentengine deploy
```

If the error persists after redeploying (e.g., on older memory-server builds), manually drop the legacy-named index in Atlas Data Explorer or mongosh:

```js
// Replace the placeholder with the database reported by memory-server startup logs.
db = db.getSiblingDB("<resolved_memory_database>")
db.session_counters.dropIndex("session_counters_session_id_org_id_unique")
```

The memory server will recreate the index correctly on its next startup attempt.

---

### 6. Malformed MONGODB_URI

**How to detect:**

- Deploy fails during `deploy setup (orchestration-engine)` with reason `CustomerSecretInvalid`
- The error excerpt distinguishes a wrong scheme (`MONGODB_URI is not a valid MongoDB connection string`) from an encoding problem (`MONGODB_URI could not be parsed`)
- Operator logs, when needed: `service=memory-server`, search `Invalid URI` or `Invalid MongoDB URI`

**What you'll see:**

- `Invalid URI scheme: URI must begin with 'mongodb://' or 'mongodb+srv://'`

**Why this happens:**

The `MONGODB_URI` value is missing the protocol prefix, has extra whitespace, or uses an incorrect format.

**How to fix:**

1. Go to **Project → Secrets** in the UI
2. Edit `MONGODB_URI` — ensure it starts with `mongodb://` or `mongodb+srv://`
3. Remove any leading/trailing whitespace
4. Correct format: `mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority`
5. Redeploy: `agentengine deploy`

---

### 7. Atlas Storage Quota Exceeded

**How to detect:**

- Deploy fails during `deploy setup (orchestration-engine)`
- The error excerpt contains `over your space quota`, `writes are blocked on your cluster`, or `the Atlas cluster behind MONGODB_URI is over its storage quota, so writes (including required indexes) are blocked`

**Why this happens:**

The cluster is over its storage quota or has crossed its disk-use threshold, so index creation fails.

**How to fix:**

Free storage or move to a higher Atlas tier, then redeploy: `agentengine deploy`

---

### 8. Project Secret Catalog Out of Sync

**How to detect:**

- Deploy fails during `deploy setup (orchestration-engine)`
- The error excerpt contains `a secret in this project's secret catalog was not found in the credential broker`

**Why this happens:**

The project secret catalog lists a secret that the credential broker no longer holds, usually after a workspace secret was removed.

**How to fix:**

```bash
agentengine secret sync
```

If the failure persists, contact support.

---

### 9. Pod Startup Failures

**How to detect:**

The deploy result reports these in `reason:` and the error excerpt. Each names the failing component.

| Reason | What it means | Fix |
|---|---|---|
| `ImagePullFailed` | The image or a pinned tag is missing from the registry. | `agentengine deploy --build` from the agent directory. |
| `ContainerConfigError` | A referenced config or secret key is missing. | Fix the agent configuration or secret, then redeploy. |
| `OutOfMemory` | The container exceeded its memory limit. | Reduce startup memory use. If the component needs more memory, contact support. |
| `CrashLoopBackOff` | The process exits before becoming healthy. | Read the error excerpt. If it is your agent, run `agentengine logs --source agent --level error`. |
| `InitContainerFailed` | A setup container failed. | Read the init container's message in the deploy result. |
| `PodCreationRejected` | The cluster rejected the pod spec. | Contact support. |

If the reason is `ImagePullFailed` after a long idle period, the previous image was cleaned up from the registry. A fresh build replaces it.

---

### 10. Deploy Fails Fast (Dependencies Unhealthy)

**How to detect:**

```bash
# Deploy failed in under 5 minutes
agentengine deploy logs --severity error

# Check underlying services in platform operator logs:
# service=orchestration-engine, level=ERROR
# service=memory-server, level=ERROR
```

**Why this happens:**

Your agent depends on the orchestration engine or memory server at startup. If those services are down (due to Atlas issues, missing secrets, or other project-level problems), your agent exits immediately, repeated crashes follow, and the deploy is marked as failed.

**How to fix:**

1. **Do NOT immediately redeploy** — the same failure will recur
2. Identify and fix the underlying issue (usually one of issues 1–8 above)
3. Verify platform operator logs show no new OE or memory-server errors
4. Then redeploy: `agentengine deploy`

---

## When to Contact Support

Contact support if you see any of these — they indicate platform-side issues you cannot fix:

| Signal | Meaning |
|---|---|
| `ImagePullBackOff` persists after a fresh `agentengine deploy --build` | Platform registry issue |
| Deploy fails repeatedly with no errors in workspace logs | Platform component failure |
| `agentengine status` shows `not_deployed` immediately after deploying | Platform lost deployment state |
| All components healthy but agent unreachable | Platform routing issue |
| `code = Unimplemented` on invoke after a platform upgrade | Service version mismatch in the invoke path |
| `secret catalog was not found in the credential broker` persists after `agentengine secret sync` | Credential broker state is out of sync |

**Gathering info for a support request:**

```bash
agentengine status --json > status.json
agentengine deploy get --json > deployment.json
agentengine deploy logs --json > deploy-events.json
agentengine logs export --service agent --since 24h
agentengine logs export --service tool --since 24h
agentengine deploy list --limit 5 --json > deploy-history.json
```

Include your **workspace ID**, **project ID**, **deployment ID**, these JSON files, and the exported log file.

---

## Command Reference

| Command | What It Shows |
|---|---|
| `agentengine status` | Workspace state: ready, failed, degraded, deploying, not_deployed |
| `agentengine status --verbose` | Component-level health, IDs, build ID |
| `agentengine status --json` | Machine-readable output (for sharing with support) |
| `agentengine deploy logs` | Deployment event timeline (latest deploy) |
| `agentengine deploy logs --severity error` | Error events only |
| `agentengine deploy logs --category <category>` | Filter events by category |
| `agentengine deploy logs -f` | Follow events live during a deploy |
| `agentengine deploy get` | Full deployment detail with conditions and components |
| `agentengine deploy get --json` | Includes the structured `failure` object |
| `agentengine deploy get -f` | Follow deployment until it completes |
| `agentengine deploy list --status failed` | Recent failed deployments |
| `agentengine deploy --build` | Build and deploy one agent; rejects `--workspace` and `--all` |
| `agentengine deploy --timeout 45m` | Change the per-deployment wait (default 30m) |
| `agentengine logs` | Runtime logs (default: last hour, maximum 6h) |
| `agentengine logs --source <sandbox>` | Filter by sandbox: `agent`, `tool` |
| `agentengine logs --level error` | Error-level only |
| `agentengine logs --grep "<text>"` | Search messages (case-insensitive) |
| `agentengine logs --since 30m` | Logs from the last 30 minutes |
| `agentengine logs -f` | Tail logs in real-time |
| `agentengine logs --all --since 6h` | All logs in the 6-hour interactive limit (auto-paginates) |
| `agentengine logs export --service agent --since 24h` | Export up to 24 hours as compressed JSON Lines |
| `agentengine secret set <NAME>` | Set a project-scoped secret (the default) |
| `agentengine secret sync` | Apply stored secrets to running deployments |
| `agentengine build list` | List builds |
| `agentengine build logs <build-id>` | Stream build output |
