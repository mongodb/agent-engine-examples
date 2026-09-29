# Atlas Agent Engine Templates

Public starter templates for Atlas Agent Engine. Create a local agent
with the [`agentengine` CLI](https://github.com/mongodb/agent-engine-client-libraries/releases),
choose an LLM connection, and run it with `agentengine dev up`.

## Quick Start

Prerequisites:

- The `agentengine` CLI.
- Docker or another container runtime supported by `agentengine dev up`.
- An API key from the LLM provider you want to use.

```bash
agentengine create --template hello-world-agent --name "My First Agent"
cd my-first-agent/agents/my-first-agent
agentengine dev up
```

`agentengine create` configures the selected client and saves its credential as
`LLM_API_KEY` in `.env`. Review the
generated README before starting the agent to customize its model, optional
memory, or deployment configuration.

## Templates

| Template | Use when |
| --- | --- |
| `hello-world-agent` | You want a minimal Python agent with optional memory. |
| `hello-world-agent-ts` (`templates/hello-world-agent-langgraph-ts/`) | You want a minimal TypeScript agent. |
| `insurance-agent` | You want a realistic Python agent with policies, claims, and human review. |
| `insurance-agent-ts` | You want a TypeScript deep-agent example with subagents and human review. |
| `chatbot-client` | You want a Next.js chat client with a human-review queue. |

## LLM Connections

Choose OpenAI, Anthropic, Google Gemini, OpenRouter, or an Azure
preset for OpenAI Chat Completions or Anthropic Messages. Azure setup asks for
an endpoint, model or deployment name, and API key. The preset supplies the
authentication headers and, for Anthropic Messages, the required version header.

For other integrations, select **I'll configure it myself** and implement the
generated LLM builder before running the agent. Supported presets and standalone
templates read `LLM_API_KEY`; keep credentials in `.env` or the platform secret
store, never in source control.

## Coding Agent Skills

The CLI installs project-local coding-agent skills into both `.agents/skills/`
for Codex and Copilot and `.claude/skills/` for Claude Code:

- **New project:** `agentengine create` installs them as part of scaffolding.
- **Existing agent workspace:** run `agentengine init` in the agent directory. It
  installs the same skills without re-scaffolding, and works for a single agent,
  a monorepo root, or `--workspace-id`.

Both commands are safe to run in sequence: existing skill files are left
untouched, so local edits are preserved and nothing is duplicated. That also
means an already-installed skill is not updated in place.

The Atlas Agent Engine docs skill retrieves and cites
`https://www.mongodb.com/docs/agentengine` on demand. Before that public site
is available, it reports that public documentation is not available yet.

## Deploying An Agent

After local testing, register the generated agent with `agentengine init`. Store
the agent credential with `agentengine secret set LLM_API_KEY` before running
`agentengine build` and `agentengine deploy`. Interactive deploy also offers to upload
the credential from `.env`.

Memory extraction needs its own provider credential and a Voyage key; follow
the generated README's memory setup before enabling it.

## CI

The public `Template Smoke` workflow validates the `hello-world-agent` template
on pull requests and updates to `main`. It checks that the curated repository
has no internal references and compiles the starter source.

## License

Use of the Atlas Agent Engine software referenced by these templates is governed
by the [License Agreement](LICENSE).
