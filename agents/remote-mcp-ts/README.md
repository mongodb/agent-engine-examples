# Remote MCP Agents (TypeScript)

TypeScript port of [`agents/remote-mcp`](../remote-mcp/) — minimal LangGraph
agents for testing Magenta remote MCP support against hosted Streamable HTTP
MCP servers. The agents do not define custom tools; their tools are
discovered from the `mcp.servers` block in `agent.yaml`.

The default `agent.yaml` runs the **GitHub-only** profile. You only need an LLM
API key and `GITHUB_MCP_TOKEN` — no MCP OAuth setup — so it is the recommended
starting point for basic remote MCP testing. Switch to `configs/agent.all.yaml`
when you want one entry point with GitHub, Sentry, Glean, and Atlas together.

## Quick Start (GitHub)

```bash
cd agents/remote-mcp-ts
cp env.example .env
# Fill the LLM key for config.provider and GITHUB_MCP_TOKEN in .env.
pnpm install
agentic dev up
```

The default profile targets GitHub's hosted remote MCP endpoint:

```text
https://api.githubcopilot.com/mcp/
```

It uses a personal access token through the bearer-token path:

- `auth.type: bearer_env`
- `auth.token_env: GITHUB_MCP_TOKEN`
- `X-MCP-Readonly: "true"`
- `X-MCP-Toolsets: repos,issues,pull_requests,actions`

Set `GITHUB_MCP_TOKEN` in `.env` or export it before starting the dev stack. Use
the least-privileged token that can read the repos, issues, pull requests, and
workflow runs you want to inspect.

## Combined Main Agent (optional)

The combined profile enables GitHub, Sentry, Glean, and Atlas MCP servers at
the same time. Use it when you want one entry point that can decide which MCP
server to call based on the user's request. It requires:

- GitHub bearer-token auth from `GITHUB_MCP_TOKEN`
- Sentry OAuth from `agentic dev mcp auth login sentry`
- Glean OAuth from `agentic dev mcp auth login glean`
- Atlas client-credentials auth from `MCP_GROUP_SA_ID_DEV` and
  `MCP_GROUP_SA_SECRET_DEV`

```bash
cd agents/remote-mcp-ts
cp configs/agent.all.yaml agent.yaml
cp env.example .env
# Fill the LLM key for config.provider and GITHUB_MCP_TOKEN in .env.
# Fill ATLAS_GROUP_ID and cloud-dev GSA credentials for Atlas tools.
set -a
source .env
set +a
scripts/create-atlas-mcp-config.sh create --env-file .env.atlas
agentic dev mcp auth login sentry
agentic dev mcp auth login glean
pnpm install
agentic dev up
```

Switch back to the default GitHub profile with:

```bash
cp configs/agent.github.yaml agent.yaml
```

## Sentry OAuth Profile

Sentry is configured as the OAuth example because its hosted MCP service
supports remote OAuth without a local personal token. Switch to the Sentry
profile, then bootstrap the MCP OAuth cache:

```bash
cd agents/remote-mcp-ts
cp configs/agent.sentry.yaml agent.yaml
agentic dev mcp auth login sentry
```

The command opens Sentry's OAuth flow, lists tools as a smoke check, and writes
the OAuth cache under `~/.agentic/mcp-oauth`. `agentic dev up` mounts that cache
into the runtime container so the TypeScript MCP SDK can refresh access tokens
when needed.

## Glean OAuth Profile

Glean is configured as a second OAuth example using MongoDB's Glean MCP default
server:

```text
https://mongodb-be.glean.com/mcp/default
```

Switch to the Glean profile, then bootstrap the MCP OAuth cache:

```bash
cd agents/remote-mcp-ts
cp configs/agent.glean.yaml agent.yaml
agentic dev mcp auth login glean
```

The command opens Glean's SSO-backed OAuth flow, lists tools as a smoke check,
and writes the OAuth cache under `~/.agentic/mcp-oauth`. The profile requests a
small lowercase OAuth scope set for MCP search, documents, and entities because
Glean scopes are case-sensitive; if you need a different Glean MCP server or
scope set, update `configs/agent.glean.yaml` before running the login command.

## Atlas Client-Credentials Profile

