# Atlas Agent Engine Docs Skill

This skill lets a coding agent retrieve Atlas Agent Engine documentation on demand without requiring `agentic create`.

## Install In An Existing Agent

Copy this directory into the existing agent project's skill directory:

```bash
mkdir -p /path/to/agent/.agents/skills
cp -R .agents/skills/atlas-agent-engine-docs /path/to/agent/.agents/skills/
```

Agents that discover skills from a different directory can use the same `SKILL.md` from their supported skill location.

## Documentation Access

After the public-preview documentation site is live, the skill reads
`https://www.mongodb.com/docs/agentengine`.

For private-preview documentation before that launch, configure the authorized
repository URL outside the agent project. On the first relevant question, the
skill clones it using the developer's existing Git authentication:

```bash
export AGENT_ENGINE_DOCS_REPO_URL=<authorized-private-docs-repository-url>
export AGENT_ENGINE_DOCS_DIR="$HOME/.cache/atlas-agent-engine-docs"
```

Do not add shared preview-site credentials to the agent project, environment
examples, or this skill. Do not commit the private repository URL. Keep the
local documentation checkout within the authorized environment.
