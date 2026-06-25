# Remote MCP Agents

Minimal LangGraph agents for testing Magenta remote MCP support against hosted
Streamable HTTP MCP servers. The agents do not define custom `@app.tool`
functions; their tools are discovered from the `mcp.servers` block in
`agent.yaml`.

The default `agent.yaml` runs the combined main agent with GitHub, Sentry,
Glean, and Atlas MCP servers enabled at the same time. Use it when you want one
entry point that can decide which MCP server to call based on the user's
request.

## Combined Main Agent

The default combined profile uses:

- GitHub bearer-token auth from `GITHUB_MCP_TOKEN`
- Sentry OAuth from `agentic dev mcp auth login sentry`
- Glean OAuth from `agentic dev mcp auth login glean`
- Atlas client-credentials auth from `MCP_GROUP_SA_ID_DEV` and
  `MCP_GROUP_SA_SECRET_DEV`

Set up the auth material before starting the dev stack:

```bash
cd agents/remote-mcp
cp env.example .env
# Fill the LLM key for config.provider and GITHUB_MCP_TOKEN in .env.
# Fill ATLAS_GROUP_ID and cloud-dev GSA credentials if you want Atlas tools.
set -a
source .env
set +a
scripts/create-atlas-mcp-config.sh create --env-file .env.atlas
agentic dev mcp auth login sentry
agentic dev mcp auth login glean
agentic dev up
```

The combined profile is also saved as `configs/agent.all.yaml` so you can switch
back to it after testing a single-server profile:

```bash
cp configs/agent.all.yaml agent.yaml
```

## GitHub Bearer Token Profile

The GitHub profile targets GitHub's hosted remote MCP endpoint:

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

## Sentry OAuth Profile

Sentry is configured as the OAuth example because its hosted MCP service
supports remote OAuth without a local personal token. Switch to the Sentry
profile, then bootstrap the MCP OAuth cache:

```bash
cd agents/remote-mcp
cp configs/agent.sentry.yaml agent.yaml
agentic dev mcp auth login sentry
```

The command opens Sentry's OAuth flow, lists tools as a smoke check, and writes
the OAuth cache under `~/.agentic/mcp-oauth`. `agentic dev up` mounts that cache
into the runtime container so the Python MCP SDK can refresh access tokens when
needed.

## Glean OAuth Profile

Glean is configured as a second OAuth example using MongoDB's Glean MCP default
server:

```text
https://mongodb-be.glean.com/mcp/default
```

Switch to the Glean profile, then bootstrap the MCP OAuth cache:

```bash
cd agents/remote-mcp
cp configs/agent.glean.yaml agent.yaml
agentic dev mcp auth login glean
```

The command opens Glean's SSO-backed OAuth flow, lists tools as a smoke check,
and writes the OAuth cache under `~/.agentic/mcp-oauth`. The profile requests a
small lowercase OAuth scope set for MCP search, documents, and entities because
Glean scopes are case-sensitive; if you need a different Glean MCP server or
scope set, update `configs/agent.glean.yaml` before running the login command.

## Atlas Client-Credentials Profile

Atlas cloud-dev Remote MCP uses OAuth2 client credentials. The committed
`agent.yaml` stores only the env-var names:

- `auth.type: client_credentials`
- `auth.token_url: https://cloud-dev.mongodb.com/api/oauth/token`
- `auth.client_id_env: MCP_GROUP_SA_ID_DEV`
- `auth.client_secret_env: MCP_GROUP_SA_SECRET_DEV`

Create a cloud-dev Global Service Account with enough permission to create the
group-level MCP config, then bootstrap the group-level MCP client credentials:

```bash
cd agents/remote-mcp
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

Switch back to the GitHub bearer-token profile with:

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
agentic dev up
```

From the magenta-examples repo root:

```bash
agentic dev up --workspace remote-mcp
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

For the default combined profile, ask a task that can use multiple MCP servers:

> Use Glean to find docs about remote MCP support, inspect the related GitHub
> code or pull requests, check Sentry for recent issues that look relevant, and
> use Atlas to inspect the cloud-dev project resources involved in the test.

## Config Profiles

| Config | Use case | Auth |
| --- | --- | --- |
| `agent.all.yaml` | Combined main agent with GitHub, Sentry, Glean, and Atlas tools | `GITHUB_MCP_TOKEN`, Sentry and Glean OAuth caches, plus Atlas client credentials |
| `agent.atlas.yaml` | Atlas cloud-dev project and cluster investigation | Client credentials from `.env.atlas` |
| `agent.github.yaml` | GitHub issue, pull request, and Actions triage | Bearer token from `GITHUB_MCP_TOKEN` |
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
