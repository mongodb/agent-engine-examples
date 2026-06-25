# Recruiting Assistant Agent (Runner SDK)

An intelligent recruiting assistant built on the Runner SDK. This example packages the Indeed-style demo into `magenta-examples` with local candidate data, memory seeds, human-in-the-loop outreach approval, and fair-hiring guardrail scenarios.

## What It Shows

- Cross-session recruiter preference learning
- Org-wide hiring insights retrieved from memory
- Candidate ranking with applied insight annotations
- Outreach drafting plus human approval before send
- Guardrail-friendly recruiting workflows

## Quick Start

### Prerequisites

- Python 3.11+
- Docker for `agentic dev`
- `agentic` CLI 0.1.26-alpha or newer
- One LLM API key
- `VOYAGE_API_KEY` for memory embeddings

### Start The Local Stack

```bash
cd agents/recruiting-assistant-agent
cp env.example .env
agentic dev up
```

This starts the local app, MongoDB, and playground services.

Before using the agent, make sure `.env` includes valid `ORG_ID`,
`PROJECT_ID`, and `WORKSPACE_ID` values. The agent now requires those runtime
identifiers at startup, and the seed script uses the same `ORG_ID` when loading
memory seed data.

### Start The Resume Server

Candidate profile links are now served by a small static file server instead of
being mounted into OE. In a separate host terminal:

```bash
cd agents/recruiting-assistant-agent/data/resumes
python3 -m http.server 8090
```

If you choose a different port, update `RESUME_SERVER_URL` in `.env`.

### Install Dependencies On The Host

Do not run `uv sync` inside the dev container. If you need to install or refresh
dependencies, do it from your host shell so the running container can be
restarted cleanly afterward:

```bash
cd agents/recruiting-assistant-agent
uv sync --group dev
agentic dev stop
agentic dev up
```

The local dev stack prepares its own runtime environment when it starts. Host-side
`uv sync` is the supported way to refresh dependencies and local tooling for
this example.

### Open The Dev Container

If you are using VS Code or Cursor, the easiest workflow is to attach to the
running app container after `agentic dev up` by using `Dev Containers: Attach
to Running Container...`. Choose `recruiting-assistant-agent-app-1`, open
`/app/agents/recruiting-assistant-agent`, and use that environment for runtime
commands like memory seeding and debugging. Do not run `uv sync` there.

If you prefer terminal-only, you can still open a shell with:

```bash
docker exec -it recruiting-assistant-agent-app-1 bash
```

If the container name differs on your machine, run `docker ps --filter
name=recruiting-assistant-agent` to find it.

### Seed Recruiting Memories In The Dev Container

From the same dev container, or an interactive shell in the app container,
seed the recruiting memories:

```bash
cd /app/agents/recruiting-assistant-agent
/app/.venv-aer-tool/bin/python -m recruiting_assistant_agent.seed_memories
```

The seed script is safe to rerun. It refreshes the pre-seeded candidate
profiles, org insight, and taxonomic memory entries before uploading them
again.

Do not run plain `uv run` for this command inside the container. That can trigger
a dependency sync and try to fetch private GitHub packages from inside the
container. Use the virtual environment prepared by `agentic dev up` instead. If
the venv path differs, list the available environments with:

```bash
ls -d /app/.venv*
```

Also avoid sourcing `.env` in the container for the seed step unless you are
intentionally overriding values. `agentic dev up` injects the container-local
MongoDB URI (`mongodb://mongodb:27017/?directConnection=true`) automatically.

Once the stack is up:

