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

Before the public site launches, the skill reports that public documentation is
not available yet. It does not use private-preview documentation sources or
credentials.
