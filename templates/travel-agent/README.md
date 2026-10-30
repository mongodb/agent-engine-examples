# Travel Agent Template

A starter travel assistant intended for the future `agentengine create` flow.

This template gives you a compact app that searches flights and accommodation
stored in MongoDB, creates bookings, suspends for human review before a booking
is written, and remembers traveler preferences across conversations.

**Features:** Memory (wired, on by default) · Human-in-the-loop (suspend/resume)

## What This Template Includes

- A runnable SDK app with MongoDB query tools and a booking tool
- Long-term memory for traveler preferences
- `agent.yaml`-driven runtime settings for feature flags
- A MongoDB-backed flights, listings, and bookings store, with sample data in `data/`
  and a loader in `scripts/`
- `agent.yaml` metadata for build and deploy workflows, with local-dev settings
  (service ports) in `dev.yaml`

The LLM client is defined in `src/travel_agent/llm.py` and reads `LLM_API_KEY` from
`.env` or project secrets.

Memory extraction runs in a separate service, configured in `project-config.yaml`.
This template points extraction at `LLM_API_KEY`, so the agent and extraction share
one key.

## Quick Start

```bash
cp env.example .env
```

Before you start the app, edit `.env` and:

- Leave `MONGODB_URI` empty for `agentengine dev`; set it only when running against
  your own MongoDB deployment
- Set `LLM_API_KEY` for the configured client
- Review the LLM builder in `src/travel_agent/llm.py`. To switch providers,
  update the import, constructor, and `MODEL`; the key remains `LLM_API_KEY`.
  Update `extraction_llm` in `project-config.yaml` to match.
- Set `VOYAGE_API_KEY`, because `features.memory: true` in `agent.yaml`

Then start the local stack:

```bash
agentengine dev up
```

Once the local stack is running:

- Load the sample data into the local MongoDB, using the `mongo` URL that
  `agentengine dev up` prints:

  ```bash
  MONGODB_URI="<mongo URL>/?directConnection=true" uv run scripts/seed_data.py
  ```

- Open the Playground UI at `http://localhost:3000` to chat with the agent
- The playground port is configured in `dev.yaml` under
  `services.playground.port`

Stop the local stack with:

```bash
agentengine dev down
```

## Register It On The Platform

When you are happy with the local app and want to connect it to the platform, load
the sample data into your Atlas cluster:

```bash
MONGODB_URI="<your Atlas connection string>" uv run scripts/seed_data.py
```

Then register the workspace and link your cluster:

```bash
agentengine init
agentengine atlas setup
```

`agentengine init` is the point where org/project selection and workspace
registration should happen. Choose the project that maps to the Atlas project
containing your cluster. `agentengine atlas setup` links the cluster and stores
`MONGODB_URI` and `VOYAGE_API_KEY` as project secrets.

Before building, store the remaining secrets and the memory settings. The CLI
prompts without echoing the secret value:

```bash
agentengine secret set LLM_API_KEY
agentengine secret set MONGODB_DATABASE
agentengine memory configure
```

Then build and deploy:

```bash
agentengine build
agentengine deploy
```

Local development reads secrets from `.env`; deployed agents read them from the
platform secret store. This template keeps secrets in `.env`, while runtime
feature flags live in `agent.yaml`; the model lives in the LLM builder.

## Project Layout

```text
travel-agent/
├── agent.yaml
├── dev.yaml
├── env.example
├── project-config.yaml
├── pyproject.toml
├── pyrightconfig.json
├── data/
│   ├── flights.json
│   └── listings.json
├── scripts/
│   └── seed_data.py
└── src/travel_agent/
    ├── __init__.py
    ├── llm.py
    ├── main.py
    ├── system_message.py
    └── tools.py
```