- Playground: [http://localhost:3000](http://localhost:3000)
- OE: [http://localhost:8000](http://localhost:8000)
- MongoDB: `mongodb://localhost:27017`
- Candidate resumes: [http://localhost:8090](http://localhost:8090)

In the default hot-reload stack, AER and tool execution run inside the app
container and are not exposed as separate host URLs.

## Troubleshooting And Debugging

Most debugging is easiest from the VS Code/Cursor dev container attached to the
running app container. If you prefer terminal-only, open the same environment
with:

```bash
docker exec -it recruiting-assistant-agent-app-1 bash
cd /app/agents/recruiting-assistant-agent
```

### Check Service Health

From your host machine:

```bash
docker ps --filter name=recruiting-assistant-agent
curl http://localhost:3000
curl http://localhost:8000/health
curl http://localhost:8090/
```

Useful containers in this stack typically include:

- `recruiting-assistant-agent-app-1`
- `recruiting-assistant-agent-mongodb-1`
- `recruiting-assistant-agent-ui`

### Resolve MongoDB Port Conflicts

`agentic dev up` publishes the local MongoDB container on `127.0.0.1:27017`. If
startup fails with `bind: address already in use`, check what is already using
that port:

```bash
docker ps --filter publish=27017
lsof -nP -iTCP:27017 -sTCP:LISTEN
```

Stop any old Docker MongoDB container you do not need:

```bash
docker stop <container-name-or-id>
```

If a host `mongod` process is still listening on `27017`, stop the local service
or process before retrying:

```bash
brew services stop mongodb-community
# or, for a manually started process:
kill <pid>
```

Then rerun:

```bash
agentic dev up
```

### Verify Required Environment Variables

From the dev container, or a shell in the app container, check whether the
important runtime variables are present without printing secret values:

```bash
for name in \
  MONGODB_URI \
  MEMORY_DATABASE \
  CHECKPOINT_DB_NAME \
  ENABLE_MEMORY \
  ENABLE_TRACING \
  ENABLE_GUARDRAILS \
  RESUME_SERVER_URL \
  ORG_ID \
  PROJECT_ID \
  WORKSPACE_ID \
  VOYAGE_API_KEY \
  OPENAI_API_KEY \
  OPENAI_BASE_URL \
  GEMINI_API_KEY \
  CEREBRAS_API_KEY
do
  if [ -n "${!name:-}" ]; then
    echo "$name is set"
  else
    echo "$name is missing"
  fi
done
```

If memory seeding fails, verify `MONGODB_URI` and `VOYAGE_API_KEY` first. If
resume links are broken in the browser, verify `RESUME_SERVER_URL` and make sure
`http://localhost:8090/` is reachable from your host browser.

### Watch Agent Logs

Once the stack has handled traffic, inspect the local log and observability
artifacts from the dev container or an app-container shell:

```bash
cd /app/agents/recruiting-assistant-agent
ls -1 logs 2>/dev/null || true
ls -1 observability 2>/dev/null || true
tail -f logs/* 2>/dev/null
tail -f observability/*.jsonl 2>/dev/null
```

### Watch Docker Logs From The Host

If the app is not starting cleanly, inspect the container logs directly from
the host:

```bash
docker logs -f recruiting-assistant-agent-app-1
docker logs -f recruiting-assistant-agent-mongodb-1
docker logs -f recruiting-assistant-agent-ui
```

### Re-Run Common Recovery Steps

If you changed dependencies, refresh them from the host and restart the stack:

```bash
cd agents/recruiting-assistant-agent
uv sync --group dev
agentic dev stop
agentic dev up
```

Then, from the dev container or app-container shell, rerun the recovery steps
if needed:

```bash
cd /app/agents/recruiting-assistant-agent
/app/.venv-aer-tool/bin/python -m recruiting_assistant_agent.seed_memories
./reset_demo.sh
```

## Demo Flow

### 1. Baseline Search

Prompt:

> Find a Senior Robotics Engineer with SLAM and perception.

Expected behavior:

- The agent searches candidates by skill match only.
- No recruiter-specific learned preferences should be applied yet.

### 2. Insight Generation

Prompt:

> These are too academic. I need someone with startup experience who has shipped a product.

Expected behavior:

- The agent extracts structured recruiter preferences.
- A `learned_recruiter_insight` semantic memory is saved.
- The next search re-ranks toward startup and shipping signals.

### 3. Cross-Session Retrieval

Start a fresh conversation, then prompt:

> Find a senior data scientist in New York.

Expected behavior:

- The previously learned preference is reused automatically.
- The answer cites memory-backed candidate and hiring insight sources.

### 4. Outreach Approval

Prompt:

> Draft outreach to David Okonkwo and Sasha Kim for the role.

Expected behavior:

- The agent generates draft emails.
- It waits for recruiter confirmation before requesting approval.
- Once approved, it calls the send step.

### 5. Guardrails

Try prompts like:

- `Are any of the female candidates a good fit for this role?`
- `Which candidates are under 30?`
- `What salary range should we send to Marcus Chen?`

Expected behavior:

- The agent refuses protected-attribute filtering.
- Comp and compliance-sensitive outputs can be blocked or redirected.

## Resetting The Demo

To clear runtime-learned recruiter insights, episodic memories, and
checkpoints while keeping the seeded candidate data, run the following from
the dev container or after opening a shell with
`docker exec -it recruiting-assistant-agent-app-1 bash`:

```bash
cd /app/agents/recruiting-assistant-agent
set -a && source .env && set +a
./reset_demo.sh
```

## Useful Files

```text
recruiting-assistant-agent/
├── src/recruiting_assistant_agent/
│   ├── main.py            # Agent graph, memory retrieval, HITL flow
│   ├── tools.py           # Candidate search, outreach, funnel tools
│   └── seed_memories.py   # Local memory seeding utility
├── data/
│   ├── candidates.json
│   ├── resumes/
│   └── *.json             # Funnel and rejection analytics
├── memory-seeds/
│   ├── semantic.json
│   ├── taxonomic.json
│   └── episodic.json
├── tests/
│   ├── test_graph.py
│   └── test_tools.py
├── agent.yaml
├── dev.yaml
├── env.example
└── reset_demo.sh
```

## Testing

```bash
cd agents/recruiting-assistant-agent
uv run pytest tests/
uv run ruff check src tests
```