Atlas cloud-dev Remote MCP uses OAuth2 client credentials. `configs/agent.atlas.yaml`
stores only the env-var names:

- `auth.type: client_credentials`
- `auth.token_url: https://cloud-dev.mongodb.com/api/oauth/token`
- `auth.client_id_env: MCP_GROUP_SA_ID_DEV`
- `auth.client_secret_env: MCP_GROUP_SA_SECRET_DEV`

Create a cloud-dev Global Service Account with enough permission to create the
group-level MCP config, then bootstrap the group-level MCP client credentials:

```bash
cd agents/remote-mcp-ts
cp env.example .env
# Fill ATLAS_GROUP_ID, ATLAS_DEV_GSA_CLIENT_ID, and ATLAS_DEV_GSA_CLIENT_SECRET.
set -a
source .env
set +a
scripts/create-atlas-mcp-config.sh create --env-file .env.atlas
cp configs/agent.atlas.yaml agent.yaml
agentic dev up
```

The script performs the token exchange from the demo note, creates
`/api/private/groups/{groupId}/mcpConfig`, and writes the returned
`clientId`, `clientSecret`, and `configId` to `.env.atlas`. That file is ignored
by git through `.env.*`.

Before testing Atlas Remote MCP:

- Enable the org-scoped `MCP_SERVICE_ACCOUNT_ACCESS` feature flag
  (`mms.featureFlag.atlasremotemcp.mcpServiceAccountAccess`) for the
  organization that owns `ATLAS_GROUP_ID`.

By default the script requests:

```json
{
  "secretExpiresAfterHours": 720,
  "refreshIntervalHours": 336,
  "ingressManagedBy": "USER",
  "roles": ["GROUP_OWNER"]
}
```

Override those values with `--secret-expires-after-hours`,
`--refresh-interval-hours`, `--ingress-managed-by`, or `--roles ROLE1,ROLE2`.

Clean up the group-level MCP config when you are done testing:

```bash
set -a
source .env
source .env.atlas
set +a
scripts/create-atlas-mcp-config.sh delete \
  --group-id "$ATLAS_GROUP_ID" \
  --config-id "$ATLAS_MCP_CONFIG_ID_DEV"
```

Switch back to the default GitHub profile with:

```bash
cp configs/agent.github.yaml agent.yaml
```

## LLM Configuration

For full agent runs, set the key that matches `config.provider` in
`agent.yaml` or one of the saved config profiles:

```dotenv
OPENAI_API_KEY=...
# or
ANTHROPIC_API_KEY=...
```

The app reads `config.model` and `config.base_url` from `agent.yaml` first,
then falls back to the provider-specific environment variables in `.env`.

For OpenAI or OpenAI-compatible models through Grove Foundry:

```yaml
config:
  provider: openai
  model: gpt-5.4-mini
  base_url: https://grove-gateway-prod.azure-api.net/grove-foundry-prod/openai/v1
```

```dotenv
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://grove-gateway-prod.azure-api.net/grove-foundry-prod/openai/v1
```

For Anthropic models through Grove Foundry:

```yaml
config:
  provider: anthropic
  model: claude-sonnet-4-6
  base_url: https://grove-gateway-prod.azure-api.net/grove-foundry-prod/anthropic/v1
```

```dotenv
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=https://grove-gateway-prod.azure-api.net/grove-foundry-prod/anthropic/v1
```

When a Grove Foundry base URL is set, the example sends the provider API key as
the `api-key` header. OpenAI URLs are normalized to the `/openai/v1` endpoint;
Anthropic URLs may be configured with the curl-style `/anthropic/v1` endpoint,
and the adapter passes the SDK the `/anthropic` base path so its `/v1/messages`
request lands on the Grove route.

## Run The Example

From this directory:

```bash
pnpm install
agentic dev up
```

From the magenta-examples repo root:

```bash
agentic dev up --workspace remote-mcp-ts
```

For GitHub, ask a repository-shaped task, for example:

> Use GitHub tools to inspect `10gen/agentic-platform` issues related to remote
> MCP support and summarize the most relevant open items.

For Sentry, ask an incident-shaped task, for example:

> Use Sentry tools to list the projects I can access and summarize recent
> unresolved issues in the most active project.

