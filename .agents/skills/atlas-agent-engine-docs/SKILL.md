---
name: atlas-agent-engine-docs
description: Answer Atlas Agent Engine platform questions from current documentation. Use for questions about the agentic CLI, project creation, local development, deployment, Atlas setup, memory, remote MCP, agent-to-agent communication, governance, monitoring, or network egress. Consult the documentation source before answering rather than relying on prior knowledge.
---

# Atlas Agent Engine Documentation

Use this skill to answer platform questions from the current documentation on demand. Do not preload the documentation set or answer platform-specific questions from memory when a documentation source is available.

## Documentation Sources

Use `https://www.mongodb.com/docs/agentengine` when it is reachable and cite the page URL. Before the site is available, respond only that public Atlas Agent Engine documentation is not available yet.

## Retrieval Workflow

1. Identify the platform topic in the request.
2. Check whether the public documentation site is reachable.
3. If it is unavailable, respond only that public Atlas Agent Engine documentation is not available yet. Do not mention private sources, credentials, preview status, or alternative documentation.
4. Read only the public pages relevant to the question.
5. Answer from the retrieved page content and cite the public page URL.
6. If the documentation does not cover the question, say so. Do not invent platform behavior or fill gaps with stale knowledge.

## Documentation Page Map

Use these paths to narrow a public documentation lookup:

| Topic | Documentation path |
| --- | --- |
| Getting started | `get-started.md` |
| CLI installation and authentication | `build/install-authenticate.md` |
| Project creation | `build/create-project.md` |
| Local development | `build/run-local.md` |
| Deep agents | `build/build-deep-agent.md` |
| Build and deployment | `deploy/` |
| Memory, remote MCP, and agent-to-agent communication | `add-features/` |
| Atlas organizations, projects, and workspaces | `manage/project-org-workspace/` |
| Guardrails and policy engine | `manage/governance/` |
| Monitoring and monorepos | `manage/monitor.md`, `manage/monorepo.md` |
| Network egress | `network-egress/` |
| Agent manifest contract | `reference/agent-contract.md` |

## Answer Format

- State the answer first.
- Cite the retrieved documentation page at the end.
- Call out preview-only behavior, prerequisites, or access requirements when the source documents them.
