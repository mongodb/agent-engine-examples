---
name: atlas-agent-engine-docs
description: Answer Atlas Agent Engine platform questions from current documentation. Use for questions about the agentic CLI, project creation, local development, deployment, Atlas setup, memory, remote MCP, agent-to-agent communication, governance, monitoring, or network egress. Consult the documentation source before answering rather than relying on prior knowledge.
---

# Atlas Agent Engine Documentation

Use this skill to answer platform questions from the current documentation on demand. Do not preload the documentation set or answer platform-specific questions from memory when a documentation source is available.

## Documentation Sources

Use one source for each answer, in this order:

1. **Public preview:** when `https://www.mongodb.com/docs/agentengine` is reachable, use the public site and cite the page URL.
2. **Private preview:** before the public site is available, read an authorized local documentation checkout. If it is not already present, clone the configured private source on demand using the developer's existing Git authentication.

Set `AGENT_ENGINE_DOCS_REPO_URL` to the authorized private documentation repository URL outside the project and skill. Set `AGENT_ENGINE_DOCS_DIR` to the local checkout path; when unset, use `$HOME/.cache/atlas-agent-engine-docs`.

Never use, request, store, print, or commit shared credentials for the password-protected preview docs site. Never add the private repository URL to the project or this skill. The checkout must be obtained through the developer's own authorized access. If the developer cannot access the checkout or configure `AGENT_ENGINE_DOCS_REPO_URL`, say that private-preview documentation is unavailable to them rather than attempting to bypass access controls.

## Retrieval Workflow

1. Identify the platform topic in the request.
2. Choose the public site when it is reachable; otherwise use the local private-preview checkout.
3. If the private-preview checkout does not exist and `AGENT_ENGINE_DOCS_REPO_URL` is configured, clone it into `AGENT_ENGINE_DOCS_DIR` with `git clone "$AGENT_ENGINE_DOCS_REPO_URL" "$AGENT_ENGINE_DOCS_DIR"`. Let Git use the developer's existing credentials; never prompt for or persist credentials yourself.
4. For the local checkout, start with `README.md`, which contains the documentation index, then read only the relevant Markdown pages.
5. Answer from the retrieved page content. Include the page title and either the public URL or the local repository-relative file path.
6. If the documentation does not cover the question, say so. Do not invent platform behavior or fill gaps with stale knowledge.

## Private-Preview Page Map

Use these paths to narrow a private-preview lookup:

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

## Freshness

Before relying on a local checkout for a time-sensitive answer, check its latest commit. The mirror is regenerated daily. If the checkout is stale and has no local changes, update it with:

```bash
git -C "$AGENT_ENGINE_DOCS_DIR" pull --ff-only
```

Do not modify files in the checkout. It is generated from the documentation source and local edits will be overwritten.

## Answer Format

- State the answer first.
- Cite the retrieved documentation page at the end.
- Call out preview-only behavior, prerequisites, or access requirements when the source documents them.
- Keep private-preview content within the developer's authorized environment. Do not copy it into public issues, repositories, or external channels.