For Glean, ask a company-knowledge-shaped task, for example:

> Use Glean tools to search for recent docs about remote MCP support and
> summarize the most relevant findings with links.

For Atlas, ask an Atlas cloud-dev resource-shaped task, for example:

> Use Atlas tools to list the clusters in my configured project and summarize
> their provider, region, MongoDB version, and current state.

For the combined profile (`configs/agent.all.yaml`), ask a task that can use multiple MCP servers:

> Use Glean to find docs about remote MCP support, inspect the related GitHub
> code or pull requests, check Sentry for recent issues that look relevant, and
> use Atlas to inspect the cloud-dev project resources involved in the test.

## Local SDK development (`agentic-platform`)

Use this when testing unreleased TypeScript SDK changes (for example MCP support
on an `agentic-platform` branch) instead of the git-pinned packages in
`package.json`.

### Prerequisites

- Docker running
- `agentic` CLI on your `PATH`
- An `agentic-platform` checkout with
  `client-libraries/packages/{sdk-core-ts,runner-shared-ts,magenta-sdklanggraph-ts}`
- LLM and MCP credentials in `.env` (the default GitHub-only `agent.yaml` is
  the simplest setup)

### 1. Build the SDK chain

Run once, or again after editing SDK packages:

```bash
cd /path/to/agentic-platform/client-libraries/packages/sdk-core-ts
npm install && npm run build
cd ../runner-shared-ts && npm install && npm run build
cd ../magenta-sdklanggraph-ts && npm install && npm run build
```

### 2. Configure credentials

```bash
cd agents/remote-mcp-ts
cp env.example .env
# Fill OPENAI_API_KEY (or the key for config.provider) and GITHUB_MCP_TOKEN.
pnpm install
```

### 3. Run with `--local-sdk`

```bash
agentic dev up --local-sdk /path/to/agentic-platform/client-libraries
```

If your locally built `agentic` CLI embeds
`runner-base-typescript-langgraph:0.0.0-dev` (that tag is not published to
GHCR), override the runner image tag:

```bash
RUNNER_BASE_TS_LANGGRAPH_REPOSITORY=ghcr.io/10gen/magenta-client-libraries/runner-base-typescript-langgraph \
RUNNER_BASE_TS_LANGGRAPH_TAG=0.1.67-alpha \
agentic dev up --local-sdk /path/to/agentic-platform/client-libraries
```

Use the tag reported by `agentic version` if `0.1.67-alpha` is stale for your
CLI.

### Monorepo Docker note

This repository's root [`.dockerignore`](../../.dockerignore) whitelists
`.agentic/entrypoint.mjs` and `.agentic/dev-entrypoint.mjs` so TypeScript agents
can copy generated entrypoints when `agentic dev` uses the monorepo root as the
Docker build context.

### Isolated mode (`agentic dev up --isolated`)

Use isolated mode to exercise production-like topology (`oe` / `aer` / `tool`
separate containers) and `required_secrets` allowlist enforcement. Code changes
require a rebuild (`agentic dev up --isolated --no-cache`); there is no
hot-reload. Isolated mode does **not** support `--local-sdk`.

For **bearer MCP** servers (GitHub, Atlas client credentials), declare secrets in
both `required_secrets.aer` (AER startup MCP discovery) and `tools."*"` (per-call
tool-pod delivery). `tools.invoke_llm` is reserved for LLM provider keys only.

When the default `runner-base-typescript-langgraph` tag in your CLI release does
not yet include the TypeScript MCP SDK, build a patched local runner-base and
pass `RUNNER_BASE_TS_LANGGRAPH_REPOSITORY` / `RUNNER_BASE_TS_LANGGRAPH_TAG` when
starting the stack. See
[TypeScript agents — isolated mode](https://github.com/10gen/agentic-platform/blob/main/docs/typescript-agents.md)
and the runner-base patch overlay in
[local Kind deploy](https://github.com/10gen/agentic-platform/blob/main/docs/typescript-agents-local-kind-deploy.md).

OAuth MCP profiles (Glean, Sentry) use `agentic dev mcp auth login <server>`;
tokens live in `~/.agentic/mcp-oauth`, which isolated compose mounts into aer/tool.

### Implementation notes

- **Entrypoint-scoped LLM registration:** `buildRemoteMcpAgent()` in `common.ts`
  calls `app.llm()` inside the `@app.entrypoint` builder. Do not register the
  LLM at module import time — the TypeScript SDK rejects import-time
  `app.llm()` calls outside the entrypoint scope.
- **`--local-sdk` type casts:** `common.ts` casts at SDK boundaries (`as never` /
  `as unknown`) because the mounted SDK checkout installs a second
  `@langchain/core` copy alongside the agent's `node_modules`. The proper fix is
  SDK-side (`@langchain/core` dedup under `--local-sdk` and Tool Pod registry
  reset behavior) — tracked with the TS MCP SDK work in
  [AP-1968](https://jira.mongodb.org/browse/AP-1968). This example keeps the
  interim workaround in one file so future TS agents can copy the pattern until
  the SDK ships the fix.
- **Dev container build:** `@types/node` is a runtime `dependency` (not
  `devDependency`) because `agentic dev up` runs `npm install --omit=dev` inside
  the app container before `tsc`.
- **Locally built `agentic` CLI (`0.0.0-dev` tags):** override published runner
  images when MCP OAuth or `agentic dev up` fails to pull GHCR tags:

```bash
RUNNER_BASE_REPOSITORY=ghcr.io/10gen/magenta-client-libraries/runner-base \
RUNNER_BASE_TAG=0.1.67-alpha \
RUNNER_BASE_TS_LANGGRAPH_REPOSITORY=ghcr.io/10gen/magenta-client-libraries/runner-base-typescript-langgraph \
RUNNER_BASE_TS_LANGGRAPH_TAG=0.1.67-alpha \
PLAYGROUND_UI_IMAGE=ghcr.io/10gen/magenta-client-libraries/playground-ui:0.1.67-alpha \
agentic dev up --local-sdk /path/to/agentic-platform/client-libraries
```

Use the tags reported by `agentic version` if `0.1.67-alpha` is stale for your
CLI. MCP requires a runner image whose bundled SDK tarballs include MCP support
(or use `--local-sdk` with a built `agentic-platform` checkout).

## Local (non-`agentic dev`) development

```bash
pnpm install
pnpm run build
pnpm run start          # combined profile (src/remote_mcp_ts/main.ts)
pnpm run start:github
pnpm run start:sentry
pnpm run start:glean
pnpm run start:atlas
```

## Config Profiles

| Config | Use case | Auth |
| --- | --- | --- |
| `agent.yaml` (default) | Same as `agent.github.yaml` — GitHub issue, pull request, and Actions triage | Bearer token from `GITHUB_MCP_TOKEN` |
| `agent.all.yaml` | Combined main agent with GitHub, Sentry, Glean, and Atlas tools | `GITHUB_MCP_TOKEN`, Sentry and Glean OAuth caches, plus Atlas client credentials |
| `agent.atlas.yaml` | Atlas cloud-dev project and cluster investigation | Client credentials from `.env.atlas` |
| `agent.github.yaml` | Saved copy of the default GitHub-only profile | Bearer token from `GITHUB_MCP_TOKEN` |
| `agent.glean.yaml` | Glean enterprise search, document, code, and people lookup | MCP OAuth cache from `agentic dev mcp auth login glean` |
| `agent.sentry.yaml` | Sentry incident, issue, and event triage | MCP OAuth cache from `agentic dev mcp auth login sentry` |

## References

- GitHub MCP server: `https://github.com/github/github-mcp-server`
- GitHub remote MCP server docs:
  `https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md`
- Glean MCP user guide: `https://docs.glean.com/user-guide/mcp/usage`
- Glean MCP setup guide:
  `https://docs.glean.com/administration/platform/mcp/enable-mcp-servers`
- Sentry MCP server: `https://github.com/getsentry/sentry-mcp`
- Sentry hosted MCP endpoint: `https://mcp.sentry.dev`
- Atlas cloud-dev Remote MCP endpoint:
  `https://cloud-dev.mongodb.com/api/private/mcp`
- Python counterpart: [`agents/remote-mcp`](../remote-mcp/)
